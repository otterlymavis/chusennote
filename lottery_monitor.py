#!/usr/bin/env python3
"""Keyword-first Japanese ticket lottery monitor.

The intended flow is:
1. User enters a keyword for an artist/event/musical.
2. The app searches for likely official pages.
3. It extracts general event information and ticket links from official pages.
4. It builds two user-facing blocks: event info and ticket/lottery info.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from html.parser import HTMLParser

USER_AGENT = "otterpia/0.2 (+https://github.com/otterpia; ticket lottery monitor)"
SEARCH_URL = "https://duckduckgo.com/html/"
TIMEOUT_SECONDS = 20

TICKET_DOMAINS = {
    "pia": ("t.pia.jp", "ticket.pia.jp"),
    "eplus": ("eplus.jp",),
    "lawson": ("l-tike.com",),
    "rakuten": ("r-t.jp", "ticket.rakuten.co.jp"),
    "ticketboard": ("ticketboard.jp",),
    "cnplayguide": ("cnplayguide.com",),
}
SOCIAL_OR_NOISY_DOMAINS = (
    "x.com",
    "twitter.com",
    "instagram.com",
    "facebook.com",
    "youtube.com",
    "wikipedia.org",
)
OFFICIAL_HINTS = ("公式", "official", "オフィシャル", "公演", "ライブ", "ミュージカル")
TICKET_LINK_HINTS = (
    "ticket",
    "チケット",
    "券",
    "抽選",
    "先行",
    "受付",
    "申込",
    "発売",
    "pia",
    "eplus",
    "ローソン",
    "lawson",
    "l-tike",
)
ROUND_LABEL_PATTERNS = (
    r"第?\s*([0-9０-９一二三四五六七八九十]+)\s*次\s*(?:抽選)?\s*先行",
    r"([0-9０-９]+)\s*次\s*プレ(?:オーダー|リクエスト)",
    r"(?:オフィシャル|公式|ファンクラブ|FC|ぴあ|e\+|ローソン)?\s*(?:抽選|先行|プレオーダー)",
    r"一般発売",
)
DATE_TOKEN = r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|20\d{2}年\s*\d{1,2}月\s*\d{1,2}日|\d{1,2}[./-]\d{1,2}|\d{1,2}月\s*\d{1,2}日)"
RANGE_RE = re.compile(rf"(?P<start>{DATE_TOKEN})(?:(?!{DATE_TOKEN}).){{0,60}}(?:[〜～~–—]|から)(?:(?!{DATE_TOKEN}).){{0,60}}(?P<end>{DATE_TOKEN})")
DATE_RE = re.compile(DATE_TOKEN)


@dataclasses.dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclasses.dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclasses.dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    links: tuple[Link, ...]


@dataclasses.dataclass(frozen=True)
class EventInfo:
    keyword: str
    official_page: str | None
    title: str | None
    summary: str | None
    event_dates: tuple[str, ...]
    venues: tuple[str, ...]
    ticket_links: tuple[Link, ...]


@dataclasses.dataclass(frozen=True)
class TicketRound:
    source: str
    url: str
    name: str
    lottery_start: str | None = None
    lottery_end: str | None = None
    results_date: str | None = None
    general_sale_date: str | None = None
    payment_deadline: str | None = None
    evidence: str = ""


@dataclasses.dataclass(frozen=True)
class AppBlocks:
    general_info: EventInfo
    ticket_info: tuple[TicketRound, ...]


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def absolute_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def hostname(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def is_ticket_url(url: str) -> bool:
    host = hostname(url)
    return any(any(domain in host for domain in domains) for domains in TICKET_DOMAINS.values())


def source_name_for_url(url: str) -> str:
    host = hostname(url)
    for name, domains in TICKET_DOMAINS.items():
        if any(domain in host for domain in domains):
            return name
    return host or "unknown"


class ExtractedHTML:
    def __init__(self) -> None:
        self.title = ""
        self.og_title = ""
        self.text_parts: list[str] = []
        self.links: list[Link] = []
        self._tag_stack: list[str] = []
        self._current_href: str | None = None
        self._current_label: list[str] = []
        self._title_parts: list[str] = []


class EventHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.data = ExtractedHTML()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        self.data._tag_stack.append(tag)
        if tag == "meta" and attr_map.get("property", "").lower() == "og:title":
            self.data.og_title = clean_text(attr_map.get("content", ""))
        if tag == "a" and attr_map.get("href"):
            self.data._current_href = absolute_url(self.base_url, attr_map["href"])
            self.data._current_label = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.data._title_parts:
            self.data.title = clean_text(" ".join(self.data._title_parts))
            self.data._title_parts = []
        if tag == "a" and self.data._current_href:
            href = self.data._current_href
            label = clean_text(" ".join(self.data._current_label)) or href
            if not href.startswith(("mailto:", "tel:", "javascript:")):
                self.data.links.append(Link(label=label[:120], url=href))
            self.data._current_href = None
            self.data._current_label = []
        if self.data._tag_stack:
            self.data._tag_stack.pop()

    def handle_data(self, value: str) -> None:
        current = self.data._tag_stack[-1] if self.data._tag_stack else ""
        if current in {"script", "style", "noscript"}:
            return
        if current == "title":
            self.data._title_parts.append(value)
        if self.data._current_href:
            self.data._current_label.append(value)
        self.data.text_parts.append(value)


def request_html(url: str, params: dict[str, str] | None = None) -> str:
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
        content_type = response.headers.get_content_charset() or "utf-8"
    return body.decode(content_type, errors="replace")


def parse_page(url: str, html: str) -> Page:
    parser = EventHTMLParser(url)
    parser.feed(html)
    extracted = parser.data
    seen: set[str] = set()
    links: list[Link] = []
    for link in extracted.links:
        if link.url in seen:
            continue
        seen.add(link.url)
        links.append(link)
    return Page(
        url=url,
        title=extracted.og_title or extracted.title,
        text=clean_text(" ".join(extracted.text_parts)),
        links=tuple(links),
    )


def fetch_page(url: str) -> Page:
    return parse_page(url, request_html(url))


def search_web(keyword: str, limit: int = 8) -> list[SearchResult]:
    query = f"{keyword} 公式 チケット 抽選 先行"
    html = request_html(SEARCH_URL, {"q": query})
    page = parse_page(SEARCH_URL, html)
    results: list[SearchResult] = []
    for link in page.links:
        if "duckduckgo.com/l/" not in link.url and "uddg=" not in link.url:
            continue
        parsed = urllib.parse.urlparse(link.url)
        href = urllib.parse.parse_qs(parsed.query).get("uddg", [link.url])[0]
        if not href.startswith("http"):
            continue
        results.append(SearchResult(title=link.label, url=href, snippet=""))
        if len(results) >= limit:
            break
    return results


def official_score(result: SearchResult, keyword: str) -> int:
    host = hostname(result.url)
    text = f"{result.title} {result.snippet} {result.url}".lower()
    score = 0
    if any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS):
        score -= 20
    if is_ticket_url(result.url):
        score -= 5
    if any(hint.lower() in text for hint in OFFICIAL_HINTS):
        score += 10
    for token in keyword.lower().split():
        if token and token in text:
            score += 2
    if "news" in result.url or "live" in result.url or "stage" in result.url:
        score += 3
    return score


def choose_official_results(results: Sequence[SearchResult], keyword: str, limit: int = 3) -> list[SearchResult]:
    return sorted(results, key=lambda r: official_score(r, keyword), reverse=True)[:limit]


def nearby_phrases(text: str, labels: Iterable[str], width: int = 90, limit: int = 4) -> tuple[str, ...]:
    phrases: list[str] = []
    seen: set[str] = set()
    for label in labels:
        for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            start = max(0, match.start() - width // 3)
            end = min(len(text), match.end() + width)
            phrase = clean_text(text[start:end])
            if phrase not in seen:
                phrases.append(phrase)
                seen.add(phrase)
            if len(phrases) >= limit:
                return tuple(phrases)
    return tuple(phrases)


def extract_event_dates(text: str) -> tuple[str, ...]:
    candidates = nearby_phrases(text, ("公演日", "日程", "開催日", "開催日時", "日時"), limit=5)
    if candidates:
        return candidates
    return tuple(match.group(0) for match in DATE_RE.finditer(text[:3000]))[:5]


def extract_venues(text: str) -> tuple[str, ...]:
    return nearby_phrases(text, ("会場", "場所", "劇場", "ホール", "アリーナ"), limit=5)


def extract_ticket_links(page: Page) -> tuple[Link, ...]:
    links: list[Link] = []
    seen: set[str] = set()
    for link in page.links:
        haystack = f"{link.label} {link.url}".lower()
        if is_ticket_url(link.url) or any(hint.lower() in haystack for hint in TICKET_LINK_HINTS):
            if link.url not in seen:
                links.append(link)
                seen.add(link.url)
    return tuple(links)


def portal_search_links(keyword: str) -> tuple[Link, ...]:
    encoded_plus = urllib.parse.quote_plus(keyword)
    encoded_path = urllib.parse.quote(keyword)
    return (
        Link("Pia search", f"https://t.pia.jp/pia/search_all.do?kw={encoded_plus}"),
        Link("eplus search", f"https://eplus.jp/sf/search?block=true&keyword={encoded_plus}"),
        Link("Lawson Ticket search", f"https://l-tike.com/search/?keyword={encoded_path}"),
    )


def build_event_info(keyword: str, official_pages: Sequence[Page]) -> EventInfo:
    official = official_pages[0] if official_pages else None
    ticket_links: list[Link] = []
    seen: set[str] = set()
    for page in official_pages:
        for link in extract_ticket_links(page):
            if link.url not in seen:
                ticket_links.append(link)
                seen.add(link.url)
    if not ticket_links:
        ticket_links.extend(portal_search_links(keyword))

    summary = None
    if official:
        summary_phrases = nearby_phrases(official.text, ("公演", "開催", "チケット"), limit=1)
        summary = summary_phrases[0] if summary_phrases else official.text[:240]

    return EventInfo(
        keyword=keyword,
        official_page=official.url if official else None,
        title=official.title if official and official.title else keyword,
        summary=summary,
        event_dates=extract_event_dates(official.text) if official else (),
        venues=extract_venues(official.text) if official else (),
        ticket_links=tuple(ticket_links),
    )


def infer_year(month: int, day: int, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    candidate = dt.date(today.year, month, day)
    if candidate < today - dt.timedelta(days=180):
        return today.year + 1
    return today.year


def normalize_date(value: str) -> str:
    value = clean_text(value)
    jp = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", value)
    if jp:
        return f"{int(jp.group(1)):04d}-{int(jp.group(2)):02d}-{int(jp.group(3)):02d}"
    western = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", value)
    if western:
        return f"{int(western.group(1)):04d}-{int(western.group(2)):02d}-{int(western.group(3)):02d}"
    jp_short = re.search(r"(\d{1,2})月\s*(\d{1,2})日", value)
    if jp_short:
        month, day = int(jp_short.group(1)), int(jp_short.group(2))
        return f"{infer_year(month, day):04d}-{month:02d}-{day:02d}"
    short = re.search(r"(\d{1,2})[./-](\d{1,2})", value)
    if short:
        month, day = int(short.group(1)), int(short.group(2))
        return f"{infer_year(month, day):04d}-{month:02d}-{day:02d}"
    return value


def context_windows(text: str, patterns: Sequence[str], width: int = 220) -> list[str]:
    windows: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + width)
            window = clean_text(text[start:end])
            if window not in seen:
                windows.append(window)
                seen.add(window)
    return windows


def extract_first_date(text: str, labels: Sequence[str]) -> str | None:
    for label in labels:
        for match in re.finditer(label, text, flags=re.IGNORECASE):
            window = text[match.start() : min(len(text), match.end() + 100)]
            date_match = DATE_RE.search(window)
            if date_match:
                return normalize_date(date_match.group(0))
    return None


def extract_range(text: str) -> tuple[str | None, str | None]:
    match = RANGE_RE.search(text)
    if not match:
        dates = [normalize_date(m.group(0)) for m in DATE_RE.finditer(text)]
        if len(dates) >= 2:
            return dates[0], dates[1]
        if len(dates) == 1:
            return dates[0], None
        return None, None
    return normalize_date(match.group("start")), normalize_date(match.group("end"))


def round_name_from_context(context: str, fallback: str) -> str:
    for pattern in ROUND_LABEL_PATTERNS:
        match = re.search(pattern, context, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(0))
    return fallback


def extract_ticket_rounds(page: Page) -> tuple[TicketRound, ...]:
    contexts = context_windows(page.text, ROUND_LABEL_PATTERNS + ("受付期間", "申込期間", "抽選結果", "当落", "一般発売"))
    rounds: list[TicketRound] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for index, context in enumerate(contexts, start=1):
        start, end = extract_range(context)
        results_date = extract_first_date(context, ("抽選結果", "結果発表", "当落", "当選発表"))
        general_sale_date = extract_first_date(context, ("一般発売", "発売日"))
        payment_deadline = extract_first_date(context, ("入金", "支払", "払込", "決済"))
        if not any((start, end, results_date, general_sale_date, payment_deadline)):
            continue
        name = round_name_from_context(context, f"Lottery round {index}")
        key = (name, start, end)
        if key in seen:
            continue
        seen.add(key)
        rounds.append(
            TicketRound(
                source=source_name_for_url(page.url),
                url=page.url,
                name=name,
                lottery_start=start,
                lottery_end=end,
                results_date=results_date,
                general_sale_date=general_sale_date,
                payment_deadline=payment_deadline,
                evidence=context[:260],
            )
        )
    return tuple(rounds)


def build_blocks(keyword: str, search_results: Sequence[SearchResult] | None = None) -> AppBlocks:
    results = list(search_results) if search_results is not None else search_web(keyword)
    official_pages: list[Page] = []
    for result in choose_official_results(results, keyword):
        try:
            official_pages.append(fetch_page(result.url))
        except (OSError, ValueError):
            continue

    event_info = build_event_info(keyword, official_pages)
    rounds: list[TicketRound] = []
    for link in event_info.ticket_links:
        try:
            rounds.extend(extract_ticket_rounds(fetch_page(link.url)))
        except (OSError, ValueError):
            rounds.append(TicketRound(source=source_name_for_url(link.url), url=link.url, name="Fetch failed", evidence=link.label))
    return AppBlocks(general_info=event_info, ticket_info=tuple(rounds))


def render_blocks(blocks: AppBlocks) -> str:
    info = blocks.general_info
    lines = ["# General event info", ""]
    lines.append(f"- Keyword: {info.keyword}")
    lines.append(f"- Title: {info.title or 'Unknown'}")
    lines.append(f"- Official page: {info.official_page or 'Not found'}")
    if info.summary:
        lines.append(f"- Summary: {info.summary}")
    if info.event_dates:
        lines.append("- Event date clues:")
        lines.extend(f"  - {date}" for date in info.event_dates)
    if info.venues:
        lines.append("- Venue clues:")
        lines.extend(f"  - {venue}" for venue in info.venues)

    lines.extend(["", "# Ticket / lottery info", ""])
    if info.ticket_links:
        lines.append("- Ticket links found:")
        lines.extend(f"  - {link.label}: {link.url}" for link in info.ticket_links)
    else:
        lines.append("- Ticket links found: none")

    if blocks.ticket_info:
        lines.append("- Lottery / sales rounds:")
        for ticket in blocks.ticket_info:
            lines.append(f"  - {ticket.name} ({ticket.source})")
            lines.append(f"    - Link: {ticket.url}")
            lines.append(f"    - Lottery start: {ticket.lottery_start or 'Unknown'}")
            lines.append(f"    - Lottery end: {ticket.lottery_end or 'Unknown'}")
            lines.append(f"    - Results date: {ticket.results_date or 'Unknown'}")
            lines.append(f"    - General sale: {ticket.general_sale_date or 'Unknown'}")
            lines.append(f"    - Payment deadline: {ticket.payment_deadline or 'Unknown'}")
            if ticket.evidence:
                lines.append(f"    - Evidence: {ticket.evidence}")
    else:
        lines.append("- Lottery / sales rounds: none detected yet")
    return "\n".join(lines)


def blocks_to_json(blocks: AppBlocks) -> str:
    return json.dumps(dataclasses.asdict(blocks), ensure_ascii=False, indent=2)


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS watched_keywords (
            id INTEGER PRIMARY KEY,
            keyword TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            watch_id INTEGER NOT NULL,
            canonical_title TEXT NOT NULL,
            official_url TEXT,
            summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(watch_id, official_url),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );

        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(event_id, url),
            FOREIGN KEY(event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS ticket_rounds (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            round_key TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            name TEXT NOT NULL,
            lottery_start TEXT,
            lottery_end TEXT,
            results_date TEXT,
            general_sale_date TEXT,
            payment_deadline TEXT,
            evidence TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(event_id, round_key),
            FOREIGN KEY(event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            snapshot_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_id, snapshot_hash),
            FOREIGN KEY(event_id) REFERENCES events(id)
        );
        """
    )


