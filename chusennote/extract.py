"""Date, venue, and ticket-round extraction plus round normalization.

Pulls structured event/ticket facts out of fetched page text and normalizes
detected lottery rounds (status, platform, dedupe). Depends only on the
:mod:`chusennote.models` and :mod:`chusennote.util` leaf modules.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import urllib.parse
from collections.abc import Iterable, Sequence

from .models import (
    DATE_RE,
    EventInfo,
    Link,
    Page,
    RANGE_RE,
    ROUND_CONTEXT_HINTS,
    ROUND_LABEL_PATTERNS,
    TICKET_DOMAINS,
    TicketRound,
)
from .util import (
    clean_text,
    infer_membership_required,
    infer_round_type,
    is_actionable_ticket_link,
    normalize_round_name,
    parse_round_number,
    platform_confidence,
    source_name_for_url,
)


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
            context = clean_text(text[max(0, match.start() - 24) : min(len(text), match.end() + 80)])
            if venue_looks_noisy(venue, context):
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


def venue_looks_noisy(venue: str, context: str = "") -> bool:
    venue = clean_text(venue)
    context = clean_text(context)
    if any(noisy in context for noisy in ("交通アクセス", "駐車場", "公演スケジュール情報はありません")):
        return True
    return any(
        noisy in venue
        for noisy in (
            "チケット",
            "ご購入",
            "ご予約",
            "販売",
            "受付",
            "お問い合わせ",
            "お問合せ",
            "主催",
            "電話",
            "ぜひ",
            "グループ観劇",
            "座席料金",
            "座席図",
            "アクセス",
            "車いす",
            "現在",
            "スケジュール情報",
            "公演一覧",
            "Facebook",
            "LINE",
        )
    )


def extract_ticket_links(page: Page) -> tuple[Link, ...]:
    links: list[Link] = []
    seen: set[str] = set()
    for link in page.links:
        if is_actionable_ticket_link(link.url, link.label):
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
        summary_phrases = nearby_phrases(
            official.text,
            ("公演", "開催", "チケット", "料金", "S席", "A席", "未就学", "有償譲渡", "車椅子"),
            width=260,
            limit=6,
        )
        summary_parts: list[str] = []
        seen_summary_parts: set[str] = set()
        for part in (*summary_phrases, *extract_ticket_price_items(official.text), *extract_ticket_rule_items(official.text)):
            if part and part not in seen_summary_parts:
                summary_parts.append(part)
                seen_summary_parts.add(part)
        summary = " ".join(summary_parts) if summary_parts else official.text[:520]

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
    unicode_date_token = r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|20\d{2}年\s*\d{1,2}月\s*\d{1,2}日|\d{1,2}[./-]\d{1,2}|\d{1,2}月\s*\d{1,2}日)"
    unicode_range_re = re.compile(rf"(?P<start>{unicode_date_token})(?:(?!{unicode_date_token}).){{0,60}}(?:[〜～~–—]|から)(?:(?!{unicode_date_token}).){{0,60}}(?P<end>{unicode_date_token})")
    match = RANGE_RE.search(text) or unicode_range_re.search(text)
    if not match:
        dates = [date for date in (normalized_iso_date(m.group(0)) for m in DATE_RE.finditer(text)) if date]
        dates.extend(date for date in (normalized_iso_date(m.group(0)) for m in re.finditer(unicode_date_token, text)) if date and date not in dates)
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


ADVANCE_RANGE_LABELS = (
    "受付期間",
    "申込期間",
    "申込み期間",
    "申込受付期間",
    "抽選申込期間",
    "抽選受付期間",
    "抽選先行",
    "先行抽選エントリー",
    "先着先行",
    "先行先着販売",
    "ゴールド会員",
    "レギュラー会員",
    "å…ˆç€å…ˆè¡Œ",
    "å…ˆè¡Œå…ˆç€è²©å£²",
    "ã‚´ãƒ¼ãƒ«ãƒ‰ä¼šå“¡",
    "ãƒ¬ã‚®ãƒ¥ãƒ©ãƒ¼ä¼šå“¡",
)


MEMBERSHIP_RANGE_LABELS = (
    "ゴールド会員",
    "レギュラー会員",
    "シルバー会員",
    "プレミアム会員",
    "FC会員",
    "ファンクラブ会員",
    "有料会員",
    "無料会員",
    "非会員",
    "会員登録なし",
    "会員登録不要",
)


def membership_rounds_from_context(
    context: str,
    base_name: str,
    source: str,
    url: str,
    results_date: str | None = None,
    general_sale_date: str | None = None,
    payment_deadline: str | None = None,
) -> tuple[TicketRound, ...]:
    unicode_date_token = r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|20\d{2}年\s*\d{1,2}月\s*\d{1,2}日|\d{1,2}[./-]\d{1,2}|\d{1,2}月\s*\d{1,2}日)"
    label_pattern = "|".join(re.escape(label) for label in MEMBERSHIP_RANGE_LABELS)
    range_pattern = re.compile(
        rf"(?P<label>{label_pattern})[：:]\s*(?P<range>{unicode_date_token}(?:(?!{unicode_date_token}).){{0,60}}[〜～~–—](?:(?!{unicode_date_token}).){{0,60}}{unicode_date_token})"
    )
    rounds: list[TicketRound] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for match in range_pattern.finditer(context):
        label = clean_text(match.group("label"))
        start, end = extract_range(match.group("range"))
        name = f"{base_name} / {label}" if base_name and label not in base_name else label
        key = (name, start, end)
        if key in seen:
            continue
        seen.add(key)
        rounds.append(
            TicketRound(
                source=source,
                url=url,
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


ROUND_NAME_LABELS = (
    "追加公演・抽選先行",
    "追加公演・先着先行",
    "追加公演・一般発売",
    "追加公演・一般前売",
    "座席選択先行受付",
    "座席選択先行",
    "先行抽選エントリー",
    "先行抽選",
    "抽選先行",
    "先行先着販売",
    "先着先行受付",
    "先着先行",
    "先行先着",
    "会員先行予約",
    "四季の会会員先行",
    "オフィシャル先行",
    "オフィシャル抽選",
    "ファンクラブ先行",
    "プレリザーブ",
    "プレオーダー",
    "先行予約",
    "先行受付",
    "一般前売",
    "一般発売",
)

# Keywords that mark a genuine application or sale window. A date-bearing
# context that matches none of these (and carries no round label) is incidental
# noise — e.g. terms-of-service prose that merely mentions 抽選販売 — and must
# not become a round.
APPLICATION_SIGNAL_LABELS = ("受付期間", "申込期間", "申込受付", "エントリー", "受付開始", "お申し込み", "申込開始")
GENERAL_SALE_SIGNAL_LABELS = ("発売日", "一般発売", "一般前売", "発売開始", "販売開始")


def application_round_name(context: str) -> str | None:
    """A readable name for a labelled-but-untyped round, from its sale signal."""
    if any(label in context for label in APPLICATION_SIGNAL_LABELS):
        return "先行受付"
    if any(label in context for label in GENERAL_SALE_SIGNAL_LABELS):
        return "一般発売"
    return None


def round_name_from_context(context: str, fallback: str | None = None) -> str | None:
    """Name a round from the label that governs its dates.

    A context window often spans several rounds, so a fixed-priority scan can
    return a label that belongs to a *different* round (e.g. tagging a 抽選先行
    block as 先着先行). Instead, pick the label nearest to — and preferably
    before — the first date in the window, which is the one introducing it.
    Returns ``fallback`` (default ``None``) when no round label is present.
    """
    date_match = DATE_RE.search(context)
    date_pos = date_match.start() if date_match else len(context)

    spans: list[tuple[int, int, str]] = []
    numbered = re.search(r"第\s*[0-9０-９一二三四五六七八九十]+\s*次\s*(?:抽選)?\s*先行", context)
    if numbered:
        spans.append((numbered.start(), numbered.end(), clean_text(numbered.group(0))))
    for label in ROUND_NAME_LABELS:
        pos = context.find(label)
        if pos != -1:
            spans.append((pos, pos + len(label), label))
    if not spans:
        return fallback

    # Drop a label whose span sits entirely inside a more specific one so that
    # "追加公演・抽選先行" wins over the bare "抽選先行" it contains.
    candidates = [
        (start, name)
        for start, end, name in spans
        if not any(
            other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        )
    ]

    before = [(date_pos - start, name) for start, name in candidates if start <= date_pos]
    if before:
        return min(before)[1]
    return min((start - date_pos, name) for start, name in candidates)[1]


def extract_ticket_rounds(page: Page) -> tuple[TicketRound, ...]:
    contexts = context_windows(page.text, ROUND_LABEL_PATTERNS + ("受付期間", "申込期間", "抽選結果", "当落", "一般発売"))
    contexts = contexts + label_forward_contexts(page.text, ("先行先着販売", "先着先行"))
    contexts = contexts + context_windows(page.text, ROUND_CONTEXT_HINTS)
    rounds: list[TicketRound] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for context in contexts:
        start, end = extract_range_after_label(
            context,
            ADVANCE_RANGE_LABELS,
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
        # Require a round label or a real application/sale signal. A date that
        # carries neither is incidental noise (legal/terms prose), not a round.
        name = round_name_from_context(context) or application_round_name(context)
        if not name:
            continue
        if "一般発売" in name or "一般前売" in name:
            start, end = None, None
            membership_rounds = ()
        else:
            membership_rounds = membership_rounds_from_context(
                context,
                name,
                source_name_for_url(page.url),
                page.url,
                results_date=results_date,
                general_sale_date=general_sale_date,
                payment_deadline=payment_deadline,
            )
        if membership_rounds:
            for ticket in membership_rounds:
                key = (ticket.name, ticket.lottery_start, ticket.lottery_end)
                if key in seen:
                    continue
                seen.add(key)
                rounds.append(ticket)
            continue
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


def membership_rounds_from_ticket(ticket: TicketRound) -> tuple[TicketRound, ...]:
    if not ticket.evidence:
        return ()
    if "会員" in ticket.name or "/" in ticket.name:
        return ()
    if not any(label in ticket.name for label in ("先行", "先着")) or "抽選" in ticket.name:
        return ()
    base_name = ticket.name if ticket.name not in {"先行"} else round_name_from_context(ticket.evidence, ticket.name)
    rounds = membership_rounds_from_context(
        ticket.evidence,
        base_name,
        ticket.source,
        ticket.url,
        results_date=ticket.results_date,
        general_sale_date=ticket.general_sale_date,
        payment_deadline=ticket.payment_deadline,
    )
    return tuple(
        normalize_ticket_round(
            dataclasses.replace(
                round_,
                platform=ticket.platform,
                confidence=ticket.confidence,
                round_type=ticket.round_type,
                membership_required=ticket.membership_required,
            )
        )
        for round_ in rounds
    )


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


JP_PREFECTURES = (
    "北海道", "青森", "岩手", "宮城", "秋田", "山形", "福島", "茨城", "栃木", "群馬",
    "埼玉", "千葉", "東京", "神奈川", "新潟", "富山", "石川", "福井", "山梨", "長野",
    "岐阜", "静岡", "愛知", "三重", "滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山",
    "鳥取", "島根", "岡山", "広島", "山口", "徳島", "香川", "愛媛", "高知", "福岡",
    "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄",
)
VENUE_SUFFIX_HINTS = (
    "アリーナ", "ホール", "ドーム", "スタジアム", "スタジオ", "劇場", "会館", "公会堂",
    "体育館", "メッセ", "フォーラム", "ガーデン", "センター", "Zepp", "国際展示場",
    "サンプラザ", "ベイホール", "プラザ", "ピット", "ラウンジ",
)
SCHEDULE_LINK_HINTS = (
    "live", "tour", "schedule", "concert", "event", "公演", "ライブ", "ツアー",
    "スケジュール", "コンサート", "live-information", "liveinfo",
)
_VENUE_SUFFIX_RE = "|".join(re.escape(hint) for hint in VENUE_SUFFIX_HINTS)
_PREFECTURE_RE = "|".join(re.escape(name) for name in JP_PREFECTURES)


def tour_venue_from_window(window: str) -> str:
    """Pull a concise venue/city out of the text following a tour date."""
    window = clean_text(window)
    prefecture_venue = re.search(
        rf"(?:{_PREFECTURE_RE})\s*[・:：]?\s*[^\s、,，。]{{0,24}}?(?:{_VENUE_SUFFIX_RE})",
        window,
    )
    if prefecture_venue:
        return clean_text(prefecture_venue.group(0))
    venue = re.search(rf"[^\s、,，。]{{1,24}}?(?:{_VENUE_SUFFIX_RE})", window)
    if venue:
        return clean_text(venue.group(0))
    prefecture = re.search(rf"(?:{_PREFECTURE_RE})", window)
    return prefecture.group(0) if prefecture else ""


def extract_tour_dates(page: Page) -> tuple[dict[str, str], ...]:
    """Parse an artist live/tour schedule into individual (date, venue) shows."""
    text = page.text
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in DATE_RE.finditer(text):
        iso_date = normalized_iso_date(match.group(0))
        if not iso_date:
            continue
        venue = tour_venue_from_window(text[match.end() : match.end() + 50])
        if not venue:
            continue
        key = (iso_date, venue)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"date": iso_date, "date_text": clean_text(match.group(0)), "venue": venue})
    return tuple(entries)


def split_event_notes(text: str) -> list[str]:
    normalized = clean_text(text)
    parts = re.split(r"(?=※)|[。\n\r]+", normalized)
    return [part.strip(" ・:：。") for part in parts if part.strip(" ・:：。")]


def extract_ticket_rule_items(text: str, limit: int = 6) -> tuple[str, ...]:
    hints = ("未就学", "入場", "有償譲渡", "転売", "車椅子", "身分証", "本人確認", "禁止", "注意", "同意")
    items: list[str] = []
    seen: set[str] = set()
    for note in split_event_notes(text):
        if (
            "重要なお知らせ" in note
            or "＞＞" in note
            or ">>" in note
            or "クッキー" in note
            or "cookie" in note.lower()
        ):
            continue
        if items and note.startswith(("なお、", "なお ")):
            if note in items[-1]:
                continue
            merged = f"{items[-1]} {note}"
            items[-1] = merged
            seen.add(merged)
            continue
        if any(note in existing or existing in note for existing in items):
            continue
        if any(hint in note for hint in hints) and note not in seen:
            items.append(note)
            seen.add(note)
        if len(items) >= limit:
            break
    return tuple(items)


def extract_ticket_price_items(text: str, limit: int = 6) -> tuple[str, ...]:
    items: list[str] = []
    seen: set[str] = set()
    normalized = clean_text(text)
    tier_matches = list(re.finditer(r"(?:チケット\s*)?(S席|A席|Yシート(?:（[^）]+）)?|U-25(?:（[^）]+）)?)[：:]", normalized))
    for index, match in enumerate(tier_matches):
        start = match.start(1)
        end = tier_matches[index + 1].start(1) if index + 1 < len(tier_matches) else len(normalized)
        note = normalized[start:end]
        note = re.split(r"(?=＊＝|※|チケット販売|Tickets|News|Tour|Cast)", note)[0].strip(" ・:：。＊*")
        if note and re.search(r"\d[\d,]*\s*円", note) and note not in seen:
            items.append(note)
            seen.add(note)
        if len(items) >= limit:
            return tuple(items)
    if items:
        return tuple(items)
    price_pattern = re.compile(r"(?:チケット\s*)?(?:S席|A席|Yシート|U-25)[^。※\n\r]{0,260}?\d[\d,]*\s*円[^。※\n\r]{0,260}")
    for match in price_pattern.finditer(normalized):
        note = match.group(0).strip(" ・:：。")
        if note and note not in seen:
            items.append(note)
            seen.add(note)
        if len(items) >= limit:
            return tuple(items)
    if items:
        return tuple(items)
    for note in split_event_notes(text):
        has_price = bool(re.search(r"\d[\d,]*\s*円", note))
        if not has_price:
            continue
        note = re.sub(r"^.*?(?=(?:チケット|料金|S席|A席|Yシート|U-25))", "", note).strip()
        if note and note not in seen:
            items.append(note)
            seen.add(note)
        if len(items) >= limit:
            break
    return tuple(items)


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


def normalize_ticket_round(ticket: TicketRound, today: dt.date | None = None) -> TicketRound:
    platform = ticket.platform or ticket.source or source_name_for_url(ticket.url)
    application_start = ticket.application_start_at or ticket.lottery_start
    application_end = ticket.application_end_at or ticket.lottery_end
    if ticket.evidence and (not application_start or not application_end):
        evidence_start, evidence_end = extract_range_after_label(ticket.evidence, ADVANCE_RANGE_LABELS)
        application_start = application_start or evidence_start
        application_end = application_end or evidence_end
    if "一般発売" in ticket.name or "一般前売" in ticket.name:
        application_start, application_end = None, None
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


def ticket_round_latest_ordinal(ticket: TicketRound) -> int:
    """Latest known date on a round as an ordinal, for newest-first sorting."""
    candidates = (
        ticket.application_start_at or ticket.lottery_start,
        ticket.application_end_at or ticket.lottery_end,
        ticket.results_date,
        ticket.general_sale_date,
        ticket.payment_end_at or ticket.payment_deadline,
    )
    parsed = [parse_iso_date(value) for value in candidates if value]
    valid = [date for date in parsed if date]
    return max(valid).toordinal() if valid else 0


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
    deduped.sort(
        key=lambda ticket: (
            -ticket_round_latest_ordinal(ticket),
            ticket.platform or ticket.source or "",
            ticket.name or "",
        )
    )
    return tuple(deduped)
