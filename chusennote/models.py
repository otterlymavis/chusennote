"""Shared constants and dataclasses for chusennote.

This is a dependency-free leaf module: it imports nothing from the rest of the
project, so every other module can depend on it without risking import cycles.
"""

from __future__ import annotations

import dataclasses
import re


USER_AGENT = "chusennote/0.2 (+https://github.com/otterlymavis/chusennote; ticket lottery monitor)"
# A browser-like UA for the optional headless-browser fetch path, which renders
# JavaScript-heavy ticket platforms that the plain HTTP fetch cannot read.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Enable the headless-browser fetch path. "fallback"/"auto"/"1" render with a
# browser only when the plain fetch fails or returns a thin/empty-shell page;
# "always" renders every page; unset/"off" disables it (default). It stays
# opt-in because each render spawns Chromium, which would otherwise add a slow
# render to every dead or SPA link on every watch run.
BROWSER_FETCH_ENV = "CHUSENNOTE_BROWSER_FETCH"
BROWSER_TIMEOUT_MS = 30000
# Settle delay after DOMContentLoaded to let client-side JS render content.
BROWSER_SETTLE_MS = 2500
# Below this rendered-text length a page is treated as a JS shell worth a
# browser re-render in fallback mode.
BROWSER_MIN_TEXT_LENGTH = 400
# Empty-state placeholders that a JS shell serves before client-side rendering
# fills in real content (e.g. shiki.jp prints "公演スケジュール情報はありません" in
# its static HTML). A page carrying one of these is re-rendered in fallback mode
# even when it clears the length threshold. Generic "no data / coming soon"
# phrasing, so this catches the shape across sites rather than one event.
EMPTY_STATE_MARKERS = (
    "情報はありません",
    "情報がありません",
    "ただいま準備中",
    "準備中です",
    "coming soon",
)
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
DB_SCHEMA_VERSION = 7
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
    "pia": ("t.pia.jp", "ticket.pia.jp", "w.pia.jp"),
    "eplus": ("eplus.jp",),
    "lawson": ("l-tike.com",),
    "rakuten": ("r-t.jp", "ticket.rakuten.co.jp"),
    "ticketboard": ("ticketboard.jp", "tickebo.jp"),
    "cnplayguide": ("cnplayguide.com",),
    "e-get": ("e-get.jp",),
    "tv-asahi-ticket": ("ticket.tv-asahi.co.jp",),
}
SOCIAL_OR_NOISY_DOMAINS = (
    "x.com",
    "twitter.com",
    "instagram.com",
    "facebook.com",
    "line.me",
    "youtube.com",
    "tiktok.com",
    "pornhub.com",
    "xvideos.com",
    "xnxx.com",
    "wikipedia.org",
    # Streaming / encyclopaedic / profile sites: about an artist, never an event.
    "music.apple.com",
    "open.spotify.com",
    "spotify.com",
    "fandom.com",
    "kprofiles.com",
    "bilibili.tv",
    "bilibili.com",
    "genius.com",
    "last.fm",
    "discogs.com",
    "imdb.com",
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
    ticket_rules: tuple[str, ...] = ()
    ticket_prices: tuple[str, ...] = ()


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


# Notification subscriptions: how granular a user wants to be reminded.
#   artist_all     - every event/show discovered under an artist watch
#   event_all      - every location and round of a tracked event
#   event_location - only rounds/shows at one location (city) of an event
#   round          - a single named lottery round
NOTIFY_SCOPE_ARTIST_ALL = "artist_all"
NOTIFY_SCOPE_EVENT_ALL = "event_all"
NOTIFY_SCOPE_EVENT_LOCATION = "event_location"
NOTIFY_SCOPE_ROUND = "round"
NOTIFY_SCOPES = (
    NOTIFY_SCOPE_ARTIST_ALL,
    NOTIFY_SCOPE_EVENT_ALL,
    NOTIFY_SCOPE_EVENT_LOCATION,
    NOTIFY_SCOPE_ROUND,
)
NOTIFY_CHANNELS = ("feed", "email", "push")
DEFAULT_NOTIFY_CHANNELS = "feed"
# Remind ahead of and on each date: 7 days before, 1 day before, the day itself.
DEFAULT_LEAD_DAYS = (7, 1, 0)
# Push delivery (FCM) and email (SMTP) are configured through the environment.
FCM_SERVER_KEY_ENV = "CHUSENNOTE_FCM_SERVER_KEY"
SMTP_HOST_ENV = "CHUSENNOTE_SMTP_HOST"
SMTP_PORT_ENV = "CHUSENNOTE_SMTP_PORT"
SMTP_USER_ENV = "CHUSENNOTE_SMTP_USER"
SMTP_PASSWORD_ENV = "CHUSENNOTE_SMTP_PASSWORD"
SMTP_FROM_ENV = "CHUSENNOTE_SMTP_FROM"
NOTIFY_EMAIL_ENV = "CHUSENNOTE_NOTIFY_EMAIL"


@dataclasses.dataclass(frozen=True)
class NotificationSubscription:
    id: int
    watch_id: int
    scope: str
    location: str = ""
    round_key: str = ""
    channels: str = DEFAULT_NOTIFY_CHANNELS
    lead_days: str = "7,1,0"
    enabled: bool = True


@dataclasses.dataclass(frozen=True)
class DeviceToken:
    id: int
    token: str
    platform: str = "android"
    label: str = ""


@dataclasses.dataclass(frozen=True)
class User:
    id: int
    email: str
    created_at: str