def upsert_keyword(connection: sqlite3.Connection, keyword: str, now: str) -> int:
    connection.execute(
        """
        INSERT INTO watched_keywords(keyword, created_at, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(keyword) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (keyword, now, now),
    )
    row = connection.execute("SELECT id FROM watched_keywords WHERE keyword = ?", (keyword,)).fetchone()
    return int(row[0])


def upsert_event(connection: sqlite3.Connection, watch_id: int, info: EventInfo, now: str) -> tuple[int, bool]:
    official_url = info.official_page or f"keyword:{info.keyword}"
    existing = connection.execute(
        "SELECT id FROM events WHERE watch_id = ? AND official_url = ?",
        (watch_id, official_url),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO events(watch_id, canonical_title, official_url, summary, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(watch_id, official_url) DO UPDATE SET
            canonical_title = excluded.canonical_title,
            summary = excluded.summary,
            updated_at = excluded.updated_at
        """,
        (watch_id, info.title or info.keyword, official_url, info.summary, now, now),
    )
    row = connection.execute(
        "SELECT id FROM events WHERE watch_id = ? AND official_url = ?",
        (watch_id, official_url),
    ).fetchone()
    return int(row[0]), existing is None


def source_confidence(link: Link) -> int:
    if is_ticket_url(link.url):
        return 90
    if any(hint.lower() in f"{link.label} {link.url}".lower() for hint in TICKET_LINK_HINTS):
        return 60
    return 40


