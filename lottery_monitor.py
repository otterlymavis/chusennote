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
import base64
import hashlib
import html
import http.server
import json
import os
import pathlib
import re
import shlex
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from html.parser import HTMLParser


def configure_cli_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


USER_AGENT = "chusennote/0.2 (+https://github.com/otterlymavis/chusennote; ticket lottery monitor)"
SEARCH_URL = "https://duckduckgo.com/html/"
BING_SEARCH_URL = "https://www.bing.com/search"
# Optional managed search backend. HTML scraping of DuckDuckGo/Bing gets
# bot-throttled and returns irrelevant results, so a real search API is the
# reliable way to locate official pages. Set both env vars to enable it.
SEARCH_PROVIDER_ENV = "CHUSENNOTE_SEARCH_PROVIDER"  # brave | bing | serpapi
SEARCH_API_KEY_ENV = "CHUSENNOTE_SEARCH_API_KEY"
TIMEOUT_SECONDS = 20
DEFAULT_DB_PATH = "chusennote.sqlite3"
DEFAULT_SESSION_LOG_DIR = "history_logs"
DB_SCHEMA_VERSION = 4
MIN_KEYWORD_OVERLAP = 0.45
WATCH_KIND_ARTIST = "artist"
WATCH_KIND_EVENT = "event"
WATCH_KINDS = (WATCH_KIND_ARTIST, WATCH_KIND_EVENT)
UPCOMING_STATUS_ORDER = {
    "closing_soon": 0,
    "results_today": 1,
    "payment_due": 2,
    "general_sale_soon": 3,
    "open": 4,
    "upcoming": 5,
    "unknown": 6,
    "closed": 7,
}
DEFAULT_ALERT_PREFERENCES = ",".join(
    (
        "new_official_page",
        "new_ticket_link",
        "new_lottery_round",
        "ticket_field_changed",
        "lottery_opened",
        "lottery_closing_soon",
        "results_today",
        "payment_due_soon",
        "general_sale_soon",
        "watch_failed",
    )
)

TICKET_DOMAINS = {
    "pia": ("t.pia.jp", "ticket.pia.jp"),
    "eplus": ("eplus.jp",),
    "lawson": ("l-tike.com",),
    "rakuten": ("r-t.jp", "ticket.rakuten.co.jp"),
    "ticketboard": ("ticketboard.jp", "tickebo.jp"),
    "cnplayguide": ("cnplayguide.com",),
}
SOCIAL_OR_NOISY_DOMAINS = (
    "x.com",
    "twitter.com",
    "instagram.com",
    "facebook.com",
    "line.me",
    "youtube.com",
    "pornhub.com",
    "xvideos.com",
    "xnxx.com",
    "wikipedia.org",
)
OFFICIAL_HINTS = ("公式", "official", "オフィシャル", "公演", "ライブ", "ミュージカル")
# Hosts that frequently carry official Japanese stage/live information. Used as a
# gentle ranking boost, never as a hard filter.
OFFICIAL_HOST_HINTS = (
    "tohostage.com",
    "umegei.com",
    "horipro-stage.jp",
    "horipro.co.jp",
    "stage.co.jp",
    "marv.jp",
    "tbs.co.jp",
    "parco-play.com",
)
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
    "rakuten",
    "ticketboard",
    "ticket board",
    "cnplayguide",
    "cnプレイガイド",
)
ROUND_CONTEXT_HINTS = (
    "受付期間",
    "申込期間",
    "申込み期間",
    "申込受付期間",
    "抽選申込期間",
    "抽選受付期間",
    "当落",
    "当選発表",
    "抽選結果",
    "結果発表",
    "入金",
    "支払",
    "支払い",
    "支払期限",
    "入金締切",
    "一般発売",
    "発売日",
    "発売開始",
)
ROUND_LABEL_PATTERNS = (
    r"第?\s*([0-9０-９一二三四五六七八九十]+)\s*次\s*(?:抽選)?\s*先行",
    r"([0-9０-９]+)\s*次\s*プレ(?:オーダー|リクエスト)",
    r"(?:オフィシャル|公式|ファンクラブ|FC|ぴあ|e\+|ローソン)?\s*(?:抽選|先行|プレオーダー)",
    r"一般発売",
)
DATE_TOKEN = r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|20\d{2}年\s*\d{1,2}月\s*\d{1,2}日|\d{1,2}[./-]\d{1,2}|\d{1,2}月\s*\d{1,2}日)"
RANGE_RE = re.compile(rf"(?P<start>{DATE_TOKEN})(?:(?!{DATE_TOKEN}).){{0,60}}(?:[〜～~–—]|から)(?:(?!{DATE_TOKEN}).){{0,60}}(?P<end>{DATE_TOKEN})")
ROUND_LABEL_PATTERNS = ROUND_LABEL_PATTERNS + (
    r"(?:CN|Rakuten|Ticket\s*Board|楽天|チケットボード|CNプレイガイド)?\s*(?:抽選|先行|プレオーダー|プレリクエスト|先着先行)",
    r"先行先着販売",
    r"一般発売",
)
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
    round_number: int | None = None
    platform: str | None = None
    application_start_at: str | None = None
    application_end_at: str | None = None
    payment_start_at: str | None = None
    payment_end_at: str | None = None
    trade_start_at: str | None = None
    trade_end_at: str | None = None
    confidence: int = 50
    status: str = "unknown"
    round_type: str = "unknown"
    membership_required: str = "unknown"


@dataclasses.dataclass(frozen=True)
class AppBlocks:
    general_info: EventInfo
    ticket_info: tuple[TicketRound, ...]


@dataclasses.dataclass(frozen=True)
class Watch:
    id: int
    keyword: str
    kind: str = WATCH_KIND_ARTIST
    tags: str = ""
    preferred_regions: str = ""
    preferred_venues: str = ""
    alert_preferences: str = DEFAULT_ALERT_PREFERENCES
    muted: bool = False
    last_checked_at: str | None = None


@dataclasses.dataclass(frozen=True)
class WatchSource:
    id: int
    watch_id: int
    url: str
    label: str
    platform: str
    confidence: int
    private_note: bool = False
    muted: bool = False


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def split_session_log_args(argv: Sequence[str]) -> tuple[list[str], bool, str]:
    cleaned: list[str] = []
    enabled = False
    log_dir = DEFAULT_SESSION_LOG_DIR
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--session-log":
            enabled = True
        elif item == "--session-log-dir":
            index += 1
            if index >= len(argv):
                raise SystemExit("--session-log-dir requires a value")
            log_dir = argv[index]
        elif item.startswith("--session-log-dir="):
            log_dir = item.split("=", 1)[1]
        else:
            cleaned.append(item)
        index += 1
    return cleaned, enabled, log_dir


def format_shell_args(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv)


def session_log_path(log_dir: str = DEFAULT_SESSION_LOG_DIR, now: dt.datetime | None = None) -> pathlib.Path:
    timestamp = now or dt.datetime.now().astimezone()
    return pathlib.Path(log_dir) / f"session_{timestamp:%Y_%m_%d}.md"


def append_session_log(
    argv: Sequence[str],
    args: argparse.Namespace,
    exit_code: int,
    started_at: dt.datetime,
    ended_at: dt.datetime | None = None,
) -> pathlib.Path:
    ended_at = ended_at or dt.datetime.now().astimezone()
    path = session_log_path(args.session_log_dir, ended_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = format_shell_args(("lottery_monitor.py", *argv))
    db_path = getattr(args, "db", None)
    target = getattr(args, "command", "unknown")
    details = [
        f"## {started_at.isoformat(timespec='seconds')}",
        "",
        f"- Command: `{command}`",
        f"- Target: `{target}`",
        f"- Exit code: `{exit_code}`",
        f"- Duration seconds: `{(ended_at - started_at).total_seconds():.3f}`",
    ]
    if db_path:
        details.append(f"- Database: `{db_path}`")
    details.append("")
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write("\n".join(details))
    return path


def absolute_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def hostname(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def is_noisy_url(url: object) -> bool:
    host = hostname(str(url or ""))
    return any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS)


def is_ticket_url(url: str) -> bool:
    host = hostname(url)
    return any(any(domain in host for domain in domains) for domains in TICKET_DOMAINS.values())


def is_portal_search_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    return (
        (host == "t.pia.jp" and path.endswith("/search_all.do"))
        or (host == "eplus.jp" and path.startswith("/sf/search"))
        or (host == "l-tike.com" and path.startswith("/search/"))
    )


def is_shiki_stage_schedule_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host == "shiki.jp" and parsed.path == "/stage_schedule/"


def is_generic_ticket_info_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return host == "shiki.jp" and (
        path in {"/tickets", "/tickets_guide", "/door", "/special/info/warning"}
        or path.startswith("/tickets_guide/")
    )


def source_name_for_url(url: str) -> str:
    host = hostname(url)
    for name, domains in TICKET_DOMAINS.items():
        if any(domain in host for domain in domains):
            return name
    return host or "unknown"


def source_provenance(url: str, label: str = "") -> str:
    platform = source_name_for_url(url)
    haystack = f"{label} {url}".lower()
    if platform in TICKET_DOMAINS:
        return "ticket_primary"
    if any(hint.lower() in haystack for hint in OFFICIAL_HINTS):
        return "official"
    if platform == "unknown":
        return "low_confidence"
    return "manual_public"


def is_web_url(url: object) -> bool:
    parsed = urllib.parse.urlparse(str(url or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def platform_confidence(platform: str) -> int:
    if platform in TICKET_DOMAINS:
        return 90
    if platform in {"official", "manual"}:
        return 70
    return 50


def infer_round_type(name: str) -> str:
    text = name.lower()
    if "fc" in text or "ファンクラブ" in text:
        return "fc"
    if "一般発売" in name:
        return "general"
    if "トレード" in name or "リセール" in name:
        return "trade"
    if "公式" in name or "オフィシャル" in name:
        return "official"
    if any(token in text for token in ("pia", "ぴあ", "e+", "ローソン", "lawson")):
        return "platform"
    if any(token in name for token in ("抽選", "先行", "プレオーダー")):
        return "platform"
    return "unknown"


def infer_membership_required(name: str, evidence: str = "") -> str:
    text = f"{name} {evidence}".lower()
    if "fc" in text or "ファンクラブ" in text or "会員" in text:
        return "yes"
    if "一般発売" in name:
        return "no"
    return "unknown"


def normalize_round_name(name: str) -> str:
    return clean_text(name).lower()


JP_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def parse_round_number(value: str) -> int | None:
    match = re.search(r"([0-9０-９一二三四五六七八九十]+)\s*次", value)
    if not match:
        return None
    raw = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw.startswith("十") and len(raw) == 2:
        return 10 + JP_NUMERALS.get(raw[1], 0)
    if raw.endswith("十") and len(raw) == 2:
        return JP_NUMERALS.get(raw[0], 0) * 10
    if "十" in raw and len(raw) == 3:
        return JP_NUMERALS.get(raw[0], 0) * 10 + JP_NUMERALS.get(raw[2], 0)
    return JP_NUMERALS.get(raw)


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


def request_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset, errors="replace"))


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


def search_query(keyword: str) -> str:
    return f"{keyword} 公式 チケット 抽選 先行"


def warn_search_backend(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def search_api(keyword: str, limit: int = 8) -> list[SearchResult]:
    """Query a managed search API when configured via env vars.

    Returns ``[]`` when no provider/key is set or the call fails, so callers can
    fall back to HTML scraping. Supported providers: brave, bing, serpapi.
    """
    provider = os.environ.get(SEARCH_PROVIDER_ENV, "").strip().lower()
    api_key = os.environ.get(SEARCH_API_KEY_ENV, "").strip()
    if not provider or not api_key:
        return []
    query = search_query(keyword)
    try:
        if provider == "brave":
            url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
                {"q": query, "count": limit, "search_lang": "jp", "country": "jp"}
            )
            data = request_json(url, {"X-Subscription-Token": api_key})
            rows = (data.get("web") or {}).get("results", []) if isinstance(data, dict) else []
            return parse_api_results(rows, "title", "url", "description", limit)
        if provider == "bing":
            url = "https://api.bing.microsoft.com/v7.0/search?" + urllib.parse.urlencode(
                {"q": query, "count": limit, "mkt": "ja-JP"}
            )
            data = request_json(url, {"Ocp-Apim-Subscription-Key": api_key})
            rows = (data.get("webPages") or {}).get("value", []) if isinstance(data, dict) else []
            return parse_api_results(rows, "name", "url", "snippet", limit)
        if provider == "serpapi":
            url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(
                {"engine": "google", "q": query, "num": limit, "hl": "ja", "gl": "jp", "api_key": api_key}
            )
            data = request_json(url)
            rows = data.get("organic_results", []) if isinstance(data, dict) else []
            return parse_api_results(rows, "title", "link", "snippet", limit)
    except (OSError, ValueError, TypeError) as exc:
        warn_search_backend(
            f"{SEARCH_PROVIDER_ENV}={provider!r} failed ({type(exc).__name__}); falling back to HTML search."
        )
        return []
    warn_search_backend(f"unsupported {SEARCH_PROVIDER_ENV}={provider!r}; falling back to HTML search.")
    return []


def parse_api_results(
    rows: object, title_key: str, url_key: str, snippet_key: str, limit: int
) -> list[SearchResult]:
    results: list[SearchResult] = []
    if not isinstance(rows, list):
        return results
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get(url_key, "")
        if not is_web_url(url):
            continue
        results.append(
            SearchResult(
                title=clean_text(str(row.get(title_key, ""))),
                url=url,
                snippet=clean_text(str(row.get(snippet_key, ""))),
            )
        )
        if len(results) >= limit:
            break
    return results


def search_web(keyword: str, limit: int = 8) -> list[SearchResult]:
    api_results = search_api(keyword, limit=limit)
    if api_results:
        return api_results
    query = search_query(keyword)
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
    return results or search_bing(keyword, limit=limit)


def decode_bing_url(url: str) -> str:
    parsed = urllib.parse.urlparse(html.unescape(url))
    query = urllib.parse.parse_qs(parsed.query)
    encoded = query.get("u", [""])[0]
    if encoded.startswith("a1"):
        payload = encoded[2:]
        padding = "=" * (-len(payload) % 4)
        try:
            return base64.urlsafe_b64decode(f"{payload}{padding}").decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            return url
    return url


def strip_tags(value: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", html.unescape(value)))


def search_bing(keyword: str, limit: int = 8) -> list[SearchResult]:
    query = search_query(keyword)
    html_text = request_html(BING_SEARCH_URL, {"q": query})
    results: list[SearchResult] = []
    seen: set[str] = set()
    for block in re.findall(r'<li class="b_algo".*?</li>', html_text, flags=re.DOTALL):
        anchor = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, flags=re.DOTALL)
        if not anchor:
            continue
        url = decode_bing_url(anchor.group(1))
        title = strip_tags(anchor.group(2))
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.DOTALL)
        snippet = strip_tags(snippet_match.group(1)) if snippet_match else ""
        if not title or not is_web_url(url):
            continue
        if urllib.parse.urlparse(url).netloc.endswith("bing.com"):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def text_bigrams(value: str) -> set[str]:
    """Whitespace/punctuation-insensitive character bigrams for CJK-aware matching."""
    chars = re.sub(r"[\s　〜～・！？、。「」『』【】（）()\"'’”|/\\-]+", "", value.lower())
    return {chars[index : index + 2] for index in range(len(chars) - 1)}


def keyword_overlap(keyword: str, text: str) -> float:
    """Fraction of the keyword's character bigrams that appear in ``text`` (0..1).

    Japanese keywords have no spaces, so the old token split produced a single
    token that never matched a result title. Character bigrams let a result like
    ``ディア・エヴァン・ハンセン 公式`` score highly while unrelated pages stay near 0.
    """
    keyword_grams = text_bigrams(keyword)
    if not keyword_grams:
        return 0.0
    return len(keyword_grams & text_bigrams(text)) / len(keyword_grams)


def official_score(result: SearchResult, keyword: str) -> float:
    host = hostname(result.url)
    text = f"{result.title} {result.snippet} {result.url}".lower()
    title_snippet = f"{result.title} {result.snippet}"
    overlap = keyword_overlap(keyword, title_snippet)
    token_score = sum(2 for token in keyword.lower().split() if len(token) >= 3 and token in text)
    trusted_host = any(hint in host for hint in OFFICIAL_HOST_HINTS)
    has_keyword_relevance = overlap >= MIN_KEYWORD_OVERLAP or token_score > 0
    if not trusted_host and not has_keyword_relevance:
        return -20.0 if any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS) else 0.0

    score = 0.0
    if any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS):
        score -= 20
    if is_ticket_url(result.url):
        score -= 5
    if any(hint.lower() in text for hint in OFFICIAL_HINTS):
        score += 10
    # CJK-aware relevance: reward results whose title/snippet share characters
    # with the keyword. Worth up to +30 so a strong title match dominates noise.
    score += 30 * overlap
    # Latin keywords still benefit from token matching.
    score += token_score
    if trusted_host:
        score += 6
    if host.endswith((".co.jp", ".or.jp")):
        score += 4
    if "news" in result.url or "live" in result.url or "stage" in result.url:
        score += 3
    return score