def upsert_sources(connection: sqlite3.Connection, event_id: int, links: Sequence[Link], now: str) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for link in links:
        existing = connection.execute(
            "SELECT id FROM sources WHERE event_id = ? AND url = ?",
            (event_id, link.url),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, url) DO UPDATE SET
                label = excluded.label,
                platform = excluded.platform,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (event_id, link.url, link.label, source_name_for_url(link.url), source_confidence(link), now, now),
        )
        if existing is None:
            alerts.append({"type": "new_ticket_link", "label": link.label, "url": link.url})
    return alerts


def ticket_round_key(ticket: TicketRound) -> str:
    return stable_hash("|".join((ticket.source, ticket.url, ticket.name)))


def ticket_round_fields(ticket: TicketRound) -> dict[str, str | None]:
    return {
        "lottery_start": ticket.lottery_start,
        "lottery_end": ticket.lottery_end,
        "results_date": ticket.results_date,
        "general_sale_date": ticket.general_sale_date,
        "payment_deadline": ticket.payment_deadline,
    }


def upsert_ticket_rounds(
    connection: sqlite3.Connection,
    event_id: int,
    event_title: str,
    rounds: Sequence[TicketRound],
    now: str,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for ticket in rounds:
        round_key = ticket_round_key(ticket)
        previous = connection.execute(
            """
            SELECT lottery_start, lottery_end, results_date, general_sale_date, payment_deadline
            FROM ticket_rounds
            WHERE event_id = ? AND round_key = ?
            """,
            (event_id, round_key),
        ).fetchone()
        fields = ticket_round_fields(ticket)
        connection.execute(
            """
            INSERT INTO ticket_rounds(
                event_id, round_key, source, url, name, lottery_start, lottery_end,
                results_date, general_sale_date, payment_deadline, evidence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, round_key) DO UPDATE SET
                source = excluded.source,
                url = excluded.url,
                name = excluded.name,
                lottery_start = excluded.lottery_start,
                lottery_end = excluded.lottery_end,
                results_date = excluded.results_date,
                general_sale_date = excluded.general_sale_date,
                payment_deadline = excluded.payment_deadline,
                evidence = excluded.evidence,
                updated_at = excluded.updated_at
            """,
            (
                event_id,
                round_key,
                ticket.source,
                ticket.url,
                ticket.name,
                ticket.lottery_start,
                ticket.lottery_end,
                ticket.results_date,
                ticket.general_sale_date,
                ticket.payment_deadline,
                ticket.evidence,
                now,
                now,
            ),
        )
        if previous is None:
            alerts.append({"type": "new_lottery_round", "event": event_title, "round": ticket.name, "url": ticket.url})
            continue
        previous_fields = dict(zip(fields.keys(), previous, strict=True))
        for field, value in fields.items():
            old_value = previous_fields[field]
            if old_value != value:
                alerts.append(
                    {
                        "type": "ticket_field_changed",
                        "event": event_title,
                        "round": ticket.name,
                        "field": field,
                        "old": old_value or "",
                        "new": value or "",
                        "url": ticket.url,
                    }
                )
    return alerts


def save_snapshot(connection: sqlite3.Connection, event_id: int, blocks: AppBlocks, now: str) -> None:
    payload = blocks_to_json(blocks)
    connection.execute(
        """
        INSERT OR IGNORE INTO snapshots(event_id, snapshot_hash, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (event_id, stable_hash(payload), payload, now),
    )


def save_blocks(db_path: str, blocks: AppBlocks, now: str | None = None) -> list[dict[str, str]]:
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        info = blocks.general_info
        watch_id = upsert_keyword(connection, info.keyword, timestamp)
        event_id, new_event = upsert_event(connection, watch_id, info, timestamp)
        event_title = info.title or info.keyword
        alerts: list[dict[str, str]] = []
        if new_event and info.official_page:
            alerts.append({"type": "new_official_page", "event": event_title, "url": info.official_page})
        alerts.extend(upsert_sources(connection, event_id, info.ticket_links, timestamp))
        alerts.extend(upsert_ticket_rounds(connection, event_id, event_title, blocks.ticket_info, timestamp))
        save_snapshot(connection, event_id, blocks, timestamp)
        return alerts


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Japanese event ticket lotteries by keyword.")
    parser.add_argument("keyword", help="Artist, event, or musical keyword to search for")
    parser.add_argument("--json", action="store_true", help="Output the two app blocks as JSON")
    parser.add_argument("--db", help="SQLite database path for saving watch/event/ticket history")
    parser.add_argument("--alerts-json", action="store_true", help="With --db, output only detected alert changes as JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    blocks = build_blocks(args.keyword)
    alerts: list[dict[str, str]] = []
    if args.db:
        alerts = save_blocks(args.db, blocks)
    if args.alerts_json:
        print(json.dumps(alerts, ensure_ascii=False, indent=2))
    else:
        print(blocks_to_json(blocks) if args.json else render_blocks(blocks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