def choose_official_results(results: Sequence[SearchResult], keyword: str, limit: int = 3) -> list[SearchResult]:
    scored = [(official_score(result, keyword), result) for result in results]
    return [result for score, result in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:limit]


def page_matches_keyword(keyword: str, page: Page) -> bool:
    title_and_intro = f"{page.title} {page.text[:1200]}"
    if keyword_overlap(keyword, title_and_intro) >= MIN_KEYWORD_OVERLAP:
        return True
    haystack = title_and_intro.lower()
    return any(len(token) >= 3 and token in haystack for token in keyword.lower().split())


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


# Schedule pages often label a performance run as "（…）公演 期間 2026年…～…" or just
# "期 間 2026年…" rather than with the compact labels below, and frequently space out
# CJK characters (e.g. "会 場"). Capture the date range directly, then exclude any
# whose lead context is a ticketing window such as "受付期間" / "抽選受付期間".
_PERIOD_DATE = r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s*[(（][^)）]{1,5}[)）])?"
_PERIOD_DATE_END = r"(?:20\d{2}\s*年\s*)?(?:\d{1,2}\s*月\s*)?\d{1,2}\s*日(?:\s*[(（][^)）]{1,5}[)）])?"
PERFORMANCE_PERIOD_RE = re.compile(
    r"期\s*間\s*(?P<range>" + _PERIOD_DATE + r"(?:\s*[～~〜\-]\s*" + _PERIOD_DATE_END + r")?)"
)
SLASH_PERFORMANCE_PERIOD_RE = re.compile(
    r"(?P<range>20\d{2}/\d{1,2}/\d{1,2}\s*[～~〜\-]\s*20\d{2}/\d{1,2}/\d{1,2})\s*公演"
)
_PERIOD_LEAD_NOISE = ("受付", "申込", "抽選", "先行", "販売", "入金", "支払", "発売")
EVENT_DATE_NOISE = ("一般前売", "発売", "先行", "抽選", "料金", "消費税込", "備考", "小人", "追記")


def extract_event_dates(text: str) -> tuple[str, ...]:
    dates: list[str] = []
    seen: set[str] = set()
    for candidate in nearby_phrases(text, ("公演日", "公演期間", "開催日", "開催日時"), limit=5):
        date_match = DATE_RE.search(candidate)
        lead = candidate[: date_match.start()] if date_match else candidate
        label_before_date = any(label in lead for label in ("公演日", "公演期間", "開催日", "開催日時"))
        noise_scope = lead if label_before_date else candidate
        keep = bool(date_match) and not any(noisy in noise_scope for noisy in EVENT_DATE_NOISE)
        if keep and candidate not in seen:
            dates.append(candidate)
            seen.add(candidate)
    for match in SLASH_PERFORMANCE_PERIOD_RE.finditer(text):
        lead = text[max(0, match.start() - 24):match.start()].replace(" ", "").replace("　", "")
        if any(noisy in lead for noisy in _PERIOD_LEAD_NOISE):
            continue
        phrase = clean_text(match.group("range")).strip(" ：:、。")
        if phrase and phrase not in seen:
            dates.append(phrase)
            seen.add(phrase)
    for match in PERFORMANCE_PERIOD_RE.finditer(text):
        lead = text[max(0, match.start() - 12):match.start()].replace(" ", "").replace("　", "")
        if any(noisy in lead for noisy in _PERIOD_LEAD_NOISE):
            continue
        phrase = clean_text(match.group("range")).strip(" ：:、。")
        if phrase and phrase not in seen:
            dates.append(phrase)
            seen.add(phrase)
    return tuple(dates)


def extract_venues(text: str) -> tuple[str, ...]:
    venues: list[str] = []
    seen: set[str] = set()
    # Stop the venue capture at address/section markers and at ticket-sale noise,
    # so a trailing "チケット抽選先行…" link does not get swallowed into the venue
    # (which would then trip the noise filter below and drop the venue entirely).
    boundary = r"(?=〒|MAP|座席表|【|チケット|抽選|先行|受付|申込|発売|公演日|出演|料金|開場|開演|主催|お問い?合せ|お問い合わせ|TEL|$)"
    # Schedule pages frequently space out CJK labels (e.g. "会 場"), so match the
    # 会場 label space-tolerantly to catch venues like "EXシアター有明(…)" that have
    # no 劇場/ホール suffix and would otherwise be missed entirely.
    patterns = (
        rf"会\s*場のご案内\s*(?P<venue>[^。【\n\r]{{2,80}}?){boundary}",
        rf"会\s*場\s*(?P<venue>[^。【\n\r]{{2,80}}?){boundary}",
        r"(?:東京|大阪|名古屋|京都|福岡|札幌|仙台|静岡|広島|全国)\s+(?P<venue>[^\s。]{2,40}(?:劇場|ホール|アリーナ|ドーム|会館)(?:［[^］]+］)?(?:（[^）]+）)?)",
        r"(?P<venue>[\w一-龥ぁ-んァ-ヶー・（）() ]{2,40}(?:劇場|ホール|アリーナ|ドーム|会館|大劇場|小劇場))",
    )
    for pattern_index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, text):
            venue = clean_text(match.group("venue")).strip(" ：:、。")
            venue = re.sub(r"^(?:のご案内|会場のご案内)\s*", "", venue).strip()
            if not venue or venue in seen:
                continue
            if any(noisy in venue for noisy in ("チケット", "ご購入", "ご予約", "販売", "受付", "お問い合わせ", "お問合せ", "主催", "電話", "ぜひ", "グループ観劇", "座席料金", "座席図", "アクセス", "車いす")):
                continue
            if any(venue in existing or existing in venue for existing in seen):
                continue
            venues.append(venue)
            seen.add(venue)
            if len(venues) >= 5:
                return tuple(venues)
        if venues and pattern_index == 2:
            return tuple(venues)
    return tuple(venues)


def extract_ticket_links(page: Page) -> tuple[Link, ...]:
    links: list[Link] = []
    seen: set[str] = set()
    for link in page.links:
        if is_noisy_url(link.url):
            continue
        if is_generic_ticket_info_url(link.url):
            continue
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
    event_dates: list[str] = []
    venues: list[str] = []
    for page in official_pages:
        for link in extract_ticket_links(page):
            if link.url not in seen:
                ticket_links.append(link)
                seen.add(link.url)
        for date in extract_event_dates(page.text):
            if date not in event_dates:
                event_dates.append(date)
        for venue in extract_venues(page.text):
            if venue not in venues:
                venues.append(venue)
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
        event_dates=tuple(event_dates),
        venues=tuple(venues),
        ticket_links=tuple(ticket_links),
    )


def infer_year(month: int, day: int, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    candidate = dt.date(today.year, month, day)
    if candidate < today - dt.timedelta(days=180):
        return today.year + 1
    return today.year


def is_valid_month_day(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31


def normalize_date(value: str) -> str:
    value = clean_text(value)
    jp = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", value)
    if jp:
        month, day = int(jp.group(2)), int(jp.group(3))
        return f"{int(jp.group(1)):04d}-{month:02d}-{day:02d}" if is_valid_month_day(month, day) else value
    western = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", value)
    if western:
        month, day = int(western.group(2)), int(western.group(3))
        return f"{int(western.group(1)):04d}-{month:02d}-{day:02d}" if is_valid_month_day(month, day) else value
    jp_short = re.search(r"(\d{1,2})月\s*(\d{1,2})日", value)
    if jp_short:
        month, day = int(jp_short.group(1)), int(jp_short.group(2))
        return f"{infer_year(month, day):04d}-{month:02d}-{day:02d}" if is_valid_month_day(month, day) else value
    short = re.search(r"(\d{1,2})[./-](\d{1,2})", value)
    if short:
        month, day = int(short.group(1)), int(short.group(2))
        return f"{infer_year(month, day):04d}-{month:02d}-{day:02d}" if is_valid_month_day(month, day) else value
    return value


def normalized_iso_date(value: str) -> str | None:
    normalized = normalize_date(value)
    return normalized if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", normalized) else None


def first_event_sort_date(event: dict[str, object]) -> dt.date | None:
    date_items = event.get("event_dates", [])
    if not isinstance(date_items, list):
        return None
    for item in date_items:
        for match in DATE_RE.finditer(str(item)):
            normalized = normalized_iso_date(match.group(0))
            if normalized:
                return parse_iso_date(normalized)
    return None


def context_windows(text: str, patterns: Sequence[str], width: int = 220) -> list[str]:
    windows: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        try:
            matches = re.finditer(pattern, text, flags=re.IGNORECASE)
        except re.error:
            matches = re.finditer(re.escape(pattern), text, flags=re.IGNORECASE)
        for match in matches:
            start = max(0, match.start() - 60)
            end = min(len(text), match.end() + width)
            window = clean_text(text[start:end])
            if window not in seen:
                windows.append(window)
                seen.add(window)
    return windows


def label_forward_contexts(text: str, labels: Sequence[str], lead: int = 12, width: int = 180) -> list[str]:
    windows: list[str] = []
    seen: set[str] = set()
    for label in labels:
        for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            start = max(0, match.start() - lead)
            end = min(len(text), match.end() + width)
            window = clean_text(text[start:end])
            if window not in seen:
                windows.append(window)
                seen.add(window)
    return windows


def extract_first_date(text: str, labels: Sequence[str]) -> str | None:
    for label in labels:
        for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            window = text[match.start() : min(len(text), match.end() + 100)]
            date_match = DATE_RE.search(window)
            if date_match:
                normalized = normalized_iso_date(date_match.group(0))
                if normalized:
                    return normalized
    return None


def extract_last_date_before_label(text: str, labels: Sequence[str], width: int = 60) -> str | None:
    for label in labels:
        for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            window = text[max(0, match.start() - width) : match.start()]
            dates = [date for date in (normalized_iso_date(m.group(0)) for m in DATE_RE.finditer(window)) if date]
            if dates:
                return dates[-1]
    return None


def extract_range(text: str) -> tuple[str | None, str | None]:
    match = RANGE_RE.search(text)
    if not match:
        dates = [date for date in (normalized_iso_date(m.group(0)) for m in DATE_RE.finditer(text)) if date]
        if len(dates) >= 2:
            return dates[0], dates[1]
        if len(dates) == 1:
            return dates[0], None
        return None, None
    return normalized_iso_date(match.group("start")), normalized_iso_date(match.group("end"))


def extract_range_after_label(text: str, labels: Sequence[str]) -> tuple[str | None, str | None]:
    for label in labels:
        for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            window = text[match.start() : min(len(text), match.end() + 140)]
            start, end = extract_range(window)
            if start or end:
                return start, end
    return None, None


def extract_first_date_after_label(text: str, labels: Sequence[str]) -> str | None:
    for label in labels:
        for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
            window = text[match.start() : min(len(text), match.end() + 40)]
            date_match = DATE_RE.search(window)
            if date_match:
                normalized = normalized_iso_date(date_match.group(0))
                if normalized:
                    return normalized
    return None


def round_name_from_context(context: str, fallback: str) -> str:
    for pattern in ROUND_LABEL_PATTERNS:
        match = re.search(pattern, context, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(0))
    return fallback


def extract_ticket_rounds(page: Page) -> tuple[TicketRound, ...]:
    contexts = context_windows(page.text, ROUND_LABEL_PATTERNS + ("受付期間", "申込期間", "抽選結果", "当落", "一般発売"))
    contexts = contexts + label_forward_contexts(page.text, ("先行先着販売", "先着先行"))
    contexts = contexts + context_windows(page.text, ROUND_CONTEXT_HINTS)
    rounds: list[TicketRound] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for index, context in enumerate(contexts, start=1):
        start, end = extract_range_after_label(
            context,
            (
                "受付期間",
                "申込期間",
                "申込み期間",
                "申込受付期間",
                "抽選申込期間",
                "抽選受付期間",
                "抽選先行",
                "先行抽選エントリー",
            ),
        )
        results_date = extract_first_date(context, ("抽選結果", "結果発表", "当落", "当選発表"))
        general_sale_date = extract_first_date(context, ("一般発売", "一般前売", "発売日"))
        payment_deadline = extract_first_date(context, ("入金", "支払", "払込", "決済"))
        start = start or extract_last_date_before_label(context, ("会員先行予約", "先行予約", "先着先行"))
        start = start or extract_first_date_after_label(context, ("先行先着販売", "先着先行"))
        results_date = results_date or extract_first_date(context, ("抽選結果", "結果発表", "当落", "当選発表"))
        general_sale_date = general_sale_date or extract_first_date(context, ("一般発売", "発売日", "発売開始"))
        general_sale_date = general_sale_date or extract_last_date_before_label(context, ("一般発売", "一般前売", "発売開始"))
        payment_deadline = payment_deadline or extract_first_date(context, ("入金", "支払", "支払い", "支払期限", "入金締切"))
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


def adapt_ticket_rounds(page: Page, platform: str) -> tuple[TicketRound, ...]:
    rounds = extract_ticket_rounds(page)
    return tuple(
        normalize_ticket_round(
            dataclasses.replace(ticket, source=platform, platform=platform, confidence=platform_confidence(platform))
        )
        for ticket in rounds
    )


def extract_ticket_rounds_for_page(page: Page) -> tuple[TicketRound, ...]:
    platform = source_name_for_url(page.url)
    if platform in TICKET_DOMAINS:
        return adapt_ticket_rounds(page, platform)
    return dedupe_ticket_rounds(extract_ticket_rounds(page))


def build_blocks(keyword: str, search_results: Sequence[SearchResult] | None = None) -> AppBlocks:
    results = list(search_results) if search_results is not None else search_web(keyword)
    official_pages: list[Page] = []
    for result in choose_official_results(results, keyword):
        try:
            page = fetch_page(result.url)
        except (OSError, ValueError):
            continue
        if page_matches_keyword(keyword, page):
            official_pages.append(page)

    event_info = build_event_info(keyword, official_pages)
    rounds: list[TicketRound] = []
    for link in event_info.ticket_links:
        if is_portal_search_url(link.url):
            continue
        try:
            rounds.extend(extract_ticket_rounds_for_page(fetch_page(link.url)))
        except (OSError, ValueError):
            rounds.append(TicketRound(source=source_name_for_url(link.url), url=link.url, name="Fetch failed", evidence=link.label))
    return AppBlocks(general_info=event_info, ticket_info=dedupe_ticket_rounds(rounds))


def build_exact_event_blocks(keyword: str, title: str, url: str, snippet: str = "") -> AppBlocks:
    page = fetch_page(url)
    event_info = build_event_info(keyword or title or page.title, (page,))
    if not event_info.title:
        event_info = dataclasses.replace(event_info, title=title or page.title)
    rounds: list[TicketRound] = list(extract_ticket_rounds_for_page(page))
    for link in event_info.ticket_links:
        if is_portal_search_url(link.url):
            continue
        try:
            rounds.extend(extract_ticket_rounds_for_page(fetch_page(link.url)))
        except (OSError, ValueError):
            rounds.append(TicketRound(source=source_name_for_url(link.url), url=link.url, name="Fetch failed", evidence=link.label))
    if snippet and not event_info.summary:
        event_info = dataclasses.replace(event_info, summary=snippet)
    return AppBlocks(general_info=event_info, ticket_info=dedupe_ticket_rounds(tuple(rounds)))


def build_artist_blocks(keyword: str, search_results: Sequence[SearchResult] | None = None) -> AppBlocks:
    results = list(search_results) if search_results is not None else search_web(keyword)
    official_pages: list[Page] = []
    for result in choose_official_results(results, keyword):
        try:
            page = fetch_page(result.url)
        except (OSError, ValueError):
            continue
        if page_matches_keyword(keyword, page):
            official_pages.append(page)
    info = build_event_info(keyword, official_pages)
    return AppBlocks(general_info=dataclasses.replace(info, ticket_links=()), ticket_info=())


def build_artist_event_blocks(keyword: str, limit: int = 8) -> list[AppBlocks]:
    blocks: list[AppBlocks] = []
    seen_urls: set[str] = set()
    for result in choose_official_results(search_web(keyword, limit=limit), keyword, limit=limit):
        if result.url in seen_urls or is_noisy_url(result.url):
            continue
        seen_urls.add(result.url)
        try:
            page = fetch_page(result.url)
        except (OSError, ValueError):
            continue
        if not page_matches_keyword(keyword, page):
            continue
        info = build_event_info(keyword, (page,))
        if not info.title:
            info = dataclasses.replace(info, title=result.title or page.title)
        if result.snippet and not info.summary:
            info = dataclasses.replace(info, summary=result.snippet)
        blocks.append(AppBlocks(general_info=info, ticket_info=extract_ticket_rounds_for_page(page)))
    return blocks


def build_blocks_for_watch(db_path: str, watch: Watch) -> AppBlocks:
    if watch.kind == WATCH_KIND_ARTIST:
        return build_artist_blocks(watch.keyword)
    manual_sources = list_watch_sources(db_path, str(watch.id))
    manual_links = tuple(Link(source.label, source.url) for source in manual_sources)
    public_sources = [source for source in manual_sources if not source.private_note]

    # Fetch curated public sources once and reuse them for both the headline
    # event info and the ticket rounds.
    source_pages: list[Page] = []
    extra_rounds: list[TicketRound] = []
    for source in public_sources:
        try:
            page = fetch_page(source.url)
        except (OSError, ValueError):
            extra_rounds.append(
                TicketRound(
                    source=source.platform,
                    platform=source.platform,
                    url=source.url,
                    name="Fetch failed",
                    evidence=source.label,
                    confidence=source.confidence,
                )
            )
            continue
        source_pages.append(page)
        extra_rounds.extend(extract_ticket_rounds_for_page(page))
        for link in page.links:
            if not is_shiki_stage_schedule_url(link.url):
                continue
            try:
                linked_page = fetch_page(link.url)
            except (OSError, ValueError):
                continue
            source_pages.append(linked_page)
            extra_rounds.extend(extract_ticket_rounds_for_page(linked_page))

    if source_pages:
        # A curated official source is authoritative, so trust it for the headline
        # info and skip web discovery, which is bot-throttled and can spawn an
        # unrelated "twin" event for the same watch.
        info = build_event_info(watch.keyword, source_pages)
        base_rounds: tuple[TicketRound, ...] = ()
    else:
        blocks = build_blocks(watch.keyword)
        info = blocks.general_info
        base_rounds = blocks.ticket_info

    existing_urls = {link.url for link in info.ticket_links}
    merged_links = info.ticket_links + tuple(link for link in manual_links if link.url not in existing_urls)
    merged_info = dataclasses.replace(info, ticket_links=merged_links)
    return AppBlocks(general_info=merged_info, ticket_info=dedupe_ticket_rounds(base_rounds + tuple(extra_rounds)))


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
            kind TEXT NOT NULL DEFAULT 'artist',
            tags TEXT NOT NULL DEFAULT '',
            preferred_regions TEXT NOT NULL DEFAULT '',
            preferred_venues TEXT NOT NULL DEFAULT '',
            alert_preferences TEXT NOT NULL DEFAULT '',
            muted INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            watch_id INTEGER NOT NULL,
            canonical_title TEXT NOT NULL,
            official_url TEXT,
            summary TEXT,
            event_dates_json TEXT NOT NULL DEFAULT '[]',
            venues_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'watching',
            event_key TEXT NOT NULL DEFAULT '',
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
            provenance TEXT NOT NULL DEFAULT 'low_confidence',
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
            round_number INTEGER,
            platform TEXT,
            lottery_start TEXT,
            lottery_end TEXT,
            results_date TEXT,
            general_sale_date TEXT,
            payment_deadline TEXT,
            application_start_at TEXT,
            application_end_at TEXT,
            payment_start_at TEXT,
            payment_end_at TEXT,
            trade_start_at TEXT,
            trade_end_at TEXT,
            confidence INTEGER NOT NULL DEFAULT 50,
            status TEXT NOT NULL DEFAULT 'unknown',
            round_type TEXT NOT NULL DEFAULT 'unknown',
            membership_required TEXT NOT NULL DEFAULT 'unknown',
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

        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            alert_key TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_id, alert_key),
            FOREIGN KEY(event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS watch_sources (
            id INTEGER PRIMARY KEY,
            watch_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 70,
            private_note INTEGER NOT NULL DEFAULT 0,
            muted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(watch_id, url),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );
        """
    )
    migrate_db(connection)


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db(connection: sqlite3.Connection) -> None:
    add_column_if_missing(connection, "watched_keywords", "tags", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "kind", "TEXT NOT NULL DEFAULT 'artist'")
    add_column_if_missing(connection, "watched_keywords", "preferred_regions", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "preferred_venues", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "alert_preferences", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "muted", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(connection, "watched_keywords", "last_checked_at", "TEXT")
    add_column_if_missing(connection, "events", "status", "TEXT NOT NULL DEFAULT 'watching'")
    add_column_if_missing(connection, "events", "event_key", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "events", "event_dates_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing(connection, "events", "venues_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing(connection, "sources", "provenance", "TEXT NOT NULL DEFAULT 'low_confidence'")

    add_column_if_missing(connection, "ticket_rounds", "round_number", "INTEGER")
    add_column_if_missing(connection, "ticket_rounds", "platform", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "application_start_at", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "application_end_at", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "payment_start_at", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "payment_end_at", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "trade_start_at", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "trade_end_at", "TEXT")
    add_column_if_missing(connection, "ticket_rounds", "confidence", "INTEGER NOT NULL DEFAULT 50")
    add_column_if_missing(connection, "ticket_rounds", "status", "TEXT NOT NULL DEFAULT 'unknown'")
    add_column_if_missing(connection, "ticket_rounds", "round_type", "TEXT NOT NULL DEFAULT 'unknown'")
    add_column_if_missing(connection, "ticket_rounds", "membership_required", "TEXT NOT NULL DEFAULT 'unknown'")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS watch_sources (
            id INTEGER PRIMARY KEY,
            watch_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 70,
            private_note INTEGER NOT NULL DEFAULT 0,
            muted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(watch_id, url),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );
        """
    )
    connection.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")


def upsert_keyword(connection: sqlite3.Connection, keyword: str, now: str) -> int:
    connection.execute(
        """
        INSERT INTO watched_keywords(keyword, kind, alert_preferences, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(keyword) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (keyword, WATCH_KIND_EVENT, DEFAULT_ALERT_PREFERENCES, now, now),
    )
    row = connection.execute("SELECT id FROM watched_keywords WHERE keyword = ?", (keyword,)).fetchone()
    return int(row[0])


def add_watch(
    db_path: str,
    keyword: str,
    kind: str = WATCH_KIND_EVENT,
    tags: str = "",
    preferred_regions: str = "",
    preferred_venues: str = "",
    alert_preferences: str = DEFAULT_ALERT_PREFERENCES,
    now: str | None = None,
) -> Watch:
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        connection.execute(
            """
            INSERT INTO watched_keywords(
                keyword, kind, tags, preferred_regions, preferred_venues, alert_preferences, muted, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                kind = excluded.kind,
                tags = excluded.tags,
                preferred_regions = excluded.preferred_regions,
                preferred_venues = excluded.preferred_venues,
                alert_preferences = excluded.alert_preferences,
                muted = 0,
                updated_at = excluded.updated_at
            """,
            (keyword, kind, tags, preferred_regions, preferred_venues, alert_preferences, timestamp, timestamp),
        )
        row = connection.execute(
            """
            SELECT id, keyword, kind, tags, preferred_regions, preferred_venues, alert_preferences, muted, last_checked_at
            FROM watched_keywords
            WHERE keyword = ?
            """,
            (keyword,),
        ).fetchone()
        return watch_from_row(row)


def watch_from_row(row: sqlite3.Row | tuple[object, ...]) -> Watch:
    return Watch(
        id=int(row[0]),
        keyword=str(row[1]),
        kind=str(row[2] or WATCH_KIND_ARTIST),
        tags=str(row[3] or ""),
        preferred_regions=str(row[4] or ""),
        preferred_venues=str(row[5] or ""),
        alert_preferences=str(row[6] or DEFAULT_ALERT_PREFERENCES),
        muted=bool(row[7]),
        last_checked_at=str(row[8]) if row[8] else None,
    )


def watch_source_from_row(row: sqlite3.Row | tuple[object, ...]) -> WatchSource:
    return WatchSource(
        id=int(row[0]),
        watch_id=int(row[1]),
        url=str(row[2]),
        label=str(row[3] or row[2]),
        platform=str(row[4] or source_name_for_url(str(row[2]))),
        confidence=int(row[5] or 70),
        private_note=bool(row[6]),
        muted=bool(row[7]),
    )


def list_watches(db_path: str, include_muted: bool = False, kind: str | None = None) -> list[Watch]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        clauses: list[str] = []
        params: list[object] = []
        if not include_muted:
            clauses.append("muted = 0")
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = connection.execute(
            f"""
            SELECT id, keyword, kind, tags, preferred_regions, preferred_venues, alert_preferences, muted, last_checked_at
            FROM watched_keywords
            {where}
            ORDER BY id
            """,
            params,
        ).fetchall()
        return [watch_from_row(row) for row in rows]


def resolve_watch(connection: sqlite3.Connection, identifier: str) -> Watch | None:
    if identifier.isdigit():
        row = connection.execute(
            """
            SELECT id, keyword, kind, tags, preferred_regions, preferred_venues, alert_preferences, muted, last_checked_at
            FROM watched_keywords
            WHERE id = ?
            """,
            (int(identifier),),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT id, keyword, kind, tags, preferred_regions, preferred_venues, alert_preferences, muted, last_checked_at
            FROM watched_keywords
            WHERE keyword = ?
            """,
            (identifier,),
        ).fetchone()
    return watch_from_row(row) if row else None


def remove_watch(db_path: str, identifier: str, now: str | None = None) -> bool:
    return set_watch_muted(db_path, identifier, True, now=now, only_if_changed=True)


def set_watch_muted(
    db_path: str,
    identifier: str,
    muted: bool,
    now: str | None = None,
    only_if_changed: bool = False,
) -> bool:
    timestamp = now or utc_now_iso()
    muted_value = 1 if muted else 0
    changed_clause = " AND muted != ?" if only_if_changed else ""
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        if identifier.isdigit():
            params: tuple[object, ...] = (muted_value, timestamp, int(identifier))
            if only_if_changed:
                params += (muted_value,)
            cursor = connection.execute(
                f"UPDATE watched_keywords SET muted = ?, updated_at = ? WHERE id = ?{changed_clause}",
                params,
            )
        else:
            params = (muted_value, timestamp, identifier)
            if only_if_changed:
                params += (muted_value,)
            cursor = connection.execute(
                f"UPDATE watched_keywords SET muted = ?, updated_at = ? WHERE keyword = ?{changed_clause}",
                params,
            )
        return bool(cursor.rowcount)


def add_watch_source(
    db_path: str,
    watch_identifier: str,
    url: str,
    label: str = "",
    private_note: bool = False,
    now: str | None = None,
) -> WatchSource:
    timestamp = now or utc_now_iso()
    url = clean_text(url)
    if not url:
        raise ValueError("Source URL is required")
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        watch = resolve_watch(connection, watch_identifier)
        if not watch:
            raise ValueError(f"Watch not found: {watch_identifier}")
        platform = "manual" if private_note else source_name_for_url(url)
        confidence = 40 if private_note else platform_confidence(platform)
        connection.execute(
            """
            INSERT INTO watch_sources(
                watch_id, url, label, platform, confidence, private_note, muted, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(watch_id, url) DO UPDATE SET
                label = excluded.label,
                platform = excluded.platform,
                confidence = excluded.confidence,
                private_note = excluded.private_note,
                muted = 0,
                updated_at = excluded.updated_at
            """,
            (watch.id, url, label or url, platform, confidence, int(private_note), timestamp, timestamp),
        )
        row = connection.execute(
            """
            SELECT id, watch_id, url, label, platform, confidence, private_note, muted
            FROM watch_sources
            WHERE watch_id = ? AND url = ?
            """,
            (watch.id, url),
        ).fetchone()
        return watch_source_from_row(row)


def list_watch_sources(db_path: str, watch_identifier: str | None = None, include_muted: bool = False) -> list[WatchSource]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        params: list[object] = []
        clauses: list[str] = []
        if watch_identifier:
            watch = resolve_watch(connection, watch_identifier)
            if not watch:
                return []
            clauses.append("s.watch_id = ?")
            params.append(watch.id)
        if not include_muted:
            clauses.append("s.muted = 0")
            clauses.append("w.muted = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = connection.execute(
            f"""
            SELECT s.id, s.watch_id, s.url, s.label, s.platform, s.confidence, s.private_note, s.muted
            FROM watch_sources s
            JOIN watched_keywords w ON w.id = s.watch_id
            {where}
            ORDER BY s.watch_id, s.id
            """,
            params,
        ).fetchall()
        return [watch_source_from_row(row) for row in rows]


def remove_watch_source(db_path: str, identifier: str, now: str | None = None) -> bool:
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        if identifier.isdigit():
            cursor = connection.execute(
                "UPDATE watch_sources SET muted = 1, updated_at = ? WHERE id = ? AND muted = 0",
                (timestamp, int(identifier)),
            )
        else:
            cursor = connection.execute(
                "UPDATE watch_sources SET muted = 1, updated_at = ? WHERE url = ? AND muted = 0",
                (timestamp, identifier),
            )
        return bool(cursor.rowcount)


def set_watch_source_muted(db_path: str, identifier: str, muted: bool, now: str | None = None) -> bool:
    timestamp = now or utc_now_iso()
    muted_value = 1 if muted else 0
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        if identifier.isdigit():
            cursor = connection.execute(
                "UPDATE watch_sources SET muted = ?, updated_at = ? WHERE id = ?",
                (muted_value, timestamp, int(identifier)),
            )
        else:
            cursor = connection.execute(
                "UPDATE watch_sources SET muted = ?, updated_at = ? WHERE url = ?",
                (muted_value, timestamp, identifier),
            )
        return bool(cursor.rowcount)


def mark_watch_checked(db_path: str, watch_id: int, now: str) -> None:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        connection.execute(
            "UPDATE watched_keywords SET last_checked_at = ?, updated_at = ? WHERE id = ?",
            (now, now, watch_id),
        )


def comma_values(value: str) -> tuple[str, ...]:
    return tuple(clean_text(part).lower() for part in value.split(",") if clean_text(part))


def watch_matches_blocks(watch: Watch, blocks: AppBlocks) -> bool:
    regions = comma_values(watch.preferred_regions)
    venues = comma_values(watch.preferred_venues)
    if not regions and not venues:
        return True
    haystack = " ".join(blocks.general_info.event_dates + blocks.general_info.venues + (blocks.general_info.summary or "",)).lower()
    return any(region in haystack for region in regions) or any(venue in haystack for venue in venues)


def filter_alerts_for_watch(watch: Watch, blocks: AppBlocks, alerts: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    allowed = set(comma_values(watch.alert_preferences or DEFAULT_ALERT_PREFERENCES))
    if not watch_matches_blocks(watch, blocks):
        return [
            {
                "type": "watch_filtered",
                "watch_id": str(watch.id),
                "keyword": watch.keyword,
                "reason": "preferred region/venue did not match",
            }
        ]
    if not allowed:
        return []
    return [alert for alert in alerts if alert.get("type", "").lower() in allowed]


def event_match_reasons(event: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    keyword = clean_text(str(event.get("keyword") or ""))
    title = clean_text(str(event.get("title") or ""))
    summary = clean_text(str(event.get("summary") or ""))
    dates = [clean_text(str(value)) for value in event.get("event_dates", []) if clean_text(str(value))]
    venues = [clean_text(str(value)) for value in event.get("venues", []) if clean_text(str(value))]
    sources = event.get("manual_sources", [])
    rounds = event.get("rounds", [])

    haystack = " ".join((title, summary, " ".join(dates), " ".join(venues))).lower()
    if keyword and keyword.lower() in haystack:
        reasons.append(f"keyword match: {keyword}")
    elif keyword:
        reasons.append(f"tracked keyword: {keyword}")
    if dates:
        reasons.append(f"date clue: {dates[0]}")
    if venues:
        reasons.append(f"venue clue: {venues[0]}")
    if isinstance(sources, list) and sources:
        public_count = sum(1 for source in sources if isinstance(source, dict) and not source.get("private_note"))
        private_count = sum(1 for source in sources if isinstance(source, dict) and source.get("private_note"))
        if public_count:
            reasons.append(f"manual public source: {public_count}")
        if private_count:
            reasons.append(f"private source note: {private_count}")
    if isinstance(rounds, list) and rounds:
        best_confidence = max((int(round_info.get("confidence") or 0) for round_info in rounds if isinstance(round_info, dict)), default=0)
        platforms = sorted({str(round_info.get("platform") or "") for round_info in rounds if isinstance(round_info, dict) and round_info.get("platform")})
        urgent = [str(round_info.get("status")) for round_info in rounds if isinstance(round_info, dict) and round_info.get("status") in UPCOMING_STATUS_ORDER]
        if platforms:
            reasons.append(f"ticket platforms: {', '.join(platforms[:3])}")
        if best_confidence:
            reasons.append(f"source confidence: {best_confidence}")
        if urgent:
            reasons.append(f"ticket status: {urgent[0]}")
    if not reasons:
        reasons.append("saved local event")
    return reasons


def run_watches(db_path: str, now: str | None = None, kind: str | None = None) -> list[dict[str, str]]:
    timestamp = now or utc_now_iso()
    alerts: list[dict[str, str]] = []
    for watch in list_watches(db_path, kind=kind):
        try:
            if watch.kind == WATCH_KIND_ARTIST:
                artist_blocks = build_artist_event_blocks(watch.keyword)
                for blocks in artist_blocks:
                    saved_alerts = save_blocks(db_path, blocks, now=timestamp, watch_id=watch.id)
                    alerts.extend(filter_alerts_for_watch(watch, blocks, saved_alerts))
                if not artist_blocks:
                    blocks = build_blocks_for_watch(db_path, watch)
                    saved_alerts = save_blocks(db_path, blocks, now=timestamp, watch_id=watch.id)
                    alerts.extend(filter_alerts_for_watch(watch, blocks, saved_alerts))
            else:
                blocks = build_blocks_for_watch(db_path, watch)
                saved_alerts = save_blocks(db_path, blocks, now=timestamp)
                alerts.extend(filter_alerts_for_watch(watch, blocks, saved_alerts))
        except (OSError, ValueError, sqlite3.Error) as error:
            alerts.append(
                {
                    "type": "watch_failed",
                    "watch_id": str(watch.id),
                    "keyword": watch.keyword,
                    "error": str(error),
                }
            )
        finally:
            mark_watch_checked(db_path, watch.id, timestamp)
    return alerts


def run_watch_loop(
    db_path: str,
    interval_minutes: int = 60,
    kind: str | None = WATCH_KIND_EVENT,
    alerts_json: bool = False,
    max_runs: int | None = None,
    run_immediately: bool = True,
    stop_after_errors: int | None = None,
    sleep_func=time.sleep,
    run_func=run_watches,
) -> int:
    interval_seconds = interval_minutes * 60
    run_count = 0
    error_count = 0
    first_run = True
    try:
        while max_runs is None or run_count < max_runs:
            if not (first_run and run_immediately):
                sleep_func(interval_seconds)
            first_run = False
            try:
                alerts = run_func(db_path, kind=kind)
                run_count += 1
                error_count = 0
                if alerts_json:
                    print(json.dumps({"run": run_count, "alerts": alerts}, ensure_ascii=False))
                else:
                    scope = kind or "all"
                    print(f"Run {run_count}: checked {scope} watches; {len(alerts)} alerts.")
            except (OSError, ValueError, sqlite3.Error) as error:
                run_count += 1
                error_count += 1
                print(f"Run {run_count}: watch loop failed: {error}")
                if stop_after_errors is not None and error_count >= stop_after_errors:
                    return 1
    except KeyboardInterrupt:
        print("Watch loop stopped.")
        return 0
    return 0


def recent_events(
    db_path: str,
    limit: int = 50,
    include_muted_sources: bool = False,
    include_muted_watches: bool = False,
) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        watch_muted_clause = "" if include_muted_watches else "WHERE w.muted = 0"
        rows = connection.execute(
            f"""
            SELECT e.id, w.id, w.keyword, w.kind, e.canonical_title, e.official_url, e.summary,
                   e.event_dates_json, e.venues_json, e.status, e.updated_at
            FROM events e
            JOIN watched_keywords w ON w.id = e.watch_id
            {watch_muted_clause}
            ORDER BY e.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        events: list[dict[str, object]] = []
        for row in rows:
            source_muted_clause = "" if include_muted_sources else " AND muted = 0"
            manual_sources = connection.execute(
                f"""
                SELECT id, watch_id, url, label, platform, confidence, private_note, muted
                FROM watch_sources
                WHERE watch_id = ?{source_muted_clause}
                ORDER BY id
                """,
                (row[1],),
            ).fetchall()
            rounds = connection.execute(
                """
                SELECT name, platform, url, application_start_at, application_end_at,
                       results_date, general_sale_date, payment_end_at, status, confidence,
                       round_type, membership_required, evidence
                FROM ticket_rounds
                WHERE event_id = ?
                ORDER BY platform, round_number, name
                """,
                (row[0],),
            ).fetchall()
            ticket_links = connection.execute(
                """
                SELECT label, url, platform, confidence, provenance
                FROM sources
                WHERE event_id = ?
                ORDER BY confidence DESC, label
                """,
                (row[0],),
            ).fetchall()
            event = {
                    "id": row[0],
                    "watch_id": row[1],
                    "keyword": row[2],
                    "watch_kind": row[3],
                    "title": row[4],
                    "official_url": row[5],
                    "summary": row[6],
                    "event_dates": json.loads(row[7] or "[]"),
                    "venues": json.loads(row[8] or "[]"),
                    "status": row[9],
                    "updated_at": row[10],
                    "ticket_links": [
                        {
                            "label": link[0],
                            "url": link[1],
                            "platform": link[2],
                            "confidence": link[3],
                            "provenance": link[4],
                        }
                        for link in ticket_links
                        if not is_noisy_url(link[1])
                    ],
                    "manual_sources": [dataclasses.asdict(watch_source_from_row(source)) for source in manual_sources],
                    "rounds": [
                        {
                            "name": ticket[0],
                            "platform": ticket[1],
                            "url": ticket[2],
                            "application_start_at": ticket[3],
                            "application_end_at": ticket[4],
                            "results_date": ticket[5],
                            "general_sale_date": ticket[6],
                            "payment_end_at": ticket[7],
                            "status": ticket[8],
                            "confidence": ticket[9],
                            "round_type": ticket[10],
                            "membership_required": ticket[11],
                            "evidence": ticket[12],
                        }
                        for ticket in rounds
                        if not is_noisy_url(ticket[2])
                    ],
                }
            event["match_reasons"] = event_match_reasons(event)
            events.append(event)
        return events


def upcoming_relevant_date(round_info: dict[str, object]) -> str:
    status = str(round_info.get("status") or "unknown")
    if status in {"closing_soon", "open", "upcoming"}:
        return str(round_info.get("application_end_at") or round_info.get("application_start_at") or "")
    if status == "results_today":
        return str(round_info.get("results_date") or "")
    if status == "payment_due":
        return str(round_info.get("payment_end_at") or "")
    if status == "general_sale_soon":
        return str(round_info.get("general_sale_date") or "")
    return str(
        round_info.get("application_start_at")
        or round_info.get("application_end_at")
        or round_info.get("results_date")
        or round_info.get("payment_end_at")
        or round_info.get("general_sale_date")
        or ""
    )


def upcoming_priority_rows(db_path: str, limit: int = 50, include_muted_watches: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in recent_events(db_path, limit=500, include_muted_watches=include_muted_watches):
        if event.get("watch_kind") != WATCH_KIND_EVENT:
            continue
        for round_info in event.get("rounds", []):
            if not isinstance(round_info, dict):
                continue
            status = str(round_info.get("status") or "unknown")
            relevant_date = upcoming_relevant_date(round_info)
            if status == "closed" and not relevant_date:
                continue
            rows.append(
                {
                    "event_id": event.get("id"),
                    "event_title": event.get("title"),
                    "watch_id": event.get("watch_id"),
                    "watch_kind": event.get("watch_kind"),
                    "keyword": event.get("keyword"),
                    "platform": round_info.get("platform"),
                    "round_name": round_info.get("name"),
                    "status": status,
                    "relevant_date": relevant_date,
                    "url": round_info.get("url"),
                    "match_reasons": event.get("match_reasons", []),
                }
            )
    rows.sort(
        key=lambda row: (
            UPCOMING_STATUS_ORDER.get(str(row.get("status") or "unknown"), 99),
            str(row.get("relevant_date") or "9999-12-31"),
            str(row.get("event_title") or ""),
        )
    )
    return rows[:limit]


def recent_alerts(db_path: str, limit: int = 50) -> list[dict[str, object]]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        rows = connection.execute(
            """
            SELECT a.id, a.event_id, a.alert_type, a.payload_json, a.created_at,
                   e.canonical_title, w.id, w.keyword, w.kind, w.muted
            FROM alert_log a
            LEFT JOIN events e ON e.id = a.event_id
            LEFT JOIN watched_keywords w ON w.id = e.watch_id
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        alerts: list[dict[str, object]] = []
        for alert_id, event_id, alert_type, payload_json, created_at, event_title, watch_id, keyword, kind, muted in rows:
            payload = json.loads(payload_json)
            payload["alert_id"] = alert_id
            payload["event_id"] = event_id
            payload["created_at"] = created_at
            payload["alert_type"] = alert_type
            payload["event_title"] = event_title
            payload["watch_id"] = watch_id
            payload["watch_keyword"] = keyword
            payload["watch_kind"] = kind
            payload["watch_muted"] = bool(muted) if muted is not None else None
            alerts.append(payload)
        return alerts


def ics_escape(value: object) -> str:
    text = str(value or "")
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_date(value: str | None) -> str | None:
    parsed = parse_iso_date(value)
    return parsed.strftime("%Y%m%d") if parsed else None


def ics_dtstamp(generated_at: dt.datetime | None = None) -> str:
    stamp = generated_at or dt.datetime.now(dt.UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.UTC)
    return stamp.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def timeline_calendar_entries(db_path: str, include_muted_watches: bool = False) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for event in recent_events(db_path, limit=500, include_muted_watches=include_muted_watches):
        if event.get("watch_kind") != WATCH_KIND_EVENT:
            continue
        event_id = str(event.get("id") or "")
        title = str(event.get("title") or event.get("keyword") or "Tracked event")
        official_url = str(event.get("official_url") or "")
        for index, round_info in enumerate(event.get("rounds", [])):
            if not isinstance(round_info, dict):
                continue
            round_name = str(round_info.get("name") or "Ticket round")
            platform = str(round_info.get("platform") or "ticket")
            url = str(round_info.get("url") or official_url)
            label = f"{title} - {round_name}"
            application_start_text = str(round_info.get("application_start_at") or "")
            application_start = parse_iso_date(application_start_text)
            application_end = parse_iso_date(str(round_info.get("application_end_at") or ""))
            if application_start:
                entries.append(
                    {
                        "uid": stable_hash(f"{event_id}|{index}|application|{platform}|{round_name}|{application_start_text}"),
                        "summary": f"Lottery application: {label}",
                        "dtstart": application_start.strftime("%Y%m%d"),
                        "dtend": ((application_end or application_start) + dt.timedelta(days=1)).strftime("%Y%m%d"),
                        "description": f"Platform: {platform}\nStatus: {round_info.get('status') or 'unknown'}",
                        "url": url,
                    }
                )
            for field, prefix in (
                ("results_date", "Lottery results"),
                ("payment_end_at", "Payment due"),
                ("general_sale_date", "General sale"),
            ):
                parsed_date = parse_iso_date(str(round_info.get(field) or ""))
                if not parsed_date:
                    continue
                date_value = parsed_date.strftime("%Y%m%d")
                entries.append(
                    {
                        "uid": stable_hash(f"{event_id}|{index}|{field}|{platform}|{round_name}|{date_value}"),
                        "summary": f"{prefix}: {label}",
                        "dtstart": date_value,
                        "dtend": (parsed_date + dt.timedelta(days=1)).strftime("%Y%m%d"),
                        "description": f"Platform: {platform}\nStatus: {round_info.get('status') or 'unknown'}",
                        "url": url,
                    }
                )
    return entries


def render_calendar_ics(
    db_path: str,
    generated_at: dt.datetime | None = None,
    include_muted_watches: bool = False,
) -> str:
    stamp = ics_dtstamp(generated_at)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//chusennote//ticket timeline//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:chusennote ticket timeline",
    ]
    for entry in timeline_calendar_entries(db_path, include_muted_watches=include_muted_watches):
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{entry['uid']}@chusennote.local",
                f"DTSTAMP:{stamp}",
                f"SUMMARY:{ics_escape(entry['summary'])}",
                f"DTSTART;VALUE=DATE:{entry['dtstart']}",
                f"DTEND;VALUE=DATE:{entry['dtend']}",
                f"DESCRIPTION:{ics_escape(entry['description'])}",
            ]
        )
        if entry.get("url"):
            lines.append(f"URL:{ics_escape(entry['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def api_health(db_path: str) -> dict[str, object]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        artists = connection.execute(
            "SELECT COUNT(*) FROM watched_keywords WHERE muted = 0 AND kind = ?",
            (WATCH_KIND_ARTIST,),
        ).fetchone()[0]
        tracked_events = connection.execute(
            "SELECT COUNT(*) FROM watched_keywords WHERE muted = 0 AND kind = ?",
            (WATCH_KIND_EVENT,),
        ).fetchone()[0]
        saved_events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        sources = connection.execute(
            """
            SELECT COUNT(*)
            FROM watch_sources s
            JOIN watched_keywords w ON w.id = s.watch_id
            WHERE s.muted = 0 AND w.muted = 0
            """
        ).fetchone()[0]
        alerts = connection.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
    return {
        "app": "chusennote",
        "status": "ok",
        "schema_version": schema_version,
        "db_path": db_path,
        "tracked_artists": artists,
        "tracked_events": tracked_events,
        "saved_events": saved_events,
        "manual_sources": sources,
        "alerts": alerts,
    }


def event_detail(db_path: str, event_id: int) -> dict[str, object] | None:
    for event in recent_events(
        db_path,
        limit=500,
        include_muted_sources=True,
        include_muted_watches=True,
    ):
        if int(event["id"]) == event_id:
            return event
    return None


def web_source_link(url: object, label: str = "Open") -> str:
    return (
        f'<a class="action-link" href="{html.escape(str(url))}">{html.escape(label)}</a>'
        if is_web_url(url)
        else "<span>Source unavailable</span>"
    )


def render_artist_detail_page(db_path: str, artist_id: int) -> str:
    artist = next((watch for watch in list_watches(db_path, include_muted=True) if watch.id == artist_id and watch.kind == WATCH_KIND_ARTIST), None)
    if not artist:
        return "<!doctype html><title>Not found</title><h1>Artist not found</h1>"
    artist_events = [
        event
        for event in recent_events(db_path, limit=500, include_muted_sources=True, include_muted_watches=True)
        if int(event.get("watch_id") or 0) == artist.id
    ]
    artist_events.sort(key=lambda event: (first_event_sort_date(event) is None, first_event_sort_date(event) or dt.date.max, str(event.get("title") or "")))
    def render_artist_event_item(event: dict[str, object]) -> str:
        event_date = first_event_sort_date(event)
        venue_items = event.get("venues", [])
        venue_label = str(venue_items[0]) if isinstance(venue_items, list) and venue_items else "unknown"
        ticket_count = len(event.get("ticket_links", [])) if isinstance(event.get("ticket_links"), list) else 0
        round_count = len(event.get("rounds", [])) if isinstance(event.get("rounds"), list) else 0
        return f"""
        <li class="watch-row">
          <span>
            <a class="watch-title" href="/events/{html.escape(str(event.get('id')))}">{html.escape(str(event.get('title') or 'Untitled event'))}</a>
            <span class="watch-meta">
              <span class="mini-stat" title="Date">Date {html.escape(str(event_date or 'unknown'))}</span>
              <span class="mini-stat wide" title="Venue">Venue {html.escape(venue_label)}</span>
              <span class="mini-stat" title="Ticket links">Tickets {ticket_count}</span>
              <span class="mini-stat" title="Lottery rounds">Rounds {round_count}</span>
            </span>
          </span>
          <a class="action-link" href="/events/{html.escape(str(event.get('id')))}" title="Open event" aria-label="Open event">Open</a>
        </li>
        """

    event_items = "".join(render_artist_event_item(event) for event in artist_events) or "<li>No discovered events yet.</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(artist.keyword)}</title>
  <style>
    :root {{ --ink: #202126; --muted: #667085; --line: #d9dee8; --paper: #f6f7fb; --panel: #ffffff; --accent-strong: #9b2446; --green: #13795b; --blue: #315c9b; --shadow: 0 10px 28px rgba(28, 36, 52, 0.08); }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }}
    a {{ color: var(--accent-strong); font-weight: 850; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    header {{ background: rgba(255, 255, 255, 0.96); border-bottom: 1px solid var(--line); padding: 16px 24px; }}
    .topbar, main {{ max-width: 900px; margin: 0 auto; }}
    .topbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    main {{ padding: 28px 24px 56px; display: grid; gap: 18px; }}
    .back, .action-link {{ display: inline-flex; align-items: center; justify-content: center; min-width: 55px; min-height: 36px; padding: 7px 10px; border-radius: 8px; background: white; border: 1px solid var(--line); color: var(--ink); font-size: 13px; font-weight: 850; }}
    .back {{ min-width: 38px; width: 38px; padding: 0; }}
    section {{ border-top: 1px solid var(--line); padding: 18px 0 0; }}
    h1, h2 {{ margin-top: 0; letter-spacing: 0; }}
    small {{ display: block; color: var(--muted); line-height: 1.45; }}
    ul {{ min-width: 0; list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }}
    li {{ min-width: 0; display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 13px; box-shadow: var(--shadow); }}
    li span, li strong, li small {{ min-width: 0; overflow-wrap: anywhere; }}
    .watch-title {{ color: var(--ink); }}
    .watch-meta {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }}
    .mini-stat {{ display: inline-flex; align-items: center; max-width: 100%; min-height: 24px; padding: 4px 8px; border-radius: 8px; background: #fff3f6; border: 1px solid #efc2cd; color: #6d263a; font-size: 12px; font-weight: 900; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .mini-stat:nth-child(2) {{ background: #effbf5; border-color: #cfe9dc; color: var(--green); }}
    .mini-stat:nth-child(3) {{ background: #f3f7ff; border-color: #d5e2ff; color: var(--blue); }}
    .mini-stat.wide {{ max-width: min(100%, 360px); }}
    @media (max-width: 720px) {{ header {{ padding: 12px 16px; }} main {{ padding: 18px 16px 42px; }} li {{ flex-direction: column; }} }}
  </style>
</head>
<body>
  <header><div class="topbar"><a class="back" href="/" title="Back" aria-label="Back">‹</a><strong>{html.escape(artist.keyword)}</strong></div></header>
  <main>
    <section>
      <h1>{html.escape(artist.keyword)}</h1>
      <small>{len(artist_events)} discovered events sorted by date</small>
    </section>
    <section>
      <h2>Events</h2>
      <ul>{event_items}</ul>
    </section>
  </main>
</body>
</html>"""


def infer_event_location(venues: Sequence[str]) -> str:
    for venue in venues:
        text = clean_text(str(venue)).strip(" ：:")
        text = re.sub(r"^(?:会\s*場|Venue)\s*", "", text, flags=re.IGNORECASE).strip(" ：:")
        parenthetical = re.search(r"[（(]([^）)]+)[）)]", text)
        if parenthetical:
            location = clean_text(parenthetical.group(1))
            if location and len(location) <= 24:
                return location
        region = re.match(r"(東京|大阪|名古屋|京都|福岡|札幌|仙台|静岡|広島|群馬|神奈川|埼玉|千葉|兵庫|愛知|北海道|全国)\s+", text)
        if region:
            return region.group(1)
    return clean_text(str(venues[0])).strip(" ：:") if venues else ""


def render_event_detail_page(db_path: str, event_id: int) -> str:
    event = event_detail(db_path, event_id)
    if not event:
        return "<!doctype html><title>Not found</title><h1>Event not found</h1>"
    event_dates = [clean_text(str(item)) for item in event.get("event_dates", []) if clean_text(str(item))]
    venues = [clean_text(str(item)) for item in event.get("venues", []) if clean_text(str(item))]
    time_label = "; ".join(event_dates[:3]) if event_dates else "Unknown"
    venue_label = "; ".join(venues[:3]) if venues else "Unknown"
    location_label = infer_event_location(venues) or "Unknown"
    ticket_link_items = "".join(
        f"""
        <li>
          <span><strong>{html.escape(str(link.get('label') or link.get('platform') or 'Ticket link'))}</strong>
          <small>{html.escape(str(link.get('platform') or 'unknown'))} · confidence {html.escape(str(link.get('confidence') or 'unknown'))}</small></span>
          {web_source_link(link.get('url'), 'Open')}
        </li>
        """
        for link in event.get("ticket_links", [])
    ) or "<li>No ticket links saved yet.</li>"
    round_items = "".join(
        f"""
        <article class="round-card">
          <div class="round-head">
            <h3>{html.escape(str(ticket.get('name') or 'Ticket round'))}</h3>
            <span class="status">{html.escape(str(ticket.get('status') or 'unknown'))}</span>
          </div>
          <div class="fact-grid">
            <div><small>Platform</small><strong>{html.escape(str(ticket.get('platform') or 'unknown'))}</strong></div>
            <div><small>Lottery opens</small><strong>{html.escape(str(ticket.get('application_start_at') or 'unknown'))}</strong></div>
            <div><small>Lottery closes</small><strong>{html.escape(str(ticket.get('application_end_at') or 'unknown'))}</strong></div>
            <div><small>Results</small><strong>{html.escape(str(ticket.get('results_date') or 'unknown'))}</strong></div>
            <div><small>Payment due</small><strong>{html.escape(str(ticket.get('payment_end_at') or 'unknown'))}</strong></div>
            <div><small>On sale</small><strong>{html.escape(str(ticket.get('general_sale_date') or 'unknown'))}</strong></div>
          </div>
          <p><small>Type: {html.escape(str(ticket.get('round_type') or 'unknown'))} · membership: {html.escape(str(ticket.get('membership_required') or 'unknown'))} · confidence {html.escape(str(ticket.get('confidence') or 'unknown'))}</small></p>
          <p><small>Evidence: {html.escape(str(ticket.get('evidence') or 'none'))}</small></p>
          {web_source_link(ticket.get('url'), 'Open source')}
        </article>
        """
        for ticket in event.get("rounds", [])
    ) or "<p>No lottery rounds saved yet.</p>"
    manual_source_items = "".join(
        f"""
        <li>
          <span><strong>{html.escape(str(source.get('label')))}</strong>
          <small>{html.escape('private note' if source.get('private_note') else str(source.get('platform')))}</small></span>
          {web_source_link(source.get('url'))}
        </li>
        """
        for source in event.get("manual_sources", [])
    ) or "<li>No manual sources.</li>"
    official = event.get("official_url") or ""
    official_link = web_source_link(official, "Open") if is_web_url(official) else "<span>Unavailable</span>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(str(event.get('title') or 'Event'))}</title>
  <style>
    :root {{ --ink: #202126; --muted: #667085; --line: #d9dee8; --paper: #f6f7fb; --panel: #ffffff; --accent-strong: #9b2446; --green: #13795b; --blue: #315c9b; --shadow: 0 10px 28px rgba(28, 36, 52, 0.08); }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }}
    a {{ color: var(--accent-strong); font-weight: 850; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    header {{ background: rgba(255, 255, 255, 0.96); border-bottom: 1px solid var(--line); padding: 16px 24px; }}
    .topbar, main {{ max-width: 1040px; margin: 0 auto; }}
    .topbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    main {{ padding: 28px 24px 56px; display: grid; gap: 18px; }}
    .back, .action-link {{ display: inline-flex; align-items: center; justify-content: center; min-width: 55px; min-height: 36px; padding: 7px 10px; border-radius: 8px; background: white; border: 1px solid var(--line); color: var(--ink); font-size: 13px; font-weight: 850; }}
    .back {{ min-width: 38px; width: 38px; padding: 0; }}
    .hero {{ border-bottom: 1px solid var(--line); padding-bottom: 18px; }}
    section {{ min-width: 0; border-top: 1px solid var(--line); padding-top: 18px; }}
    h1, h2, h3, p {{ margin-top: 0; letter-spacing: 0; }}
    h1 {{ margin-bottom: 10px; font-size: clamp(28px, 4vw, 40px); line-height: 1.08; }}
    h2 {{ margin-bottom: 12px; font-size: 22px; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 8px; background: #e9f9f1; color: var(--green); font-size: 12px; font-weight: 900; }}
    .summary-grid, .fact-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 10px; }}
    .summary-grid > div, .fact-grid > div {{ min-width: 0; padding: 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); }}
    .summary-grid strong, .fact-grid strong {{ display: block; min-width: 0; overflow-wrap: anywhere; }}
    small {{ display: block; color: var(--muted); line-height: 1.45; }}
    ul {{ min-width: 0; list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }}
    li {{ min-width: 0; display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 13px; box-shadow: var(--shadow); }}
    li span, li strong, li small {{ min-width: 0; overflow-wrap: anywhere; }}
    .rounds {{ display: grid; gap: 12px; }}
    .round-card {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; box-shadow: var(--shadow); }}
    .round-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; margin-bottom: 10px; }}
    @media (max-width: 720px) {{ header {{ padding: 12px 16px; }} main {{ padding: 18px 16px 42px; }} .summary-grid, .fact-grid {{ grid-template-columns: 1fr; }} li {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header><div class="topbar"><a class="back" href="/" title="Back" aria-label="Back">‹</a><span class="status">{html.escape(str(event.get('status') or 'watching'))}</span></div></header>
  <main>
    <div class="hero">
      <h1>{html.escape(str(event.get('title') or 'Untitled event'))}</h1>
      <div class="summary-grid">
        <div><small>Official page</small>{official_link}</div>
        <div><small>Updated</small><strong>{html.escape(str(event.get('updated_at') or 'unknown'))}</strong></div>
        <div><small>Watch keyword</small><strong>{html.escape(str(event.get('keyword') or 'unknown'))}</strong></div>
      </div>
    </div>
    <section>
      <h2>General Info</h2>
      <div class="summary-grid">
        <div><small>Location</small><strong>{html.escape(location_label)}</strong></div>
        <div><small>Time</small><strong>{html.escape(time_label)}</strong></div>
        <div><small>Venue</small><strong>{html.escape(venue_label)}</strong></div>
      </div>
    </section>
    <section><h2>Ticket Links</h2><ul>{ticket_link_items}</ul></section>
    <section><h2>Lottery Rounds</h2><div class="rounds">{round_items}</div></section>
    <section><h2>Manual Sources</h2><ul>{manual_source_items}</ul></section>
  </main>
</body>
</html>"""


def event_identity_key(info: EventInfo) -> str:
    venue = normalize_round_name(" ".join(info.venues[:2]))
    date = normalize_round_name(" ".join(info.event_dates[:2]))
    return stable_hash("|".join((normalize_round_name(info.title or info.keyword), venue, date, info.official_page or "")))


def upsert_event(
    connection: sqlite3.Connection,
    watch_id: int,
    info: EventInfo,
    rounds: Sequence[TicketRound],
    now: str,
) -> tuple[int, bool]:
    official_url = info.official_page or f"keyword:{info.keyword}"
    existing = connection.execute(
        "SELECT id FROM events WHERE watch_id = ? AND official_url = ?",
        (watch_id, official_url),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO events(watch_id, canonical_title, official_url, summary, event_dates_json, venues_json, status, event_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(watch_id, official_url) DO UPDATE SET
            canonical_title = excluded.canonical_title,
            summary = excluded.summary,
            event_dates_json = excluded.event_dates_json,
            venues_json = excluded.venues_json,
            status = excluded.status,
            event_key = excluded.event_key,
            updated_at = excluded.updated_at
        """,
        (
            watch_id,
            info.title or info.keyword,
            official_url,
            info.summary,
            json.dumps(list(info.event_dates), ensure_ascii=False),
            json.dumps(list(info.venues), ensure_ascii=False),
            compute_event_status(info, rounds, parse_iso_date(now)),
            event_identity_key(info),
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT id FROM events WHERE watch_id = ? AND official_url = ?",
        (watch_id, official_url),
    ).fetchone()
    return int(row[0]), existing is None


def delete_stale_keyword_fallback_events(connection: sqlite3.Connection, watch_id: int, current_event_id: int) -> None:
    rows = connection.execute(
        """
        SELECT id FROM events
        WHERE watch_id = ? AND id != ? AND official_url LIKE 'keyword:%'
        """,
        (watch_id, current_event_id),
    ).fetchall()
    for row in rows:
        event_id = int(row[0])
        for table in ("sources", "ticket_rounds", "snapshots", "alert_log"):
            connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))
        connection.execute("DELETE FROM events WHERE id = ?", (event_id,))


def cleanup_database(db_path: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        counts = {
            "sources": 0,
            "ticket_rounds": 0,
            "snapshots": 0,
            "alert_log": 0,
            "watch_sources": 0,
            "keyword_fallback_events": 0,
        }
        for table in ("sources", "ticket_rounds", "snapshots", "alert_log"):
            cursor = connection.execute(
                f"""
                DELETE FROM {table}
                WHERE event_id NOT IN (SELECT id FROM events)
                """
            )
            counts[table] += cursor.rowcount if cursor.rowcount >= 0 else 0
        cursor = connection.execute(
            """
            DELETE FROM watch_sources
            WHERE watch_id NOT IN (SELECT id FROM watched_keywords)
            """
        )
        counts["watch_sources"] += cursor.rowcount if cursor.rowcount >= 0 else 0

        stale_fallbacks = connection.execute(
            """
            SELECT fallback.id
            FROM events AS fallback
            WHERE fallback.official_url LIKE 'keyword:%'
              AND EXISTS (
                SELECT 1 FROM events AS official
                WHERE official.watch_id = fallback.watch_id
                  AND official.id != fallback.id
                  AND (
                    official.official_url LIKE 'http://%'
                    OR official.official_url LIKE 'https://%'
                  )
              )
            """
        ).fetchall()
        for row in stale_fallbacks:
            event_id = int(row[0])
            for table in ("sources", "ticket_rounds", "snapshots", "alert_log"):
                cursor = connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))
                counts[table] += cursor.rowcount if cursor.rowcount >= 0 else 0
            connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
            counts["keyword_fallback_events"] += 1
        return counts


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
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, url) DO UPDATE SET
                label = excluded.label,
                platform = excluded.platform,
                confidence = excluded.confidence,
                provenance = excluded.provenance,
                updated_at = excluded.updated_at
            """,
            (
                event_id,
                link.url,
                link.label,
                source_name_for_url(link.url),
                source_confidence(link),
                source_provenance(link.url, link.label),
                now,
                now,
            ),
        )
        if existing is None:
            alerts.append({"type": "new_ticket_link", "label": link.label, "url": link.url})
    return alerts


def ticket_round_key(ticket: TicketRound) -> str:
    normalized = normalize_ticket_round(ticket)
    return stable_hash(
        "|".join(
            (
                normalized.platform or normalized.source,
                normalized.url,
                normalize_round_name(normalized.name),
                normalized.application_start_at or "",
                normalized.general_sale_date or "",
            )
        )
    )


def ticket_round_fields(ticket: TicketRound) -> dict[str, str | None]:
    normalized = normalize_ticket_round(ticket)
    return {
        "lottery_start": normalized.application_start_at or normalized.lottery_start,
        "lottery_end": normalized.application_end_at or normalized.lottery_end,
        "results_date": normalized.results_date,
        "general_sale_date": normalized.general_sale_date,
        "payment_deadline": normalized.payment_end_at or normalized.payment_deadline,
        "payment_start_at": normalized.payment_start_at,
        "trade_start_at": normalized.trade_start_at,
        "trade_end_at": normalized.trade_end_at,
    }


def parse_iso_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def compute_ticket_status(ticket: TicketRound, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    application_start = parse_iso_date(ticket.application_start_at or ticket.lottery_start)
    application_end = parse_iso_date(ticket.application_end_at or ticket.lottery_end)
    results_date = parse_iso_date(ticket.results_date)
    payment_end = parse_iso_date(ticket.payment_end_at or ticket.payment_deadline)
    general_sale = parse_iso_date(ticket.general_sale_date)

    if results_date == today:
        return "results_today"
    if payment_end and 0 <= (payment_end - today).days <= 1:
        return "payment_due"
    if general_sale and 0 <= (general_sale - today).days <= 2:
        return "general_sale_soon"
    if general_sale and today > general_sale:
        return "closed"
    if application_start and application_end and application_start <= today <= application_end:
        if (application_end - today).days <= 2:
            return "closing_soon"
        return "open"
    if application_start and today < application_start:
        return "upcoming"
    if application_end and today > application_end:
        return "closed"
    return "unknown"


def compute_event_status(info: EventInfo, rounds: Sequence[TicketRound], today: dt.date | None = None) -> str:
    if any(normalize_ticket_round(ticket, today).status in {"open", "closing_soon"} for ticket in rounds):
        return "lottery_open"
    if rounds:
        return "lottery_found"
    if info.ticket_links:
        return "ticket_links_found"
    if info.official_page:
        return "official_found"
    return "watching"


def normalize_ticket_round(ticket: TicketRound, today: dt.date | None = None) -> TicketRound:
    platform = ticket.platform or ticket.source or source_name_for_url(ticket.url)
    application_start = ticket.application_start_at or ticket.lottery_start
    application_end = ticket.application_end_at or ticket.lottery_end
    payment_end = ticket.payment_end_at or ticket.payment_deadline
    normalized = dataclasses.replace(
        ticket,
        round_number=ticket.round_number if ticket.round_number is not None else parse_round_number(ticket.name),
        platform=platform,
        application_start_at=application_start,
        application_end_at=application_end,
        payment_end_at=payment_end,
        confidence=ticket.confidence or platform_confidence(platform),
        round_type=ticket.round_type if ticket.round_type != "unknown" else infer_round_type(ticket.name),
        membership_required=(
            ticket.membership_required
            if ticket.membership_required != "unknown"
            else infer_membership_required(ticket.name, ticket.evidence)
        ),
    )
    return dataclasses.replace(normalized, status=compute_ticket_status(normalized, today))


def dedupe_ticket_rounds(rounds: Sequence[TicketRound], today: dt.date | None = None) -> tuple[TicketRound, ...]:
    deduped: list[TicketRound] = []
    seen: set[tuple[str, str, str, str | None, str | None, str | None, str | None]] = set()
    for ticket in rounds:
        normalized = normalize_ticket_round(ticket, today)
        key = (
            normalized.platform or normalized.source,
            normalized.url,
            normalize_round_name(normalized.name),
            normalized.application_start_at,
            normalized.application_end_at,
            normalized.results_date,
            normalized.general_sale_date,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return tuple(deduped)


def date_alert(
    alert_type: str,
    event_title: str,
    ticket: TicketRound,
    field: str,
    target_date: dt.date,
    today: dt.date,
) -> dict[str, str]:
    days = (target_date - today).days
    return {
        "type": alert_type,
        "event": event_title,
        "round": ticket.name,
        "source": ticket.source,
        "date": target_date.isoformat(),
        "days_until": str(days),
        "url": ticket.url,
        "field": field,
    }


def lifecycle_alerts_for_round(event_title: str, ticket: TicketRound, today: dt.date) -> list[dict[str, str]]:
    ticket = normalize_ticket_round(ticket, today)
    alerts: list[dict[str, str]] = []
    lottery_start = parse_iso_date(ticket.application_start_at or ticket.lottery_start)
    lottery_end = parse_iso_date(ticket.application_end_at or ticket.lottery_end)
    results_date = parse_iso_date(ticket.results_date)
    general_sale_date = parse_iso_date(ticket.general_sale_date)
    payment_deadline = parse_iso_date(ticket.payment_end_at or ticket.payment_deadline)

    if lottery_start and lottery_start == today:
        alerts.append(date_alert("lottery_opened", event_title, ticket, "lottery_start", lottery_start, today))
    if lottery_end and 0 <= (lottery_end - today).days <= 2:
        alerts.append(date_alert("lottery_closing_soon", event_title, ticket, "lottery_end", lottery_end, today))
    if results_date and results_date == today:
        alerts.append(date_alert("results_today", event_title, ticket, "results_date", results_date, today))
    if payment_deadline and 0 <= (payment_deadline - today).days <= 1:
        alerts.append(date_alert("payment_due_soon", event_title, ticket, "payment_deadline", payment_deadline, today))
    if general_sale_date and 0 <= (general_sale_date - today).days <= 2:
        alerts.append(date_alert("general_sale_soon", event_title, ticket, "general_sale_date", general_sale_date, today))
    return alerts


def record_lifecycle_alerts(
    connection: sqlite3.Connection,
    event_id: int,
    event_title: str,
    rounds: Sequence[TicketRound],
    now: str,
) -> list[dict[str, str]]:
    today = parse_iso_date(now) or dt.date.today()
    alerts: list[dict[str, str]] = []
    for ticket in rounds:
        for alert in lifecycle_alerts_for_round(event_title, ticket, today):
            key = stable_hash("|".join((alert["type"], ticket_round_key(ticket), alert["field"], alert["date"])))
            payload = json.dumps(alert, ensure_ascii=False, sort_keys=True)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO alert_log(event_id, alert_key, alert_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, key, alert["type"], payload, now),
            )
            if cursor.rowcount:
                alerts.append(alert)
    return alerts


def upsert_ticket_rounds(
    connection: sqlite3.Connection,
    event_id: int,
    event_title: str,
    rounds: Sequence[TicketRound],
    now: str,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for ticket in dedupe_ticket_rounds(rounds, parse_iso_date(now)):
        ticket = normalize_ticket_round(ticket, parse_iso_date(now))
        round_key = ticket_round_key(ticket)
        previous = connection.execute(
            """
            SELECT lottery_start, lottery_end, results_date, general_sale_date, payment_deadline,
                   payment_start_at, trade_start_at, trade_end_at
            FROM ticket_rounds
            WHERE event_id = ? AND round_key = ?
            """,
            (event_id, round_key),
        ).fetchone()
        fields = ticket_round_fields(ticket)
        connection.execute(
            """
            INSERT INTO ticket_rounds(
                event_id, round_key, source, url, name, round_number, platform,
                lottery_start, lottery_end, results_date, general_sale_date, payment_deadline,
                application_start_at, application_end_at, payment_start_at, payment_end_at,
                trade_start_at, trade_end_at, confidence, status, round_type, membership_required,
                evidence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, round_key) DO UPDATE SET
                source = excluded.source,
                url = excluded.url,
                name = excluded.name,
                round_number = excluded.round_number,
                platform = excluded.platform,
                lottery_start = excluded.lottery_start,
                lottery_end = excluded.lottery_end,
                results_date = excluded.results_date,
                general_sale_date = excluded.general_sale_date,
                payment_deadline = excluded.payment_deadline,
                application_start_at = excluded.application_start_at,
                application_end_at = excluded.application_end_at,
                payment_start_at = excluded.payment_start_at,
                payment_end_at = excluded.payment_end_at,
                trade_start_at = excluded.trade_start_at,
                trade_end_at = excluded.trade_end_at,
                confidence = excluded.confidence,
                status = excluded.status,
                round_type = excluded.round_type,
                membership_required = excluded.membership_required,
                evidence = excluded.evidence,
                updated_at = excluded.updated_at
            """,
            (
                event_id,
                round_key,
                ticket.source,
                ticket.url,
                ticket.name,
                ticket.round_number,
                ticket.platform,
                ticket.lottery_start,
                ticket.lottery_end,
                ticket.results_date,
                ticket.general_sale_date,
                ticket.payment_deadline,
                ticket.application_start_at,
                ticket.application_end_at,
                ticket.payment_start_at,
                ticket.payment_end_at,
                ticket.trade_start_at,
                ticket.trade_end_at,
                ticket.confidence,
                ticket.status,
                ticket.round_type,
                ticket.membership_required,
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


def save_blocks(db_path: str, blocks: AppBlocks, now: str | None = None, watch_id: int | None = None) -> list[dict[str, str]]:
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        info = blocks.general_info
        watch_id = watch_id or upsert_keyword(connection, info.keyword, timestamp)
        normalized_rounds = dedupe_ticket_rounds(blocks.ticket_info, parse_iso_date(timestamp))
        event_id, new_event = upsert_event(connection, watch_id, info, normalized_rounds, timestamp)
        if info.official_page and is_web_url(info.official_page):
            delete_stale_keyword_fallback_events(connection, watch_id, event_id)
        event_title = info.title or info.keyword
        alerts: list[dict[str, str]] = []
        if new_event and info.official_page:
            alerts.append({"type": "new_official_page", "event": event_title, "url": info.official_page})
        alerts.extend(upsert_sources(connection, event_id, info.ticket_links, timestamp))
        alerts.extend(upsert_ticket_rounds(connection, event_id, event_title, normalized_rounds, timestamp))
        alerts.extend(record_lifecycle_alerts(connection, event_id, event_title, normalized_rounds, timestamp))
        save_snapshot(connection, event_id, dataclasses.replace(blocks, ticket_info=normalized_rounds), timestamp)
        return alerts


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    cleaned_argv, session_log, session_log_dir = split_session_log_args(argv)
    if cleaned_argv and cleaned_argv[0] not in {"search", "watch", "artist", "event", "export", "web", "db", "-h", "--help"}:
        parser = argparse.ArgumentParser(description="Search Japanese event ticket lotteries by keyword.")
        parser.add_argument("keyword", help="Artist, event, or musical keyword to search for")
        parser.add_argument("--json", action="store_true", help="Output the two app blocks as JSON")
        parser.add_argument("--db", default=None, help="SQLite database path for saving watch/event/ticket history")
        parser.add_argument("--alerts-json", action="store_true", help="With --db, output only detected alert changes as JSON")
        parser.set_defaults(command="legacy")
        args = parser.parse_args(cleaned_argv)
        args.session_log = session_log
        args.session_log_dir = session_log_dir
        return args

    parser = argparse.ArgumentParser(description="Monitor Japanese event ticket lotteries.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search a single keyword")
    search_parser.add_argument("keyword", help="Artist, event, or musical keyword to search for")
    search_parser.add_argument("--db", default=None, help="SQLite database path for saving search results")
    search_parser.add_argument("--json", action="store_true", help="Output the two app blocks as JSON")
    search_parser.add_argument("--alerts-json", action="store_true", help="With --db, output only detected alert changes as JSON")

    watch_parser = subparsers.add_parser("watch", help="Manage and run watched keywords")
    watch_subparsers = watch_parser.add_subparsers(dest="watch_command", required=True)

    add_parser = watch_subparsers.add_parser("add", help="Add a keyword to the watchlist")
    add_parser.add_argument("keyword")
    add_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--regions", default="")
    add_parser.add_argument("--venues", default="")

    list_parser = watch_subparsers.add_parser("list", help="List watched keywords")
    list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    list_parser.add_argument("--json", action="store_true")
    list_parser.add_argument("--include-muted", action="store_true")

    remove_parser = watch_subparsers.add_parser("remove", help="Remove a watch by id or keyword")
    remove_parser.add_argument("identifier")
    remove_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    for watch_toggle in ("mute", "unmute"):
        watch_toggle_parser = watch_subparsers.add_parser(watch_toggle, help=f"{watch_toggle.title()} a watch by id or keyword")
        watch_toggle_parser.add_argument("identifier")
        watch_toggle_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    run_parser = watch_subparsers.add_parser("run", help="Run all active watched keywords")
    run_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    run_parser.add_argument("--alerts-json", action="store_true")

    loop_parser = watch_subparsers.add_parser("loop", help="Run active watches repeatedly in the foreground")
    loop_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    loop_parser.add_argument("--interval-minutes", type=non_negative_int, default=60)
    loop_parser.add_argument("--kind", choices=WATCH_KINDS, default=WATCH_KIND_EVENT)
    loop_parser.add_argument("--alerts-json", action="store_true")
    loop_parser.add_argument("--max-runs", type=positive_int)
    loop_parser.add_argument("--run-immediately", action=argparse.BooleanOptionalAction, default=True)
    loop_parser.add_argument("--stop-after-errors", type=positive_int)

    for command_name, kind, help_text in (
        ("artist", WATCH_KIND_ARTIST, "Manage tracked artists with basic event info"),
        ("event", WATCH_KIND_EVENT, "Manage tracked events with ticket and lottery info"),
    ):
        kind_parser = subparsers.add_parser(command_name, help=help_text)
        kind_parser.set_defaults(kind=kind)
        kind_subparsers = kind_parser.add_subparsers(dest="kind_command", required=True)

        kind_add_parser = kind_subparsers.add_parser("add", help=f"Add a tracked {kind}")
        kind_add_parser.add_argument("keyword")
        kind_add_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
        kind_add_parser.add_argument("--tags", default="")
        kind_add_parser.add_argument("--regions", default="")
        kind_add_parser.add_argument("--venues", default="")
        kind_add_parser.add_argument("--alerts", default=DEFAULT_ALERT_PREFERENCES)

        kind_list_parser = kind_subparsers.add_parser("list", help=f"List tracked {kind}s")
        kind_list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
        kind_list_parser.add_argument("--json", action="store_true")
        kind_list_parser.add_argument("--include-muted", action="store_true")

        kind_remove_parser = kind_subparsers.add_parser("remove", help=f"Remove a tracked {kind}")
        kind_remove_parser.add_argument("identifier")
        kind_remove_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

        for kind_toggle in ("mute", "unmute"):
            kind_toggle_parser = kind_subparsers.add_parser(kind_toggle, help=f"{kind_toggle.title()} a tracked {kind}")
            kind_toggle_parser.add_argument("identifier")
            kind_toggle_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

        kind_run_parser = kind_subparsers.add_parser("run", help=f"Run all active tracked {kind}s")
        kind_run_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
        kind_run_parser.add_argument("--alerts-json", action="store_true")

    source_parser = watch_subparsers.add_parser("source", help="Manage manual source URLs for a watch")
    source_subparsers = source_parser.add_subparsers(dest="source_command", required=True)

    source_add_parser = source_subparsers.add_parser("add", help="Add a manual source URL")
    source_add_parser.add_argument("watch")
    source_add_parser.add_argument("url")
    source_add_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    source_add_parser.add_argument("--label", default="")
    source_add_parser.add_argument("--private-note", action="store_true", help="Store the URL/note without scraping it")

    source_list_parser = source_subparsers.add_parser("list", help="List manual source URLs")
    source_list_parser.add_argument("watch", nargs="?")
    source_list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    source_list_parser.add_argument("--json", action="store_true")
    source_list_parser.add_argument("--include-muted", action="store_true")

    source_remove_parser = source_subparsers.add_parser("remove", help="Remove a manual source by id or URL")
    source_remove_parser.add_argument("identifier")
    source_remove_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    for source_toggle in ("mute", "unmute"):
        source_toggle_parser = source_subparsers.add_parser(source_toggle, help=f"{source_toggle.title()} a manual source by id or URL")
        source_toggle_parser.add_argument("identifier")
        source_toggle_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    web_parser = subparsers.add_parser("web", help="Run the local web UI")
    web_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    web_parser.add_argument("--port", type=int, default=8765, help="Local port to serve on")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")

    db_parser = subparsers.add_parser("db", help="Maintain the local SQLite database")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    cleanup_parser = db_subparsers.add_parser("cleanup", help="Remove stale fallback and orphaned rows")
    cleanup_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    cleanup_parser.add_argument("--json", action="store_true")

    export_parser = subparsers.add_parser("export", help="Export saved data")
    export_parser.add_argument("target", choices=("events", "alerts", "artists", "tracked-events", "sources", "calendar", "upcoming"))
    export_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    export_parser.add_argument("--json", action="store_true", default=True)
    export_parser.add_argument("--include-muted", action="store_true", help="Include muted watches or embedded manual sources where supported")

    args = parser.parse_args(cleaned_argv)
    args.session_log = session_log
    args.session_log_dir = session_log_dir
    return args


def watches_to_json(watches: Sequence[Watch]) -> str:
    return json.dumps([dataclasses.asdict(watch) for watch in watches], ensure_ascii=False, indent=2)


def watch_sources_to_json(sources: Sequence[WatchSource]) -> str:
    return json.dumps([dataclasses.asdict(source) for source in sources], ensure_ascii=False, indent=2)


def render_watches(watches: Sequence[Watch]) -> str:
    if not watches:
        return "No active watches."
    lines = ["# Watchlist", ""]
    for watch in watches:
        checked = watch.last_checked_at or "never"
        muted_label = " [muted]" if watch.muted else ""
        lines.append(f"- {watch.id}: {watch.keyword}{muted_label} (last checked: {checked})")
    return "\n".join(lines)


def render_watch_sources(sources: Sequence[WatchSource]) -> str:
    if not sources:
        return "No manual sources."
    lines = ["# Manual sources", ""]
    for source in sources:
        mode = "private note" if source.private_note else source.platform
        muted_label = " [muted]" if source.muted else ""
        lines.append(f"- {source.id}: watch {source.watch_id} · {source.label}{muted_label} ({mode}) {source.url}")
    return "\n".join(lines)


def json_response(handler: http.server.BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: http.server.BaseHTTPRequestHandler, body: str, content_type: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def html_response(handler: http.server.BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_form(handler: http.server.BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8") if length else ""
    parsed = urllib.parse.parse_qs(raw)
    return {key: values[0] for key, values in parsed.items() if values}


def redirect_response(handler: http.server.BaseHTTPRequestHandler, location: str = "/") -> None:
    handler.send_response(303)
    handler.send_header("Location", location)
    handler.end_headers()


def add_watch_from_form(db_path: str, form: dict[str, str]) -> Watch:
    return add_watch(
        db_path,
        clean_text(form.get("keyword", "")),
        kind=form.get("kind", WATCH_KIND_EVENT),
        tags=form.get("tags", ""),
        preferred_regions=form.get("regions", ""),
        preferred_venues=form.get("venues", ""),
        alert_preferences=form.get("alerts", DEFAULT_ALERT_PREFERENCES),
    )


def render_watch_preferences(watch: Watch, include_alerts: bool = False) -> str:
    parts = [
        f"tags {watch.tags or 'none'}",
        f"regions {watch.preferred_regions or 'none'}",
        f"venues {watch.preferred_venues or 'none'}",
    ]
    if include_alerts:
        parts.append(f"alerts {watch.alert_preferences or 'none'}")
    parts.append(f"last checked {watch.last_checked_at or 'never'}")
    return " | ".join(parts)


def render_web_page(
    db_path: str,
    event_search_keyword: str = "",
    event_search_results: Sequence[SearchResult] = (),
    event_search_error: str = "",
) -> str:
    watches = list_watches(db_path, include_muted=True)
    events = recent_events(db_path)
    upcoming_rows = upcoming_priority_rows(db_path, limit=6)
    latest_event_by_watch_id = {
        int(event["watch_id"]): event
        for event in reversed(events)
        if event.get("watch_kind") == WATCH_KIND_EVENT and event.get("watch_id")
    }
    active_artist_watches = [watch for watch in watches if not watch.muted and watch.kind == WATCH_KIND_ARTIST]
    active_event_watches = [watch for watch in watches if not watch.muted and watch.kind == WATCH_KIND_EVENT]
    artist_items = "\n".join(
        f"""
        <li class="watch-row">
          <span class="watch-copy"><a class="watch-title" href="/artists/{watch.id}" title="Open artist events">{html.escape(watch.keyword)}</a> <small>#{watch.id} | checked {html.escape(watch.last_checked_at or 'never')}</small></span>
          <span class="row-actions"><a class="action-link" href="/artists/{watch.id}" title="Open artist events" aria-label="Open artist events">Open</a><form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><button class="icon-button danger" title="Remove artist" aria-label="Remove artist"><span aria-hidden="true">x</span></button></form></span>
        </li>
        """
        for watch in active_artist_watches
    ) or '<li class="empty-row">No tracked artists.</li>'
    def render_tracked_event_item(watch: Watch) -> str:
        event = latest_event_by_watch_id.get(watch.id)
        if not event:
            return f"""
        <li class="watch-row">
          <span class="watch-copy"><strong>{html.escape(watch.keyword)}</strong> <small>#{watch.id} | not searched yet</small></span>
          <form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><button class="icon-button danger" title="Remove event" aria-label="Remove event"><span aria-hidden="true">x</span></button></form>
        </li>
        """
        detail_url = f"/events/{html.escape(str(event.get('id')))}"
        ticket_count = len(event.get("ticket_links", [])) if isinstance(event.get("ticket_links"), list) else 0
        round_count = len(event.get("rounds", [])) if isinstance(event.get("rounds"), list) else 0
        date_items = event.get("event_dates", [])
        date_label = str(date_items[0]) if isinstance(date_items, list) and date_items else "no date"
        official_label = "Official" if is_web_url(event.get("official_url")) else "No official"
        return f"""
        <li class="watch-row event-row">
          <span class="watch-copy">
            <a class="watch-title event-title" href="{detail_url}" title="Open event details">{html.escape(str(event.get('title') or watch.keyword))}</a>
            <small>{html.escape(watch.keyword)}</small>
            <span class="watch-meta">
              <span class="mini-stat" title="Official page">{html.escape(official_label)}</span>
              <span class="mini-stat" title="Ticket links">Tickets {ticket_count}</span>
              <span class="mini-stat" title="Lottery rounds">Rounds {round_count}</span>
              <span class="mini-stat wide" title="First date clue">Date {html.escape(date_label)}</span>
            </span>
          </span>
          <span class="row-actions"><a class="action-link" href="{detail_url}" title="Open event details" aria-label="Open event details">Open</a><form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><button class="icon-button danger" title="Remove event" aria-label="Remove event"><span aria-hidden="true">x</span></button></form></span>
        </li>
        """

    tracked_event_items = "\n".join(render_tracked_event_item(watch) for watch in active_event_watches) or '<li class="empty-row">No tracked events.</li>'
    event_result_items = "\n".join(
        f"""
        <li class="watch-row">
          <span class="watch-copy"><strong>{html.escape(result.title or result.url)}</strong><small>{html.escape(result.url)}</small></span>
          <form method="post" action="/event/add">
            <input type="hidden" name="keyword" value="{html.escape(event_search_keyword)}">
            <input type="hidden" name="title" value="{html.escape(result.title)}">
            <input type="hidden" name="url" value="{html.escape(result.url)}">
            <input type="hidden" name="snippet" value="{html.escape(result.snippet)}">
            <button class="secondary-button" title="Add exact event" aria-label="Add exact event">Add</button>
          </form>
        </li>
        """
        for result in event_search_results
    )
    event_search_panel = ""
    if event_search_error:
        event_search_panel = f'<p class="message">{html.escape(event_search_error)}</p>'
    elif event_search_keyword:
        event_search_panel = f"""
        <div class="results-panel">
          <div class="subhead"><span>Results</span><small>{html.escape(event_search_keyword)}</small></div>
          <ul>{event_result_items or '<li class="empty-row">No matching event pages found.</li>'}</ul>
        </div>
        """
    upcoming_items = "\n".join(
        f"""
        <li class="watch-row attention-row">
          <span class="watch-copy">
            <a class="watch-title" href="/events/{html.escape(str(row.get('event_id')))}">{html.escape(str(row.get('event_title') or 'Untitled event'))}</a>
            <small>{html.escape(str(row.get('keyword') or 'unknown'))}</small>
            <span class="watch-meta">
              <span class="mini-stat" title="Ticket status">{html.escape(str(row.get('status') or 'unknown'))}</span>
              <span class="mini-stat" title="Relevant date">Date {html.escape(str(row.get('relevant_date') or 'unknown'))}</span>
              <span class="mini-stat" title="Platform">{html.escape(str(row.get('platform') or 'unknown'))}</span>
              <span class="mini-stat wide" title="Round">{html.escape(str(row.get('round_name') or 'Ticket round'))}</span>
            </span>
          </span>
          <span class="row-actions"><a class="action-link" href="/events/{html.escape(str(row.get('event_id')))}" title="Open event details" aria-label="Open event details">Open</a></span>
        </li>
        """
        for row in upcoming_rows
    ) or '<li class="empty-row">No ticket rounds need attention.</li>'
    upcoming_label = "round" if len(upcoming_rows) == 1 else "rounds"
    active_dashboard_tab = "events" if event_search_keyword or event_search_error else "attention"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>chusennote</title>
  <style>
    :root {{
      --ink: #202126;
      --muted: #667085;
      --line: #d9dee8;
      --paper: #f6f7fb;
      --panel: #ffffff;
      --accent: #d94f70;
      --accent-strong: #9b2446;
      --green: #13795b;
      --blue: #315c9b;
      --shadow: 0 10px 28px rgba(28, 36, 52, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--paper);
      color: var(--ink);
    }}
    a {{ color: var(--accent-strong); font-weight: 850; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .watch-title {{ color: var(--ink); }}
    header {{
      background: rgba(255, 255, 255, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 16px 24px;
    }}
    .topbar {{
      max-width: 1180px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .brand {{ display: flex; gap: 10px; align-items: center; color: var(--ink); font-size: 22px; font-weight: 950; }}
    .brand:hover {{ text-decoration: none; }}
    .brand-mark {{
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: var(--accent);
      color: white;
      box-shadow: 0 8px 20px rgba(155, 36, 70, 0.2);
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 56px; }}
    .dashboard-intro {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-end;
      margin-bottom: 22px;
    }}
    .dashboard-intro h1 {{ margin: 0; font-size: 30px; line-height: 1.1; }}
    .dashboard-intro p {{ margin: 6px 0 0; color: var(--muted); font-weight: 650; }}
    .summary-strip {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }}
    .summary-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 32px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font-size: 13px;
      font-weight: 850;
      white-space: nowrap;
    }}
    .dashboard-tabs {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 0 0 18px;
    }}
    .tab-button {{
      min-height: 54px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      box-shadow: var(--shadow);
      text-align: left;
    }}
    .tab-button:hover {{ transform: translateY(-1px); }}
    .tab-button[aria-selected="true"] {{
      border-color: #e7b6c5;
      background: #fff3f6;
      color: var(--accent-strong);
    }}
    .tab-button .tab-label {{ font-weight: 950; }}
    .tab-button .tab-count {{
      min-width: 28px;
      min-height: 28px;
      display: inline-grid;
      place-items: center;
      padding: 3px 8px;
      border-radius: 8px;
      background: var(--paper);
      color: var(--ink);
      font-size: 12px;
      font-weight: 950;
    }}
    .dashboard-panel {{ display: none; }}
    .dashboard-panel.is-active {{ display: block; }}
    section {{
      min-width: 0;
      padding: 0;
    }}
    .section-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }}
    h1, h2 {{ margin: 0; letter-spacing: 0; }}
    h2 {{ font-size: 20px; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 8px; background: #e9f9f1; color: var(--green); font-size: 12px; font-weight: 900; white-space: nowrap; }}
    form {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)) auto; gap: 8px; align-items: center; }}
    input {{
      min-width: 0;
      min-height: 42px;
      padding: 10px 12px;
      border: 1px solid #cfd6e3;
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }}
    input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(217, 79, 112, 0.16); outline: none; }}
    input[name="keyword"] {{ grid-column: 1 / -2; }}
    button {{
      min-height: 40px;
      padding: 10px 14px;
      border: 1px solid var(--accent-strong);
      border-radius: 8px;
      background: var(--accent-strong);
      color: white;
      cursor: pointer;
      font-weight: 850;
      font: inherit;
    }}
    button:hover {{ transform: translateY(-1px); }}
    .secondary-button {{
      min-width: 64px;
      background: #fff;
      color: var(--accent-strong);
      border-color: #e7b6c5;
      box-shadow: none;
    }}
    .icon-button {{
      width: 42px;
      min-width: 42px;
      padding: 0;
      display: inline-grid;
      place-items: center;
      font-size: 22px;
      line-height: 1;
    }}
    .icon-button.soft {{ background: #fff; border-color: #cfd6e3; color: var(--ink); }}
    .icon-button.danger {{ background: #fff; border-color: #efc2cd; color: #b9284a; box-shadow: none; }}
    .action-link {{
      min-height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 7px 10px;
      border: 1px solid #cfd6e3;
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font-size: 13px;
      font-weight: 850;
    }}
    .action-link:hover {{ text-decoration: none; transform: translateY(-1px); }}
    .button-text {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
    ul {{ list-style: none; padding: 0; margin: 14px 0 0; display: grid; gap: 10px; }}
    .results-panel {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }}
    .subhead {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; font-weight: 900; }}
    .message {{ margin: 12px 0 0; color: #b9284a; font-weight: 800; }}
    li {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 13px;
      box-shadow: var(--shadow);
    }}
    .watch-row {{ align-items: flex-start; }}
    .event-row {{ min-height: 108px; }}
    .attention-row {{ min-height: 96px; }}
    .empty-row {{ color: var(--muted); font-weight: 700; box-shadow: none; }}
    .watch-copy {{ min-width: 0; display: grid; gap: 4px; }}
    .event-title {{
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    small {{ color: var(--muted); line-height: 1.45; }}
    li form {{ display: block; flex: 0 0 auto; }}
    .row-actions {{ display: inline-flex; gap: 8px; align-items: center; }}
    .watch-meta {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }}
    .mini-stat {{
      display: inline-flex;
      align-items: center;
      max-width: 100%;
      min-height: 24px;
      padding: 4px 8px;
      border-radius: 8px;
      background: #fff3f6;
      border: 1px solid #efc2cd;
      color: #6d263a;
      font-size: 12px;
      font-weight: 900;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .mini-stat:nth-child(2) {{ background: #effbf5; border-color: #cfe9dc; color: var(--green); }}
    .mini-stat:nth-child(3) {{ background: #f3f7ff; border-color: #d5e2ff; color: var(--blue); }}
    .mini-stat.wide {{ max-width: min(100%, 320px); }}
    li .icon-button {{ width: 34px; min-width: 34px; min-height: 34px; font-size: 18px; }}
    .run-form {{ display: flex; margin-top: 10px; }}
    .run-form button {{ width: auto; min-width: 92px; padding: 0 12px; font-size: 14px; }}
    @media (max-width: 820px) {{
      header {{ padding: 12px 16px; }}
      main {{ padding: 18px 16px 42px; }}
      .dashboard-intro {{ align-items: flex-start; flex-direction: column; }}
      .summary-strip {{ justify-content: flex-start; }}
      .dashboard-tabs {{ grid-template-columns: 1fr; }}
      form {{ grid-template-columns: 1fr auto; }}
      input {{ grid-column: 1 / -1; }}
      input[name="keyword"] {{ grid-column: 1 / 2; }}
      li {{ align-items: flex-start; flex-direction: column; }}
      .row-actions {{ width: 100%; justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <a class="brand" href="/"><span class="brand-mark">cn</span><span>chusennote</span></a>
    </div>
  </header>
  <main>
    <div class="dashboard-intro">
      <div>
        <h1>Watch dashboard</h1>
        <p>Track official pages, ticket links, and lottery rounds from one place.</p>
      </div>
      <div class="summary-strip" aria-label="Dashboard summary">
        <span class="summary-pill">{len(active_artist_watches)} active artists</span>
        <span class="summary-pill">{len(active_event_watches)} active events</span>
        <span class="summary-pill">{len(events)} saved events</span>
      </div>
    </div>
    <div class="dashboard-tabs" role="tablist" aria-label="Dashboard sections">
      <button class="tab-button" type="button" role="tab" aria-selected="{'true' if active_dashboard_tab == 'attention' else 'false'}" aria-controls="panel-attention" id="tab-attention" data-tab-target="attention"><span class="tab-label">Attention</span><span class="tab-count">{len(upcoming_rows)}</span></button>
      <button class="tab-button" type="button" role="tab" aria-selected="{'true' if active_dashboard_tab == 'artists' else 'false'}" aria-controls="panel-artists" id="tab-artists" data-tab-target="artists"><span class="tab-label">Artists</span><span class="tab-count">{len(active_artist_watches)}</span></button>
      <button class="tab-button" type="button" role="tab" aria-selected="{'true' if active_dashboard_tab == 'events' else 'false'}" aria-controls="panel-events" id="tab-events" data-tab-target="events"><span class="tab-label">Events</span><span class="tab-count">{len(active_event_watches)}</span></button>
    </div>
    <section class="dashboard-panel {'is-active' if active_dashboard_tab == 'attention' else ''}" id="panel-attention" role="tabpanel" aria-labelledby="tab-attention" data-tab-panel="attention">
      <div class="section-head"><h2>Needs Attention</h2><span class="status">{len(upcoming_rows)} {upcoming_label}</span></div>
      <ul>{upcoming_items}</ul>
    </section>
    <section class="dashboard-panel {'is-active' if active_dashboard_tab == 'artists' else ''}" id="panel-artists" role="tabpanel" aria-labelledby="tab-artists" data-tab-panel="artists">
      <div class="section-head"><h2>Tracked Artists</h2><span class="status">{len(active_artist_watches)} active</span></div>
      <form method="post" action="/watch/add">
        <input type="hidden" name="kind" value="artist">
        <input name="keyword" placeholder="Artist" required>
        <button class="secondary-button" title="Add artist" aria-label="Add artist">Add</button>
      </form>
      <form class="run-form" method="post" action="/watch/run"><input type="hidden" name="kind" value="artist"><button class="secondary-button" title="Run artists" aria-label="Run artists">Run artists</button></form>
      <ul>{artist_items}</ul>
    </section>
    <section class="dashboard-panel {'is-active' if active_dashboard_tab == 'events' else ''}" id="panel-events" role="tabpanel" aria-labelledby="tab-events" data-tab-panel="events">
      <div class="section-head"><h2>Tracked Events</h2><span class="status">{len(active_event_watches)} active</span></div>
      <form method="post" action="/event/search">
        <input name="keyword" placeholder="Search exact event" value="{html.escape(event_search_keyword)}" required>
        <button class="secondary-button" title="Search events" aria-label="Search events">Search</button>
      </form>
      {event_search_panel}
      <form class="run-form" method="post" action="/watch/run"><input type="hidden" name="kind" value="event"><button class="secondary-button" title="Run events" aria-label="Run events">Run events</button></form>
      <ul>{tracked_event_items}</ul>
    </section>
  </main>
  <script>
    const tabButtons = Array.from(document.querySelectorAll('[data-tab-target]'));
    const tabPanels = Array.from(document.querySelectorAll('[data-tab-panel]'));
    function showDashboardTab(name) {{
      tabButtons.forEach((button) => {{
        button.setAttribute('aria-selected', String(button.dataset.tabTarget === name));
      }});
      tabPanels.forEach((panel) => {{
        panel.classList.toggle('is-active', panel.dataset.tabPanel === name);
      }});
    }}
    tabButtons.forEach((button) => {{
      button.addEventListener('click', () => showDashboardTab(button.dataset.tabTarget));
    }});
  </script>
</body>
</html>"""


def render_alert_item(alert: dict[str, object]) -> str:
    event_id = alert.get("event_id")
    event_text = html.escape(str(alert.get("event") or alert.get("event_title") or ""))
    event_link = (
        f'<a href="/events/{html.escape(str(event_id))}">{event_text}</a>'
        if event_id and event_text
        else event_text
    )
    watch_keyword = str(alert.get("watch_keyword") or "")
    watch_context = ""
    if watch_keyword:
        watch_kind = str(alert.get("watch_kind") or "watch")
        muted = " muted" if alert.get("watch_muted") is True else ""
        watch_context = f" <small>{html.escape(watch_kind)} {html.escape(watch_keyword)}{muted}</small>"
    elif alert.get("watch_id"):
        watch_context = f" <small>watch #{html.escape(str(alert.get('watch_id')))}</small>"
    return (
        f"<li><strong>{html.escape(str(alert.get('type', alert.get('alert_type', 'alert'))))}</strong> "
        f"{event_link} {html.escape(str(alert.get('round', '')))} "
        f"{watch_context} <small>{html.escape(str(alert.get('created_at', '')))}</small></li>"
    )


def render_event_card(event: dict[str, object], basic: bool = False) -> str:
    rounds = event.get("rounds", [])
    date_items = event.get("event_dates", [])
    venue_items = event.get("venues", [])
    date_text = "; ".join(str(item) for item in date_items[:2]) if isinstance(date_items, list) else ""
    venue_text = "; ".join(str(item) for item in venue_items[:2]) if isinstance(venue_items, list) else ""
    metadata = "".join(
        f"<p><small>{html.escape(label)}: {html.escape(value)}</small></p>"
        for label, value in (("Dates", date_text), ("Venues", venue_text))
        if value
    )
    reasons = event.get("match_reasons", [])
    reason_items = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in reasons[:4]) if isinstance(reasons, list) else ""
    reason_section = f'<ul class="reasons">{reason_items}</ul>' if reason_items else ""
    round_cards = "" if basic else "\n".join(
        f"""
        <div class="round">
          <strong>{html.escape(str(ticket.get('name') or 'Ticket round'))}</strong>
          <div><span class="status">{html.escape(str(ticket.get('status') or 'unknown'))}</span> {html.escape(str(ticket.get('platform') or 'unknown'))} · confidence {html.escape(str(ticket.get('confidence') or 'unknown'))}</div>
          <small>Apply: {html.escape(str(ticket.get('application_start_at') or 'unknown'))} to {html.escape(str(ticket.get('application_end_at') or 'unknown'))}</small><br>
          <small>Results: {html.escape(str(ticket.get('results_date') or 'unknown'))}</small><br>
          <small>Type: {html.escape(str(ticket.get('round_type') or 'unknown'))} · membership: {html.escape(str(ticket.get('membership_required') or 'unknown'))}</small><br>
          <small>Evidence: {html.escape(str(ticket.get('evidence') or 'none'))}</small><br>
          {f'<a href="{html.escape(str(ticket.get("url")))}">Source</a>' if is_web_url(ticket.get("url")) else '<span>Source unavailable</span>'}
        </div>
        """
        for ticket in rounds
    ) or "<p>No ticket rounds saved yet.</p>"
    official = event.get("official_url") or ""
    official_link = (
        f'<a href="{html.escape(str(official))}">Official page</a>'
        if is_web_url(official)
        else "<span>Official page unavailable</span>"
    )
    ticket_section = "" if basic else f'<div class="rounds">{round_cards}</div>'
    return f"""
    <article class="event">
      <h3><a href="/events/{html.escape(str(event.get('id')))}">{html.escape(str(event.get('title') or 'Untitled event'))}</a></h3>
      <p><span class="status">{html.escape(str(event.get('status') or 'watching'))}</span> {official_link} · <small>{html.escape(str(event.get('updated_at') or ''))}</small></p>
      {metadata}
      {reason_section}
      {ticket_section}
    </article>
    """


def make_web_handler(db_path: str) -> type[http.server.BaseHTTPRequestHandler]:
    class ChusennoteHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            query = urllib.parse.parse_qs(parsed_url.query)
            if path == "/":
                html_response(self, render_web_page(db_path))
            elif re.fullmatch(r"/artists/\d+", path):
                html_response(self, render_artist_detail_page(db_path, int(path.rsplit("/", 1)[1])))
            elif re.fullmatch(r"/events/\d+", path):
                html_response(self, render_event_detail_page(db_path, int(path.rsplit("/", 1)[1])))
            elif path == "/api/health":
                json_response(self, api_health(db_path))
            elif path == "/api/watchlist":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                json_response(self, [dataclasses.asdict(watch) for watch in list_watches(db_path, include_muted=include_muted)])
            elif path == "/api/events":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                json_response(
                    self,
                    recent_events(
                        db_path,
                        include_muted_sources=include_muted,
                        include_muted_watches=include_muted,
                    ),
                )
            elif path == "/api/upcoming":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                json_response(self, upcoming_priority_rows(db_path, include_muted_watches=include_muted))
            elif path == "/api/alerts":
                json_response(self, recent_alerts(db_path))
            elif path == "/api/sources":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                json_response(self, [dataclasses.asdict(source) for source in list_watch_sources(db_path, include_muted=include_muted)])
            elif path == "/calendar.ics":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                text_response(
                    self,
                    render_calendar_ics(db_path, include_muted_watches=include_muted),
                    "text/calendar; charset=utf-8",
                )
            else:
                json_response(self, {"error": "not found"}, status=404)

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            form = read_form(self)
            if path == "/watch/add":
                keyword = clean_text(form.get("keyword", ""))
                if not keyword:
                    json_response(self, {"error": "keyword is required"}, status=400)
                    return
                add_watch_from_form(db_path, form)
                redirect_response(self)
            elif path == "/watch/remove":
                remove_watch(db_path, form.get("identifier", ""))
                redirect_response(self)
            elif path == "/watch/unmute":
                set_watch_muted(db_path, form.get("identifier", ""), False)
                redirect_response(self)
            elif path == "/watch/run":
                run_watches(db_path, kind=form.get("kind") or None)
                redirect_response(self)
            elif path == "/event/search":
                keyword = clean_text(form.get("keyword", ""))
                if not keyword:
                    html_response(self, render_web_page(db_path, event_search_error="Keyword is required."))
                    return
                try:
                    results = search_web(keyword, limit=6)
                except (OSError, ValueError) as error:
                    html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_error=str(error)))
                    return
                html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_results=results))
            elif path == "/event/add":
                keyword = clean_text(form.get("keyword", ""))
                title = clean_text(form.get("title", ""))
                url = clean_text(form.get("url", ""))
                snippet = clean_text(form.get("snippet", ""))
                if not is_web_url(url):
                    html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_error="Pick a valid event page."))
                    return
                try:
                    blocks = build_exact_event_blocks(keyword or title, title, url, snippet)
                    save_blocks(db_path, blocks)
                except (OSError, ValueError) as error:
                    html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_error=str(error)))
                    return
                redirect_response(self)
            elif path == "/source/add":
                try:
                    add_watch_source(
                        db_path,
                        form.get("watch", ""),
                        form.get("url", ""),
                        form.get("label", ""),
                        bool(form.get("private_note")),
                    )
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                redirect_response(self)
            elif path == "/source/remove":
                remove_watch_source(db_path, form.get("identifier", ""))
                redirect_response(self)
            elif path == "/source/unmute":
                set_watch_source_muted(db_path, form.get("identifier", ""), False)
                redirect_response(self)
            elif path == "/api/watchlist":
                keyword = clean_text(form.get("keyword", ""))
                if not keyword:
                    json_response(self, {"error": "keyword is required"}, status=400)
                    return
                json_response(self, dataclasses.asdict(add_watch_from_form(db_path, form)))
            elif path == "/api/watchlist/remove":
                json_response(self, {"removed": remove_watch(db_path, form.get("identifier", ""))})
            elif path == "/api/watchlist/mute":
                json_response(self, {"muted": set_watch_muted(db_path, form.get("identifier", ""), True)})
            elif path == "/api/watchlist/unmute":
                json_response(self, {"unmuted": set_watch_muted(db_path, form.get("identifier", ""), False)})
            elif path == "/api/run":
                json_response(self, run_watches(db_path, kind=form.get("kind") or None))
            elif path == "/api/sources":
                try:
                    source = add_watch_source(
                        db_path,
                        form.get("watch", ""),
                        form.get("url", ""),
                        form.get("label", ""),
                        bool(form.get("private_note")),
                    )
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, dataclasses.asdict(source))
            elif path == "/api/sources/remove":
                json_response(self, {"removed": remove_watch_source(db_path, form.get("identifier", ""))})
            elif path == "/api/sources/mute":
                json_response(self, {"muted": set_watch_source_muted(db_path, form.get("identifier", ""), True)})
            elif path == "/api/sources/unmute":
                json_response(self, {"unmuted": set_watch_source_muted(db_path, form.get("identifier", ""), False)})
            else:
                json_response(self, {"error": "not found"}, status=404)

    return ChusennoteHandler


def create_web_server(db_path: str, port: int, host: str = "127.0.0.1") -> http.server.ThreadingHTTPServer:
    return http.server.ThreadingHTTPServer((host, port), make_web_handler(db_path))


def run_web(db_path: str, port: int, host: str = "127.0.0.1") -> None:
    server = create_web_server(db_path, port, host)
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    bind_note = f" (bound to {host})" if display_host != host else ""
    print(f"Serving chusennote at http://{display_host}:{server.server_port}{bind_note}")
    server.serve_forever()


def run_command(args: argparse.Namespace) -> int:
    if args.command == "web":
        run_web(args.db, args.port, args.host)
        return 0
    if args.command == "db":
        if args.db_command == "cleanup":
            counts = cleanup_database(args.db)
            if args.json:
                print(json.dumps(counts, ensure_ascii=False, indent=2))
            else:
                total = sum(counts.values())
                print(f"Database cleanup removed {total} rows.")
                for key, count in counts.items():
                    if count:
                        print(f"- {key}: {count}")
            return 0
    if args.command == "export":
        if args.target == "events":
            print(
                json.dumps(
                    recent_events(
                        args.db,
                        include_muted_sources=args.include_muted,
                        include_muted_watches=args.include_muted,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.target == "alerts":
            print(json.dumps(recent_alerts(args.db), ensure_ascii=False, indent=2))
        elif args.target == "artists":
            print(watches_to_json(list_watches(args.db, include_muted=args.include_muted, kind=WATCH_KIND_ARTIST)))
        elif args.target == "tracked-events":
            print(watches_to_json(list_watches(args.db, include_muted=args.include_muted, kind=WATCH_KIND_EVENT)))
        elif args.target == "sources":
            print(watch_sources_to_json(list_watch_sources(args.db, include_muted=args.include_muted)))
        elif args.target == "calendar":
            print(render_calendar_ics(args.db, include_muted_watches=args.include_muted), end="")
        elif args.target == "upcoming":
            print(json.dumps(upcoming_priority_rows(args.db, include_muted_watches=args.include_muted), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"artist", "event"}:
        if args.kind_command == "add":
            watch = add_watch(
                args.db,
                args.keyword,
                kind=args.kind,
                tags=args.tags,
                preferred_regions=args.regions,
                preferred_venues=args.venues,
                alert_preferences=args.alerts,
            )
            print(f"Added tracked {args.kind} {watch.id}: {watch.keyword}")
            return 0
        if args.kind_command == "list":
            watches = list_watches(args.db, include_muted=args.include_muted, kind=args.kind)
            print(watches_to_json(watches) if args.json else render_watches(watches))
            return 0
        if args.kind_command == "remove":
            removed = remove_watch(args.db, args.identifier)
            print(f"Removed tracked {args.kind}." if removed else f"Tracked {args.kind} not found.")
            return 0 if removed else 1
        if args.kind_command == "mute":
            muted = set_watch_muted(args.db, args.identifier, True)
            print(f"Muted tracked {args.kind}." if muted else f"Tracked {args.kind} not found.")
            return 0 if muted else 1
        if args.kind_command == "unmute":
            unmuted = set_watch_muted(args.db, args.identifier, False)
            print(f"Unmuted tracked {args.kind}." if unmuted else f"Tracked {args.kind} not found.")
            return 0 if unmuted else 1
        if args.kind_command == "run":
            alerts = run_watches(args.db, kind=args.kind)
            if args.alerts_json:
                print(json.dumps(alerts, ensure_ascii=False, indent=2))
            else:
                print(f"Ran {len(list_watches(args.db, kind=args.kind))} active tracked {args.kind}s; {len(alerts)} alerts.")
            return 0
    if args.command == "watch":
        if args.watch_command == "source":
            if args.source_command == "add":
                try:
                    source = add_watch_source(args.db, args.watch, args.url, args.label, args.private_note)
                except ValueError as error:
                    print(str(error))
                    return 1
                print(f"Added source {source.id}: {source.label}")
                return 0
            if args.source_command == "list":
                sources = list_watch_sources(args.db, args.watch, include_muted=args.include_muted)
                print(watch_sources_to_json(sources) if args.json else render_watch_sources(sources))
                return 0
            if args.source_command == "remove":
                removed = remove_watch_source(args.db, args.identifier)
                print("Removed source." if removed else "Source not found.")
                return 0 if removed else 1
            if args.source_command == "mute":
                muted = set_watch_source_muted(args.db, args.identifier, True)
                print("Muted source." if muted else "Source not found.")
                return 0 if muted else 1
            if args.source_command == "unmute":
                unmuted = set_watch_source_muted(args.db, args.identifier, False)
                print("Unmuted source." if unmuted else "Source not found.")
                return 0 if unmuted else 1
        if args.watch_command == "add":
            watch = add_watch(
                args.db,
                args.keyword,
                kind=WATCH_KIND_EVENT,
                tags=args.tags,
                preferred_regions=args.regions,
                preferred_venues=args.venues,
            )
            print(f"Added watch {watch.id}: {watch.keyword}")
            return 0
        if args.watch_command == "list":
            watches = list_watches(args.db, include_muted=args.include_muted)
            print(watches_to_json(watches) if args.json else render_watches(watches))
            return 0
        if args.watch_command == "remove":
            removed = remove_watch(args.db, args.identifier)
            print("Removed watch." if removed else "Watch not found.")
            return 0 if removed else 1
        if args.watch_command == "mute":
            muted = set_watch_muted(args.db, args.identifier, True)
            print("Muted watch." if muted else "Watch not found.")
            return 0 if muted else 1
        if args.watch_command == "unmute":
            unmuted = set_watch_muted(args.db, args.identifier, False)
            print("Unmuted watch." if unmuted else "Watch not found.")
            return 0 if unmuted else 1
        if args.watch_command == "run":
            alerts = run_watches(args.db)
            if args.alerts_json:
                print(json.dumps(alerts, ensure_ascii=False, indent=2))
            else:
                print(f"Ran {len(list_watches(args.db))} active watches; {len(alerts)} alerts.")
            return 0
        if args.watch_command == "loop":
            return run_watch_loop(
                args.db,
                interval_minutes=args.interval_minutes,
                kind=args.kind,
                alerts_json=args.alerts_json,
                max_runs=args.max_runs,
                run_immediately=args.run_immediately,
                stop_after_errors=args.stop_after_errors,
            )

    blocks = build_blocks(args.keyword)
    alerts: list[dict[str, str]] = []
    if args.db:
        alerts = save_blocks(args.db, blocks)
    if args.alerts_json:
        print(json.dumps(alerts, ensure_ascii=False, indent=2))
    else:
        print(blocks_to_json(blocks) if args.json else render_blocks(blocks))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv or sys.argv[1:])
    started_at = dt.datetime.now().astimezone()
    args = parse_args(raw_argv)
    exit_code = run_command(args)
    if args.session_log:
        append_session_log(raw_argv, args, exit_code, started_at)
    return exit_code


if __name__ == "__main__":
    configure_cli_stdio()
    raise SystemExit(main())
