"""Local web UI: HTML rendering and the standard-library HTTP server.

Renders the dashboard, artist/event detail pages, and JSON/ICS API responses,
and wires them into a ThreadingHTTPServer. Depends on the persistence and
discovery layers (:mod:`chusennote.crud`, :mod:`chusennote.read_models`,
:mod:`chusennote.pipeline`) plus the lower leaf modules.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import html
import http.cookies
import http.server
import ipaddress
import json
import os
import re
import urllib.parse
from collections.abc import Iterable, Sequence

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403
from .crud import *  # noqa: F401,F403
from .read_models import *  # noqa: F401,F403
from .pipeline import *  # noqa: F401,F403
from .notifications import *  # noqa: F401,F403
from .auth import *  # noqa: F401,F403
from .storage import is_postgres_url, resolve_target


WEB_SESSION_COOKIE = "chusennote_session"
TRUST_PROXY_HEADERS_ENV = "CHUSENNOTE_TRUST_PROXY_HEADERS"
FORM_BODY_LIMIT = 64 * 1024
FORM_FIELD_LIMIT = 100
QUERY_FIELD_LIMIT = 100
NOTIFICATION_LIMIT_MAX = 500
EVENT_SEARCH_LIMIT_MAX = 20


class FormBodyError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class QueryParameterError(ValueError):
    pass


def web_session_cookie(token: str, *, secure: bool, clear: bool = False) -> str:
    cookie = http.cookies.SimpleCookie()
    cookie[WEB_SESSION_COOKIE] = token
    morsel = cookie[WEB_SESSION_COOKIE]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    if secure:
        morsel["secure"] = True
    if clear:
        morsel["max-age"] = 0
    return morsel.OutputString()


def web_request_uses_https(handler: http.server.BaseHTTPRequestHandler) -> bool:
    trusted = os.environ.get(TRUST_PROXY_HEADERS_ENV, "").strip().lower() in {"1", "true", "yes"}
    forwarded_proto = handler.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return trusted and forwarded_proto == "https"


def web_request_allows_credentials(handler: http.server.BaseHTTPRequestHandler) -> bool:
    if web_request_uses_https(handler):
        return True
    host_header = handler.headers.get("Host", "")
    host = urllib.parse.urlsplit(f"//{host_header}").hostname
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return address.is_loopback or address.is_link_local or any(
            address in network
            for network in (
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
            )
        )
    return (
        address.is_loopback
        or address.is_link_local
        or address in ipaddress.ip_network("fc00::/7")
    )


def web_request_has_valid_origin(handler: http.server.BaseHTTPRequestHandler) -> bool:
    origin = handler.headers.get("Origin", "").strip()
    if not origin:
        return True
    parsed = urllib.parse.urlsplit(origin)
    expected_scheme = "https" if web_request_uses_https(handler) else "http"
    return parsed.scheme.lower() == expected_scheme and parsed.netloc.lower() == handler.headers.get("Host", "").lower()


def safe_web_redirect(value: str, fallback: str = "/") -> str:
    parsed = urllib.parse.urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parsed.scheme or parsed.netloc:
        return fallback
    if any(character in value for character in "\r\n"):
        return fallback
    return value


# One stylesheet for every page: system fonts, one accent, flat bordered lists.
PAGE_CSS = """
    :root { color-scheme: light dark; --ink: #1f2328; --muted: #667085; --line: #e4e7ec; --paper: #f7f7f8; --panel: #ffffff; --accent: #9b2446; --accent-soft: #fbeaf0; --on-accent: #ffffff; --danger: #b42318; }
    @media (prefers-color-scheme: dark) {
      :root { --ink: #e8eaed; --muted: #9aa3b2; --line: #2d3138; --paper: #141619; --panel: #1c1f24; --accent: #f29bb3; --accent-soft: #3a2029; --on-accent: #1c1f24; --danger: #ff9b91; }
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    header { border-bottom: 1px solid var(--line); background: var(--panel); }
    .topbar, main { max-width: 720px; margin: 0 auto; padding: 0 20px; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 56px; }
    .topbar nav { display: flex; align-items: center; gap: 16px; font-size: 15px; }
    .brand { color: var(--ink); font-size: 18px; font-weight: 700; }
    main { display: grid; grid-template-columns: minmax(0, 1fr); gap: 32px; padding-top: 28px; padding-bottom: 56px; }
    section { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }
    h1, h2, h3, p, dl { margin: 0; }
    h1 { font-size: 26px; line-height: 1.2; overflow-wrap: anywhere; }
    h2 { font-size: 17px; }
    h3 { font-size: 16px; }
    small, .meta, .muted { color: var(--muted); font-size: 14px; }
    small, .meta { display: block; }
    .meta span + span::before { content: " · "; }
    ul { list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }
    li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; overflow-wrap: anywhere; }
    li + li { border-top: 1px solid var(--line); }
    li > :first-child { min-width: 0; flex: 1 1 auto; }
    .empty-row { color: var(--muted); }
    .title { display: block; color: var(--ink); font-weight: 600; }
    .now { color: var(--accent); font-weight: 600; }
    .row-actions { display: flex; flex: 0 0 auto; align-items: center; gap: 4px; }
    .section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    form { margin: 0; }
    input:not([type="checkbox"]):not([type="hidden"]) { width: 100%; min-width: 0; min-height: 40px; padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--ink); font: inherit; }
    input:focus-visible, button:focus-visible, a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    button, .button { display: inline-flex; align-items: center; justify-content: center; min-height: 36px; padding: 6px 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--ink); font: inherit; font-size: 14px; font-weight: 500; white-space: nowrap; cursor: pointer; }
    button:hover, .button:hover { border-color: var(--muted); text-decoration: none; }
    button.primary { border-color: var(--accent); background: var(--accent); color: var(--on-accent); }
    button.quiet { min-height: 32px; padding: 4px 8px; border-color: transparent; background: none; color: var(--muted); }
    button.quiet:hover { border-color: transparent; color: var(--ink); }
    button.danger { color: var(--danger); }
    .message { color: var(--danger); }
    summary { cursor: pointer; color: var(--muted); font-size: 14px; }
    details[open] > summary { margin-bottom: 10px; }
    .panel { padding: 14px 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }
    .stack { display: grid; gap: 8px; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; }
    .inline-form { display: flex; gap: 8px; }
    .tabs { display: flex; gap: 24px; border-bottom: 1px solid var(--line); }
    .tab { min-height: 40px; padding: 0; border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: none; color: var(--muted); font-size: 16px; }
    .tab:hover { border-bottom-color: var(--line); }
    .tab[aria-selected="true"] { border-bottom-color: var(--accent); color: var(--ink); font-weight: 600; }
    .tab-count { margin-left: 6px; color: var(--muted); font-size: 13px; font-weight: 400; }
    .tab-panel { display: none; }
    .tab-panel.is-active { display: grid; }
    .facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 16px; }
    .facts dt { color: var(--muted); }
    .facts dd { margin: 0; overflow-wrap: anywhere; }
    .round { display: grid; gap: 6px; }
    .round-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
    .round-links { display: flex; flex-wrap: wrap; gap: 6px; }
    .subscription-row { flex-wrap: wrap; }
    .subscription-editor { flex-basis: 100%; }
    .subscription-editor form { display: grid; gap: 10px; }
    fieldset { margin: 0; padding: 0; border: 0; }
    legend { padding: 0; color: var(--muted); font-size: 14px; }
    .channel-options { display: flex; flex-wrap: wrap; gap: 6px 14px; }
    .lead-days { display: grid; gap: 4px; font-size: 14px; }
    .prose { line-height: 1.6; }
    .prose h1, .prose h2, .prose p, .prose ul { margin: 0 0 12px; }
    .prose h2 { margin-top: 24px; font-size: 18px; }
    .prose ul { padding-left: 20px; border: 0; background: none; list-style: disc; }
    .prose li { display: list-item; padding: 0; border: 0; }
    footer { display: flex; flex-wrap: wrap; gap: 4px 16px; color: var(--muted); font-size: 13px; }
    @media (max-width: 600px) {
      .topbar, main { padding-left: 16px; padding-right: 16px; }
      li { flex-wrap: wrap; }
      .inline-form { flex-direction: column; }
      .facts { grid-template-columns: minmax(0, 1fr); gap: 0; }
      .facts dd { margin-bottom: 8px; }
    }
"""

# Served as its own file so browsers fetch it once instead of with every page.
# The URL carries a content hash, so a changed stylesheet is a new URL and the
# old one can be cached forever.
PAGE_CSS_PATH = "/static/page.css"
PAGE_CSS_URL = f"{PAGE_CSS_PATH}?v={hashlib.sha256(PAGE_CSS.encode('utf-8')).hexdigest()[:12]}"

# Every page shares one top bar, so there is no per-page back button to keep
# in sync: Home and Settings are always one click away.
TOPBAR = '<a class="brand" href="/">chusennote</a><nav><a href="/">Home</a><a href="/settings">Settings</a></nav>'
URGENT_STATUSES = {"open", "closing_soon", "results_today", "payment_due", "trade_open", "trade_closing_soon"}
ISO_DATE_PATTERN = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")


def friendly_date(value: object, today: dt.date | None = None) -> str:
    """Show ``2026-10-26T23:59`` as ``Oct 26, 23:59``; the year only when it is not this year.

    Values that are not ISO dates (free-text clues such as ``10月下旬``) pass
    through unchanged.
    """
    text = clean_text(str(value or ""))
    match = ISO_DATE_PATTERN.fullmatch(text[:16])
    if not match:
        return text
    try:
        date = dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return text
    label = f"{date:%b} {date.day}"
    if date.year != (today or dt.date.today()).year:
        label += f", {date.year}"
    if match.group(4):
        label += f", {match.group(4)}:{match.group(5)}"
    return label


def status_html(status: object, label: object) -> str:
    text = clean_text(str(label or ""))
    if not text:
        return ""
    css_class = "now" if str(status or "") in URGENT_STATUSES else ""
    return f'<span class="{css_class}">{html.escape(text)}</span>' if css_class else f"<span>{html.escape(text)}</span>"


def meta_line(*parts: str) -> str:
    """Join already-escaped fragments with middots, dropping empty ones."""
    spans = "".join(part if part.startswith("<span") else f"<span>{part}</span>" for part in parts if part)
    return f'<span class="meta">{spans}</span>' if spans else ""


def render_page(title: str, main: str, script: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{PAGE_CSS_URL}">
</head>
<body>
  <header><div class="topbar">{TOPBAR}</div></header>
  <main>{main}</main>
{script}</body>
</html>"""

def web_source_link(url: object, label: str = "Open") -> str:
    return (
        f'<a class="button" href="{html.escape(str(url))}">{html.escape(label)}</a>'
        if is_web_url(url)
        else "<span>Source unavailable</span>"
    )


# Honest venue text (real venues, "Multiple cities" for a tour, else a dash) is
# shared with the JSON API so the web UI and the apps render venues identically.
artist_venue_label = venue_label


def render_artist_detail_page(db_path: str, artist_id: int, user_id: int = 0) -> str:
    artist = next((watch for watch in list_watches(db_path, include_muted=True, user_id=user_id) if watch.id == artist_id and watch.kind == WATCH_KIND_ARTIST), None)
    if not artist:
        return "<!doctype html><title>Not found</title><h1>Artist not found</h1>"
    artist_events = [
        event
        for event in recent_events(
            db_path,
            limit=500,
            include_muted_sources=True,
            include_muted_watches=True,
            user_id=user_id,
            source_user_id=user_id,
        )
        if int(event.get("watch_id") or 0) == artist.id
    ]
    artist_events.sort(key=lambda event: (first_event_sort_date(event) is None, first_event_sort_date(event) or dt.date.max, str(event.get("title") or "")))
    def render_artist_event_item(event: dict[str, object]) -> str:
        event_date = first_event_sort_date(event)
        venue_label = artist_venue_label(event)
        return f"""
        <li>
          <span>
            <a class="title" href="/events/{html.escape(str(event.get('id')))}">{html.escape(str(event.get('title') or 'Untitled event'))}</a>
            {meta_line(html.escape(friendly_date(event_date.isoformat())) if event_date else "Date TBA", html.escape(venue_label) if venue_label != "—" else "")}
          </span>
        </li>
        """

    event_items = "".join(render_artist_event_item(event) for event in artist_events) or '<li class="empty-row">No events found yet.</li>'
    return render_page(
        artist.keyword,
        f"""
    <section>
      <div class="section-head">
        <h1>{html.escape(artist.keyword)}</h1>
        {subscribe_button(artist.id, NOTIFY_SCOPE_ARTIST_ALL, "Notify me", redirect=f"/artists/{artist.id}")}
      </div>
      <ul>{event_items}</ul>
    </section>
  """,
    )


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


def format_evidence_snippet(value: object, limit: int = 180) -> str:
    text = clean_text(str(value or ""))
    text = re.sub(r"※【重要なお知らせ】[^＞>]*(?:＞＞|>>)?", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = clean_text(text).strip(" ・:：。")
    label_match = re.search(r"【[^】]*(?:抽選|先行|一般発売|発売|受付)[^】]*】", text)
    if label_match and label_match.start() > 0:
        text = text[label_match.start() :]
    if not text:
        return "none"
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def render_event_detail_page(db_path: str, event_id: int, user_id: int = 0) -> str:
    event = event_detail(db_path, event_id, user_id=user_id)
    if not event:
        return "<!doctype html><title>Not found</title><h1>Event not found</h1>"
    event_dates = [clean_text(str(item)) for item in event.get("event_dates", []) if clean_text(str(item))]
    venues = [clean_text(str(item)) for item in event.get("venues", []) if clean_text(str(item))]
    summary_text = str(event.get("summary") or "")
    ticket_rules = tuple(
        clean_text(str(item)) for item in event.get("ticket_rules", []) if clean_text(str(item))
    ) or extract_ticket_rule_items(summary_text)
    ticket_prices = tuple(
        clean_text(str(item)) for item in event.get("ticket_prices", []) if clean_text(str(item))
    ) or extract_ticket_price_items(summary_text)
    organizers = tuple(clean_text(str(item)) for item in event.get("organizers", []) if clean_text(str(item)))
    lineup = tuple(clean_text(str(item)) for item in event.get("lineup", []) if clean_text(str(item)))
    redirect = f"/events/{event_id}"

    def date_range(start: object, end: object) -> str:
        start_text, end_text = friendly_date(start), friendly_date(end)
        if start_text and end_text:
            return f"{start_text} – {end_text}"
        return start_text or (f"until {end_text}" if end_text else "")

    def render_round(ticket: dict[str, object]) -> str:
        # Only dates that exist are shown: a general-sale round carries just a
        # sale date, and many lotteries never publish result or payment dates.
        schedule = [
            (label, value)
            for label, value in (
                ("Apply", date_range(ticket.get("application_start_at"), ticket.get("application_end_at"))),
                ("Results", friendly_date(ticket.get("results_date"))),
                ("Payment due", friendly_date(ticket.get("payment_end_at"))),
                ("On sale", friendly_date(ticket.get("general_sale_date"))),
                ("Resale", date_range(ticket.get("trade_start_at"), ticket.get("trade_end_at"))),
            )
            if value
        ]
        schedule_line = meta_line(*(f"{label} {html.escape(value)}" for label, value in schedule))
        evidence = format_evidence_snippet(ticket.get("evidence"))
        evidence_line = f"<details><summary>Source text</summary><small>{html.escape(evidence)}</small></details>" if evidence != "none" else ""
        return f"""
        <li>
          <span class="round">
            <span class="title">{html.escape(str(ticket.get('name') or 'Ticket round'))}</span>
            {meta_line(status_html(ticket.get("status"), ticket.get("status_label")), html.escape(clean_text(str(ticket.get("platform") or ticket.get("source") or ""))), html.escape(clean_text(str(ticket.get("membership_label") or ""))))}
            {schedule_line}
            {evidence_line}
          </span>
          <span class="row-actions">
            {web_source_link(ticket.get('url'), 'Source')}
            {subscribe_button(event.get('watch_id'), NOTIFY_SCOPE_ROUND, 'Notify', round_key=str(ticket.get('round_key') or ''), redirect=redirect)}
          </span>
        </li>
        """

    rounds = [ticket for ticket in event.get("rounds", []) if isinstance(ticket, dict)]
    rounds.sort(key=lambda ticket: -round_latest_date_ordinal(ticket))
    round_items = "".join(render_round(ticket) for ticket in rounds) or '<li class="empty-row">No ticket rounds found yet.</li>'

    stops = tour_stops(venues, event_dates)
    tour_section = ""
    if len(stops) > 1:
        tour_items = "".join(
            f"""
        <li>
          <span><span class="title">{html.escape(city or venue or 'Venue TBA')}</span>
          {meta_line(*(html.escape(part) for part in (venue if venue != city else "", friendly_date(date) or "Dates TBA")))}</span>
          {subscribe_button(event.get('watch_id'), NOTIFY_SCOPE_EVENT_LOCATION, 'Notify', location=(city or venue), redirect=redirect)}
        </li>
        """
            for city, venue, date in stops
        )
        tour_section = f'<section><h2>Locations &amp; Tour Dates</h2><ul class="tour-list">{tour_items}</ul></section>'

    def list_section(title: str, items: Iterable[str]) -> str:
        rows = "".join(items)
        return f"<section><h2>{title}</h2><ul>{rows}</ul></section>" if rows else ""

    ticket_link_section = list_section(
        "Ticket Links",
        (
            f"""
        <li>
          <span class="title">{html.escape(str(link.get('label') or link.get('platform') or 'Ticket link'))}</span>
          {web_source_link(link.get('url'), 'Open')}
        </li>
        """
            for link in event.get("ticket_links", [])
            if isinstance(link, dict)
        ),
    )
    related_section = list_section(
        "Related Saved Events",
        (
            f"""
        <li>
          <span><a class="title" href="/events/{html.escape(str(related.get('id')))}">{html.escape(str(related.get('title') or 'Related event'))}</a>
          <small>{html.escape(' · '.join(str(reason) for reason in related.get('recommendation_reasons', [])[:3]))}</small></span>
        </li>
        """
            for related in event.get("related_events", [])
            if isinstance(related, dict)
        ),
    )
    manual_source_section = list_section(
        "Your Sources",
        (
            f"""
        <li>
          <span><span class="title">{html.escape(str(source.get('label')))}</span>
          <small>{html.escape('Private note' if source.get('private_note') else str(source.get('platform')))}</small></span>
          {web_source_link(source.get('url'))}
        </li>
        """
            for source in event.get("manual_sources", [])
        ),
    )
    facts = [
        (label, "<br>".join(html.escape(item) for item in values))
        for label, values in (
            ("Dates", [friendly_date(item) for item in event_dates[:3]]),
            ("Venue", venues[:3]),
            ("Organizer", organizers),
            ("Cast &amp; lineup", lineup),
            ("Prices", ticket_prices),
            ("Rules", ticket_rules),
        )
        if values
    ]
    facts_section = (
        '<section><h2>Details</h2><dl class="facts panel">'
        + "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in facts)
        + "</dl></section>"
        if facts
        else ""
    )
    official = event.get("official_url") or ""
    official_button = web_source_link(official, "Official page") if is_web_url(official) else ""
    first_date = friendly_date(event_dates[0]) if event_dates else ""
    return render_page(
        str(event.get("title") or "Event"),
        f"""
    <section>
      <h1>{html.escape(str(event.get('title') or 'Untitled event'))}</h1>
      {meta_line(html.escape(str(event.get('status_label') or '')), html.escape(first_date), html.escape(infer_event_location(venues)))}
      <div class="round-links">{official_button}{subscribe_button(event.get("watch_id"), NOTIFY_SCOPE_EVENT_ALL, "Notify me for all rounds", redirect=redirect)}</div>
    </section>
    <section><h2>Ticket Rounds</h2><ul>{round_items}</ul></section>
    {tour_section}
    {facts_section}
    {ticket_link_section}
    {related_section}
    {manual_source_section}
  """,
    )


NOTIFY_CHANNEL_PRESET = "feed,push"
NOTIFY_SCOPE_LABELS = {
    NOTIFY_SCOPE_ARTIST_ALL: "Artist — all shows",
    NOTIFY_SCOPE_EVENT_ALL: "Event — all locations",
    NOTIFY_SCOPE_EVENT_LOCATION: "Event — single location",
    NOTIFY_SCOPE_ROUND: "Single round",
}


def subscribe_button(
    watch_id: object,
    scope: str,
    label: str,
    *,
    location: str = "",
    round_key: str = "",
    redirect: str = "",
    channels: str = NOTIFY_CHANNEL_PRESET,
) -> str:
    return f"""
    <form method="post" action="/subscribe" class="subscribe-form">
      <input type="hidden" name="watch" value="{html.escape(str(watch_id))}">
      <input type="hidden" name="scope" value="{html.escape(scope)}">
      <input type="hidden" name="location" value="{html.escape(location)}">
      <input type="hidden" name="round_key" value="{html.escape(round_key)}">
      <input type="hidden" name="channels" value="{html.escape(channels)}">
      <input type="hidden" name="redirect" value="{html.escape(redirect)}">
      <button class="button" type="submit" title="Notify me">{html.escape(label)}</button>
    </form>
    """


def notification_channels_from_form(form: dict[str, str], default: str) -> str:
    if form.get("channel_editor") != "1":
        return form.get("channels", default)
    selected = [channel for channel in NOTIFY_CHANNELS if form.get(f"channel_{channel}") == "1"]
    return ",".join(selected) or DEFAULT_NOTIFY_CHANNELS


def render_settings_page(db_path: str, user_id: int = 0, account_email: str = "", account_error: str = "") -> str:
    all_watches = list_watches(db_path, include_muted=True, user_id=user_id)
    watches = {watch.id: watch.keyword for watch in all_watches}
    artists = [watch for watch in all_watches if watch.kind == WATCH_KIND_ARTIST and not watch.muted]
    subscriptions = list_subscriptions(db_path, user_id=user_id)
    feed = notification_feed(db_path, limit=100, user_id=user_id)
    def render_subscription_item(subscription: NotificationSubscription) -> str:
        selected = parsed_notification_channels(subscription.channels)
        available_channels = ("feed", "push") if user_id > 0 else NOTIFY_CHANNELS
        channel_controls: list[str] = []
        for channel in available_channels:
            label = channel.upper() if channel == "line" else channel.title()
            if channel == "feed":
                channel_controls.append(
                    '<label><input type="checkbox" checked disabled> Feed</label>'
                    '<input type="hidden" name="channel_feed" value="1">'
                )
            else:
                checked = " checked" if channel in selected else ""
                channel_controls.append(
                    f'<label><input type="checkbox" name="channel_{channel}" value="1"{checked}> {label}</label>'
                )
        account_note = (
            "Account subscriptions support private feed and push delivery."
            if user_id > 0
            else "Slack, Discord, and LINE use the server's local-workspace credentials."
        )
        channels = ", ".join(channel.title() for channel in parsed_notification_channels(subscription.channels))
        return f"""
        <li class="subscription-row">
          <span><span class="title">{html.escape(watches.get(subscription.watch_id, "Removed watch"))}</span>
          {meta_line(html.escape(NOTIFY_SCOPE_LABELS.get(subscription.scope, subscription.scope)), html.escape(subscription.location), html.escape(channels), f"{html.escape(subscription.lead_days)} days before")}</span>
          <form method="post" action="/subscribe/remove">
            <input type="hidden" name="identifier" value="{subscription.id}">
            <input type="hidden" name="redirect" value="/settings">
            <button class="quiet danger" type="submit">Remove</button>
          </form>
          <details class="subscription-editor">
            <summary>Change delivery</summary>
            <form method="post" action="/subscribe">
              <input type="hidden" name="channel_editor" value="1">
              <input type="hidden" name="watch" value="{subscription.watch_id}">
              <input type="hidden" name="scope" value="{html.escape(subscription.scope)}">
              <input type="hidden" name="location" value="{html.escape(subscription.location, quote=True)}">
              <input type="hidden" name="round_key" value="{html.escape(subscription.round_key, quote=True)}">
              <input type="hidden" name="redirect" value="/settings">
              <fieldset><legend>Channels</legend><div class="channel-options">{''.join(channel_controls)}</div></fieldset>
              <label class="lead-days">Remind me this many days before <input name="lead_days" value="{html.escape(subscription.lead_days, quote=True)}" inputmode="numeric" pattern="[0-9, ]+" required></label>
              <small>{account_note}</small>
              <span><button type="submit">Save</button></span>
            </form>
          </details>
        </li>
        """

    subscription_items = "".join(render_subscription_item(subscription) for subscription in subscriptions) or '<li class="empty-row">Nothing yet. Open an event and choose “Notify me”.</li>'
    feed_items = "".join(
        f"""
        <li>
          <span><span class="title">{html.escape(str(item.get('title') or ''))}</span>
          <small>{html.escape(str(item.get('body') or ''))}</small>
          {meta_line(html.escape(friendly_date(str(item.get('created_at') or '')[:10])))}</span>
        </li>
        """
        for item in feed[:20]
    ) or '<li class="empty-row">Reminders appear here as ticket dates approach.</li>'
    artist_items = "".join(
        f"""
        <li>
          <a class="title" href="/artists/{watch.id}">{html.escape(watch.keyword)}</a>
          <form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><input type="hidden" name="redirect" value="/settings">
          <button class="quiet danger" title="Remove artist" aria-label="Remove {html.escape(watch.keyword)}">Remove</button></form>
        </li>
        """
        for watch in artists
    ) or '<li class="empty-row">Follow an artist to discover new shows.</li>'
    if account_email:
        account_section = f"""
        <ul><li>
          <span><span class="title">{html.escape(account_email)}</span><small>Signed in</small></span>
          <form method="post" action="/account/logout"><button type="submit">Log out</button></form>
        </li></ul>"""
    else:
        account_section = f"""
        <form class="panel stack" method="post" action="/account/login">
          <small>Sign in to see the same watches as the app.</small>
          {f'<p class="message">{html.escape(account_error)}</p>' if account_error else ''}
          <div class="form-grid">
            <input name="email" type="email" autocomplete="username" placeholder="Email" maxlength="{MAX_EMAIL_LENGTH}" required>
            <input name="password" type="password" autocomplete="current-password" placeholder="Password" minlength="8" maxlength="{MAX_PASSWORD_LENGTH}" required>
          </div>
          <span class="round-links">
            <button class="primary" type="submit">Log in</button>
            <button type="submit" formaction="/account/register">Create account</button>
          </span>
        </form>"""
    return render_page(
        "Settings",
        f"""
    <h1>Settings</h1>
    <section><h2>Account</h2>{account_section}</section>
    <section><h2>Reminders</h2><ul>{subscription_items}</ul></section>
    <section><h2>Recent reminders</h2><ul>{feed_items}</ul></section>
    <section>
      <div class="section-head"><h2>Artists</h2>
        <form method="post" action="/watch/run"><input type="hidden" name="kind" value="artist"><button class="quiet" title="Check artists now">Check now</button></form>
      </div>
      <form class="inline-form" method="post" action="/watch/add">
        <input type="hidden" name="kind" value="artist">
        <input name="keyword" placeholder="Artist or production company" aria-label="Artist or production company" maxlength="{MAX_KEYWORD_LENGTH}" required>
        <button class="primary" title="Follow artist">Follow</button>
      </form>
      <ul>{artist_items}</ul>
    </section>
    <footer>
      <span title="Server release">Version {html.escape(APP_VERSION)} ({APP_BUILD}) · schema {DB_SCHEMA_VERSION}</span>
      <a href="/privacy">Privacy</a>
      <a href="/support">Support</a>
    </footer>
  """,
    )


render_notifications_page = render_settings_page


def send_security_headers(
    handler: http.server.BaseHTTPRequestHandler,
    cache_control: str = "no-store",
) -> None:
    """Apply browser/API protections consistently to every response shape."""
    handler.send_header("Cache-Control", cache_control)
    handler.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
    handler.send_header("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    if web_request_uses_https(handler):
        handler.send_header("Strict-Transport-Security", "max-age=31536000")


def json_response(handler: http.server.BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    send_security_headers(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(
    handler: http.server.BaseHTTPRequestHandler,
    body: str,
    content_type: str,
    status: int = 200,
    cache_control: str = "no-store",
) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    send_security_headers(handler, cache_control)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def html_response(handler: http.server.BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    send_security_headers(handler)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_form(handler: http.server.BaseHTTPRequestHandler) -> dict[str, str]:
    if handler.headers.get("Transfer-Encoding"):
        raise FormBodyError("transfer encoding is not supported")
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as error:
        raise FormBodyError("invalid content length") from error
    if length < 0:
        raise FormBodyError("invalid content length")
    if length > FORM_BODY_LIMIT:
        raise FormBodyError(f"form body exceeds {FORM_BODY_LIMIT} bytes", status=413)
    try:
        body = handler.rfile.read(length) if length else b""
        if len(body) != length:
            raise FormBodyError("form body is shorter than content length")
        raw = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FormBodyError("form body must be valid UTF-8") from error
    try:
        parsed = urllib.parse.parse_qs(raw, max_num_fields=FORM_FIELD_LIMIT)
    except ValueError as error:
        raise FormBodyError(f"form body exceeds {FORM_FIELD_LIMIT} fields") from error
    return {key: values[0] for key, values in parsed.items() if values}


def parse_query(query_string: str) -> dict[str, list[str]]:
    try:
        return urllib.parse.parse_qs(
            query_string,
            keep_blank_values=True,
            max_num_fields=QUERY_FIELD_LIMIT,
        )
    except ValueError as error:
        raise QueryParameterError(f"query exceeds {QUERY_FIELD_LIMIT} fields") from error


def bounded_query_int(
    query: dict[str, list[str]],
    name: str,
    default: int,
    maximum: int,
) -> int:
    values = query.get(name, [])
    if not values or values == [""]:
        return default
    if len(values) != 1:
        raise QueryParameterError(f"{name} must be specified once")
    try:
        value = int(values[0])
    except ValueError as error:
        raise QueryParameterError(f"{name} must be an integer") from error
    if value < 1 or value > maximum:
        raise QueryParameterError(f"{name} must be between 1 and {maximum}")
    return value


def redirect_response(
    handler: http.server.BaseHTTPRequestHandler,
    location: str = "/",
    *,
    set_cookie: str = "",
) -> None:
    handler.send_response(303)
    send_security_headers(handler)
    handler.send_header("Location", location)
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.end_headers()


def add_watch_from_form(db_path: str, form: dict[str, str], user_id: int | None = None) -> Watch:
    return add_watch(
        db_path,
        clean_text(form.get("keyword", "")),
        kind=form.get("kind", WATCH_KIND_EVENT),
        tags=form.get("tags", ""),
        preferred_regions=form.get("regions", ""),
        preferred_venues=form.get("venues", ""),
        alert_preferences=form.get("alerts", DEFAULT_ALERT_PREFERENCES),
        user_id=user_id,
    )


DASHBOARD_SCRIPT = """  <script>
    const tabButtons = Array.from(document.querySelectorAll('[data-tab-target]'));
    const tabPanels = Array.from(document.querySelectorAll('[data-tab-panel]'));
    function showDashboardTab(name) {
      tabButtons.forEach((button) => {
        button.setAttribute('aria-selected', String(button.dataset.tabTarget === name));
      });
      tabPanels.forEach((panel) => {
        panel.classList.toggle('is-active', panel.dataset.tabPanel === name);
      });
    }
    tabButtons.forEach((button) => {
      button.addEventListener('click', () => showDashboardTab(button.dataset.tabTarget));
    });
    document.querySelectorAll('[data-edit-watch]').forEach((button) => {
      button.addEventListener('click', () => {
        showDashboardTab('watching');
        const filters = document.getElementById('watch-filters');
        if (filters) filters.open = true;
        ['keyword', 'tags', 'regions', 'venues', 'alerts'].forEach((field) => {
          const input = document.getElementById(`watch-${field}`);
          if (input) input.value = button.dataset[field] || '';
        });
        document.getElementById('watch-keyword')?.focus();
      });
    });
  </script>
"""


def render_watch_edit_button(watch: Watch) -> str:
    attributes = {
        "kind": watch.kind,
        "keyword": watch.keyword,
        "tags": watch.tags,
        "regions": watch.preferred_regions,
        "venues": watch.preferred_venues,
        "alerts": watch.alert_preferences,
    }
    encoded = " ".join(
        f'data-{name}="{html.escape(str(value or ""), quote=True)}"'
        for name, value in attributes.items()
    )
    return (
        f'<button class="quiet" type="button" data-edit-watch {encoded} '
        'title="Edit filters" aria-label="Edit filters">Edit</button>'
    )


def tracked_event_display_key(watch: Watch, event: dict[str, object] | None) -> tuple[int, int, int, int, str]:
    if not event:
        return (1, 0, 0, 0, watch.keyword.lower())
    has_official = int(is_web_url(event.get("official_url")))
    ticket_count = len(event.get("ticket_links", [])) if isinstance(event.get("ticket_links"), list) else 0
    round_count = len(event.get("rounds", [])) if isinstance(event.get("rounds"), list) else 0
    date_count = len(event.get("event_dates", [])) if isinstance(event.get("event_dates"), list) else 0
    return (-has_official, -round_count, -date_count, -ticket_count, watch.keyword.lower())


def render_web_page(
    db_path: str,
    event_search_keyword: str = "",
    event_search_results: Sequence[SearchResult] = (),
    event_search_error: str = "",
    selected_tab: str = "",
    user_id: int = 0,
    account_email: str = "",
    account_error: str = "",
) -> str:
    watches = list_watches(db_path, include_muted=True, user_id=user_id)
    events = recent_events(db_path, user_id=user_id, source_user_id=user_id)
    upcoming_rows = upcoming_priority_rows(db_path, limit=8, user_id=user_id)
    latest_event_by_watch_id = {
        int(event["watch_id"]): event
        for event in reversed(events)
        if event.get("watch_kind") == WATCH_KIND_EVENT and event.get("watch_id")
    }
    active_event_watches = [watch for watch in watches if not watch.muted and watch.kind == WATCH_KIND_EVENT]

    def render_tracked_event_item(watch: Watch) -> str:
        event = latest_event_by_watch_id.get(watch.id)
        row_actions = (
            f'<span class="row-actions">{render_watch_edit_button(watch)}'
            f'<form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}">'
            f'<button class="quiet danger" title="Stop watching" aria-label="Stop watching {html.escape(watch.keyword)}">Remove</button></form></span>'
        )
        if not event:
            return f"""
        <li>
          <span><span class="title">{html.escape(watch.keyword)}</span>
          <small>{"Nothing found yet" if watch.last_checked_at else "Not checked yet"}</small></span>
          {row_actions}
        </li>
        """
        date_items = event.get("event_dates", [])
        venue_items = [str(item) for item in event.get("venues", [])] if isinstance(event.get("venues"), list) else []
        first_date = friendly_date(date_items[0]) if isinstance(date_items, list) and date_items else ""
        return f"""
        <li>
          <span>
            <a class="title" href="/events/{html.escape(str(event.get('id')))}">{html.escape(str(event.get('title') or watch.keyword))}</a>
            {meta_line(html.escape(str(event.get('status_label') or '')), html.escape(first_date), html.escape(infer_event_location(venue_items)))}
          </span>
          {row_actions}
        </li>
        """

    active_event_watches.sort(key=lambda watch: tracked_event_display_key(watch, latest_event_by_watch_id.get(watch.id)))
    tracked_event_items = "\n".join(render_tracked_event_item(watch) for watch in active_event_watches) or '<li class="empty-row">You are not watching any events yet.</li>'
    event_result_items = "\n".join(
        f"""
        <li>
          <span><span class="title">{html.escape(result.title or result.url)}</span><small>{html.escape(result.url)}</small></span>
          <form method="post" action="/event/add">
            <input type="hidden" name="keyword" value="{html.escape(event_search_keyword)}">
            <input type="hidden" name="title" value="{html.escape(result.title)}">
            <input type="hidden" name="url" value="{html.escape(result.url)}">
            <input type="hidden" name="snippet" value="{html.escape(result.snippet)}">
            <button class="primary" title="Watch this page" aria-label="Watch this page">Watch</button>
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
        <small>Pages matching “{html.escape(event_search_keyword)}”</small>
        <ul>{event_result_items or '<li class="empty-row">No matching event pages found.</li>'}</ul>
        """
    upcoming_items = "\n".join(
        f"""
        <li>
          <span>
            <a class="title" href="/events/{html.escape(str(row.get('event_id')))}">{html.escape(str(row.get('event_title') or 'Untitled event'))}</a>
            {meta_line(status_html(row.get('status'), row.get('status_label')), html.escape(friendly_date(row.get('relevant_date'))), html.escape(str(row.get('platform') or '')))}
            <small>{html.escape(str(row.get('round_name') or ''))}</small>
          </span>
        </li>
        """
        for row in upcoming_rows
    ) or f'<li class="empty-row">{"Nothing needs your attention right now." if active_event_watches else "Watch an event to see its ticket deadlines here."}</li>'
    legacy_tabs = {"attention": "upcoming", "events": "watching", "artists": "watching"}
    selected_tab = legacy_tabs.get(selected_tab, selected_tab)
    active_dashboard_tab = selected_tab if selected_tab in {"upcoming", "watching"} else "watching" if event_search_keyword or event_search_error or not upcoming_rows else "upcoming"
    sign_in_hint = (
        ""
        if user_id > 0 or account_email
        else '<p class="panel muted">Using this device only. <a href="/settings">Sign in</a> to see your app watches.</p>'
    )

    def tab_button(name: str, label: str, count: int) -> str:
        selected = "true" if active_dashboard_tab == name else "false"
        return (
            f'<button class="tab" type="button" role="tab" aria-selected="{selected}" aria-controls="panel-{name}" '
            f'id="tab-{name}" data-tab-target="{name}">{label}<span class="tab-count">{count}</span></button>'
        )

    def panel_attributes(name: str) -> str:
        active = " is-active" if active_dashboard_tab == name else ""
        return f'class="tab-panel{active}" id="panel-{name}" role="tabpanel" aria-labelledby="tab-{name}" data-tab-panel="{name}"'

    return render_page(
        "chusennote",
        f"""
    {sign_in_hint}
    <div class="tabs" role="tablist" aria-label="Dashboard sections">
      {tab_button("upcoming", "Upcoming", len(upcoming_rows))}
      {tab_button("watching", "Watching", len(active_event_watches))}
    </div>
    <section {panel_attributes("upcoming")}>
      <ul>{upcoming_items}</ul>
    </section>
    <section {panel_attributes("watching")}>
      <form class="stack" method="post" action="/watch/add">
        <input type="hidden" name="kind" value="event">
        <div class="inline-form">
          <input id="watch-keyword" name="keyword" placeholder="Event name or keyword" aria-label="Event name or keyword" value="{html.escape(event_search_keyword)}" maxlength="{MAX_KEYWORD_LENGTH}" required>
          <button class="primary" title="Watch this keyword">Watch</button>
          <button type="submit" formaction="/event/search" title="Find the official page">Find page</button>
        </div>
        <details id="watch-filters">
          <summary>Filters</summary>
          <div class="form-grid">
            <input id="watch-tags" name="tags" placeholder="Tags" aria-label="Tags">
            <input id="watch-regions" name="regions" placeholder="Regions" aria-label="Preferred regions">
            <input id="watch-venues" name="venues" placeholder="Venues" aria-label="Preferred venues">
            <input id="watch-alerts" name="alerts" placeholder="Alert types" aria-label="Alert types" value="{html.escape(DEFAULT_ALERT_PREFERENCES)}">
          </div>
          <small>Watching a keyword you already follow updates its filters.</small>
        </details>
      </form>
      {event_search_panel}
      <div class="section-head"><h2>Events</h2>
        <form method="post" action="/watch/run"><input type="hidden" name="kind" value="event"><button class="quiet" title="Check events now">Check now</button></form>
      </div>
      <ul>{tracked_event_items}</ul>
    </section>
    <footer>
      <a href="/privacy">Privacy</a>
      <a href="/support">Support</a>
    </footer>
  """,
        script=DASHBOARD_SCRIPT,
    )


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
        f"<li><strong>{html.escape(str(alert.get('type_label') or alert.get('type') or alert.get('alert_type') or 'Alert'))}</strong> "
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
          <div><span class="status">{html.escape(str(ticket.get('status_label') or ticket.get('status') or 'unknown'))}</span> {html.escape(str(ticket.get('platform') or 'unknown'))} · confidence {html.escape(str(ticket.get('confidence') or 'unknown'))}</div>
          <small>Apply: {html.escape(str(ticket.get('application_start_at') or 'unknown'))} to {html.escape(str(ticket.get('application_end_at') or 'unknown'))}</small><br>
          <small>Results: {html.escape(str(ticket.get('results_date') or 'unknown'))}</small><br>
          <small>Resale: {html.escape(str(ticket.get('trade_start_at') or 'unknown'))} to {html.escape(str(ticket.get('trade_end_at') or 'unknown'))}</small><br>
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
      <p><span class="status">{html.escape(str(event.get('status_label') or event.get('status') or 'watching'))}</span> {official_link} · <small>{html.escape(str(event.get('updated_at') or ''))}</small></p>
      {metadata}
      {reason_section}
      {ticket_section}
    </article>
    """


def render_information_page(title: str, content: str) -> str:
    return render_page(
        f"{title} · Chusennote",
        f"""
    <article class="prose">{content}</article>
    <footer><a href="/privacy">Privacy</a><a href="/support">Support</a></footer>
  """,
    )


def render_privacy_page() -> str:
    return render_information_page(
        "Privacy Policy",
        """
        <h1>Privacy Policy</h1>
        <p class="meta">Effective September 22, 2026</p>
        <p>Chusennote helps people track public event and ticket information and receive reminders. This notice describes the information the hosted service processes.</p>
        <h2>Information you provide</h2>
        <ul>
          <li>If you create an account, the service stores your email address and a one-way password hash.</li>
          <li>The service stores the artists, events, public source links, regions, venues, tags, and reminder preferences that you choose to track.</li>
          <li>If you enable push notifications, the service stores a Firebase device token and the device platform so it can address notifications to that installation.</li>
          <li>Session, API, and calendar-feed tokens are used to authenticate access to your account. Mobile credentials are stored by the operating system's secure credential store.</li>
        </ul>
        <h2>Information collected automatically</h2>
        <p>The hosting platform may process standard request information such as IP address, user agent, request time, and diagnostic logs. Chusennote does not include advertising SDKs or third-party behavioral analytics.</p>
        <h2>How information is used</h2>
        <p>Information is used to provide the watchlist, event discovery, ticket timelines, account access, calendar feeds, and notifications you request; to protect the service; and to diagnose failures. Chusennote does not sell personal information.</p>
        <h2>Service providers</h2>
        <p>The hosted service uses Render for application hosting, Neon for PostgreSQL storage, Tavily for public-web discovery, and Firebase Cloud Messaging for push delivery. Apple may process information under its own policies when you download or use the iOS app. Public source sites are contacted only to discover or refresh event information.</p>
        <h2>Retention and control</h2>
        <p>Account data is retained while the account is active or as needed to operate and secure the service. Logging out revokes the current session and detaches that installation's push registration when the server confirms the request. Signed-in users can permanently delete their account and associated account data from Settings by confirming with their current password. To request access or correction, use the support link below. Do not include passwords, access tokens, or other secrets in a public issue.</p>
        <h2>Security and changes</h2>
        <p>The hosted service uses HTTPS and restricts credential transport. No internet service can guarantee absolute security. Material changes to this notice will be published at this URL with a revised effective date.</p>
        <p><a href="/support">Contact Chusennote support</a></p>
        """,
    )


def render_support_page() -> str:
    return render_information_page(
        "Support",
        """
        <h1>Chusennote Support</h1>
        <p>Chusennote tracks public event and ticket pages, keeps application and result dates together, and can send reminders through the app.</p>
        <h2>Before reporting a problem</h2>
        <ul>
          <li>Confirm the app's Base URL is <code>https://chusennote.onrender.com</code>.</li>
          <li>Open Settings and check that the server status is healthy.</li>
          <li>For missing notifications, confirm system notification permission is enabled and that the watch has an active push subscription.</li>
          <li>For outdated event details, open the official source link shown on the event and include that public URL in the report.</li>
        </ul>
        <h2>Contact</h2>
        <p><a href="https://github.com/otterlymavis/chusennote/issues/new">Open a support request on GitHub</a>. Do not include passwords, API tokens, private calendar links, or device tokens.</p>
        <p>Account deletion is available directly inside the signed-in iOS and Android apps under Settings. For other privacy requests, state only that the request concerns Chusennote account data and ask for private follow-up instructions. Do not post an account email or other personal data in the public issue.</p>
        <p><a href="/privacy">Read the Privacy Policy</a></p>
        """,
    )


def make_web_handler(db_path: str) -> type[http.server.BaseHTTPRequestHandler]:
    class ChusennoteHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def authorization_token(self) -> str:
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            return header[len(prefix):].strip() if header.startswith(prefix) else ""

        def has_invalid_authorization(self) -> bool:
            """Distinguish an absent local-workspace credential from a bad one."""
            header = self.headers.get("Authorization", "").strip()
            if not header:
                return False
            token = self.authorization_token()
            return not token or user_for_token(db_path, token) is None

        def reject_invalid_authorization(self, path: str) -> bool:
            # Logout validates its token inside the endpoint so a retry after a
            # lost successful response can remain idempotent after revocation.
            public_api_paths = {
                "/api/health",
                "/api/auth/register",
                "/api/auth/login",
                "/api/auth/logout",
            }
            protected_by_presence = (path.startswith("/api/") and path not in public_api_paths) or path == "/calendar.ics"
            if protected_by_presence and self.has_invalid_authorization():
                json_response(self, {"error": "unauthorized"}, status=401)
                return True
            return False

        def browser_token(self) -> str:
            try:
                cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
                return cookie[WEB_SESSION_COOKIE].value if WEB_SESSION_COOKIE in cookie else ""
            except http.cookies.CookieError:
                return ""

        def authenticated_user(self, *, allow_browser_cookie: bool = True):
            token = self.authorization_token()
            if not token and allow_browser_cookie:
                token = self.browser_token()
            return user_for_token(db_path, token)

        def browser_user_id(self) -> int:
            user = self.authenticated_user()
            return user.id if user else 0

        def api_user_id(self) -> int:
            user = self.authenticated_user(allow_browser_cookie=False)
            return user.id if user else 0

        def do_GET(self) -> None:
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            if self.reject_invalid_authorization(path):
                return
            try:
                query = parse_query(parsed_url.query)
            except QueryParameterError as error:
                json_response(self, {"error": str(error)}, status=400)
                return
            if path == "/":
                user = self.authenticated_user()
                user_id = user.id if user else 0
                html_response(
                    self,
                    render_web_page(
                        db_path,
                        selected_tab=query.get("tab", [""])[0],
                        user_id=user_id,
                        account_email=user.email if user else "",
                    ),
                )
            elif re.fullmatch(r"/artists/\d+", path):
                html_response(self, render_artist_detail_page(db_path, int(path.rsplit("/", 1)[1]), self.browser_user_id()))
            elif re.fullmatch(r"/events/\d+", path):
                html_response(self, render_event_detail_page(db_path, int(path.rsplit("/", 1)[1]), self.browser_user_id()))
            elif path in {"/settings", "/notifications"}:
                user = self.authenticated_user()
                html_response(self, render_settings_page(db_path, user.id if user else 0, user.email if user else ""))
            elif path == PAGE_CSS_PATH:
                text_response(
                    self,
                    PAGE_CSS,
                    "text/css; charset=utf-8",
                    cache_control="public, max-age=31536000, immutable",
                )
            elif path == "/privacy":
                html_response(self, render_privacy_page())
            elif path == "/support":
                html_response(self, render_support_page())
            elif path == "/api/health":
                json_response(self, api_health(db_path))
            elif path == "/api/auth/me":
                user = self.authenticated_user(allow_browser_cookie=False)
                if user is None:
                    json_response(self, {"error": "unauthorized"}, status=401)
                else:
                    json_response(self, dataclasses.asdict(user))
            elif path == "/api/watchlist":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                user_id = self.api_user_id()
                json_response(
                    self,
                    [
                        dataclasses.asdict(watch)
                        for watch in list_watches(
                            db_path,
                            include_muted=include_muted,
                            user_id=user_id,
                        )
                    ],
                )
            elif path == "/api/events":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                user_id = self.api_user_id()
                json_response(
                    self,
                    recent_events(
                        db_path,
                        include_muted_sources=include_muted,
                        include_muted_watches=include_muted,
                        user_id=user_id,
                        source_user_id=user_id,
                    ),
                )
            elif path == "/api/upcoming":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                json_response(
                    self,
                    upcoming_priority_rows(
                        db_path, include_muted_watches=include_muted, user_id=self.api_user_id()
                    ),
                )
            elif path == "/api/alerts":
                json_response(self, recent_alerts(db_path, user_id=self.api_user_id()))
            elif path == "/api/notifications":
                try:
                    limit = bounded_query_int(query, "limit", 100, NOTIFICATION_LIMIT_MAX)
                except QueryParameterError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, notification_feed(db_path, limit=limit, user_id=self.api_user_id()))
            elif path == "/api/event/search":
                try:
                    keyword = validated_keyword(query.get("keyword", [""])[0])
                    limit = bounded_query_int(query, "limit", 6, EVENT_SEARCH_LIMIT_MAX)
                    results = search_web(keyword, limit=limit)
                except (OSError, ValueError, QueryParameterError) as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, [dataclasses.asdict(result) for result in results])
            elif path == "/api/subscriptions":
                json_response(
                    self,
                    [
                        dataclasses.asdict(subscription)
                        for subscription in list_subscriptions(db_path, user_id=self.api_user_id())
                    ],
                )
            elif path == "/api/devices":
                json_response(
                    self,
                    [dataclasses.asdict(device) for device in list_devices(db_path, user_id=self.api_user_id())],
                )
            elif path == "/api/sources":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                user = self.authenticated_user(allow_browser_cookie=False)
                source_user_id = user.id if user else 0
                json_response(
                    self,
                    [
                        dataclasses.asdict(source)
                        for source in list_watch_sources(
                            db_path, include_muted=include_muted, user_id=source_user_id
                        )
                    ],
                )
            elif path == "/calendar.ics":
                include_muted = query.get("include_muted", ["0"])[0].lower() in {"1", "true", "yes"}
                token_values = query.get("token", [])
                token = clean_text(token_values[0]) if len(token_values) == 1 else ""
                # The query-string token is a calendar-only token (its own table,
                # unrelated to api_tokens) so a leaked feed URL can't be replayed
                # as a full-access bearer token; the Authorization header path
                # still checks the regular account token.
                if token_values:
                    user_id = user_id_for_calendar_token(db_path, token) if token else None
                    if user_id is None:
                        json_response(self, {"error": "unauthorized"}, status=401)
                        return
                else:
                    user = self.authenticated_user()
                    user_id = user.id if user else 0
                text_response(
                    self,
                    render_calendar_ics(db_path, include_muted_watches=include_muted, user_id=user_id),
                    "text/calendar; charset=utf-8",
                )
            else:
                json_response(self, {"error": "not found"}, status=404)

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if self.reject_invalid_authorization(path):
                return
            try:
                form = read_form(self)
            except FormBodyError as error:
                if path.startswith("/api/"):
                    json_response(self, {"error": str(error)}, status=error.status)
                else:
                    html_response(
                        self,
                        f"<!doctype html><title>Bad request</title><h1>{html.escape(str(error))}</h1>",
                        status=error.status,
                    )
                return
            if not path.startswith("/api/") and not web_request_has_valid_origin(self):
                html_response(self, "<!doctype html><title>Forbidden</title><h1>Cross-origin form submission rejected</h1>", status=403)
                return
            if path in {"/account/register", "/account/login"}:
                if not web_request_allows_credentials(self):
                    html_response(
                        self,
                        render_settings_page(
                            db_path,
                            account_error="Use HTTPS, localhost, or a literal private-network IP to sign in.",
                        ),
                        status=400,
                    )
                    return
                email = form.get("email", "")
                password = form.get("password", "")
                if path == "/account/register":
                    try:
                        user = create_user(db_path, email, password)
                    except ValueError as error:
                        html_response(self, render_settings_page(db_path, account_error=str(error)), status=400)
                        return
                else:
                    user = verify_user(db_path, email, password)
                    if user is None:
                        html_response(self, render_settings_page(db_path, account_error="Invalid credentials."), status=401)
                        return
                token = issue_token(db_path, user.id)
                redirect_response(
                    self,
                    set_cookie=web_session_cookie(token, secure=web_request_uses_https(self)),
                )
            elif path == "/account/logout":
                token = self.browser_token()
                if token:
                    revoke_token(db_path, token)
                redirect_response(
                    self,
                    set_cookie=web_session_cookie("", secure=web_request_uses_https(self), clear=True),
                )
            elif path == "/api/auth/register":
                if not web_request_allows_credentials(self):
                    json_response(self, {"error": "HTTPS or a private-network endpoint is required"}, status=400)
                    return
                try:
                    user = create_user(db_path, form.get("email", ""), form.get("password", ""))
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                token = issue_token(db_path, user.id)
                json_response(self, {"token": token, "user": dataclasses.asdict(user)})
            elif path == "/api/auth/login":
                if not web_request_allows_credentials(self):
                    json_response(self, {"error": "HTTPS or a private-network endpoint is required"}, status=400)
                    return
                user = verify_user(db_path, form.get("email", ""), form.get("password", ""))
                if user is None:
                    json_response(self, {"error": "invalid credentials"}, status=401)
                    return
                token = issue_token(db_path, user.id)
                json_response(self, {"token": token, "user": dataclasses.asdict(user)})
            elif path == "/api/auth/logout":
                device_token = clean_text(form.get("device_token", ""))
                authorization_token = self.authorization_token()
                if not authorization_token or not revoke_token(db_path, authorization_token, device_token):
                    json_response(self, {"error": "unauthorized"}, status=401)
                    return
                json_response(self, {"revoked": True, "device_detached": True})
            elif path == "/api/auth/delete":
                authorization_token = self.authorization_token()
                if not authorization_token or not delete_user_account(
                    db_path, authorization_token, form.get("password", "")
                ):
                    json_response(self, {"error": "invalid credentials"}, status=401)
                    return
                json_response(self, {"deleted": True})
            elif path == "/watch/add":
                try:
                    add_watch_from_form(db_path, form, user_id=self.browser_user_id())
                except ValueError as error:
                    html_response(
                        self,
                        f"<!doctype html><title>Invalid watch</title><h1>{html.escape(str(error))}</h1>",
                        status=400,
                    )
                    return
                redirect_response(self, "/settings" if form.get("kind") == WATCH_KIND_ARTIST else "/?tab=watching")
            elif path == "/watch/remove":
                remove_watch(db_path, form.get("identifier", ""), user_id=self.browser_user_id())
                redirect_response(self, safe_web_redirect(form.get("redirect", ""), "/?tab=watching"))
            elif path == "/watch/unmute":
                set_watch_muted(db_path, form.get("identifier", ""), False, user_id=self.browser_user_id())
                redirect_response(self)
            elif path == "/watch/run":
                kind = form.get("kind") or None
                run_watches(db_path, kind=kind, user_id=self.browser_user_id())
                redirect_response(self, "/settings" if kind == WATCH_KIND_ARTIST else "/?tab=watching" if kind == WATCH_KIND_EVENT else "/")
            elif path == "/event/search":
                try:
                    keyword = validated_keyword(form.get("keyword", ""))
                    results = search_web(keyword, limit=6)
                except (OSError, ValueError) as error:
                    html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_error=str(error), user_id=self.browser_user_id()))
                    return
                html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_results=results, user_id=self.browser_user_id()))
            elif path == "/event/add":
                keyword = clean_text(form.get("keyword", ""))
                title = clean_text(form.get("title", ""))
                url = clean_text(form.get("url", ""))
                snippet = clean_text(form.get("snippet", ""))
                if not is_public_fetch_url(url):
                    html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_error="Pick a public credential-free HTTP(S) event page.", user_id=self.browser_user_id()))
                    return
                try:
                    event_keyword = keyword or title
                    watch = add_watch(
                        db_path,
                        event_keyword,
                        kind=WATCH_KIND_EVENT,
                        user_id=self.browser_user_id(),
                    )
                    blocks = build_exact_event_blocks(event_keyword, title, url, snippet)
                    save_blocks(db_path, blocks, watch_id=watch.id)
                except (OSError, ValueError) as error:
                    html_response(self, render_web_page(db_path, event_search_keyword=keyword, event_search_error=str(error), user_id=self.browser_user_id()))
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
                        user_id=self.browser_user_id(),
                    )
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                redirect_response(self)
            elif path == "/source/remove":
                remove_watch_source(db_path, form.get("identifier", ""), user_id=self.browser_user_id())
                redirect_response(self)
            elif path == "/source/unmute":
                set_watch_source_muted(db_path, form.get("identifier", ""), False, user_id=self.browser_user_id())
                redirect_response(self)
            elif path == "/api/watchlist":
                keyword = clean_text(form.get("keyword", ""))
                if not keyword:
                    json_response(self, {"error": "keyword is required"}, status=400)
                    return
                try:
                    watch = add_watch_from_form(db_path, form, user_id=self.api_user_id())
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, dataclasses.asdict(watch))
            elif path == "/api/watchlist/remove":
                json_response(
                    self,
                    {"removed": remove_watch(db_path, form.get("identifier", ""), user_id=self.api_user_id())},
                )
            elif path == "/api/watchlist/mute":
                json_response(
                    self,
                    {"muted": set_watch_muted(db_path, form.get("identifier", ""), True, user_id=self.api_user_id())},
                )
            elif path == "/api/watchlist/unmute":
                json_response(
                    self,
                    {
                        "unmuted": set_watch_muted(
                            db_path, form.get("identifier", ""), False, user_id=self.api_user_id()
                        )
                    },
                )
            elif path == "/api/run":
                json_response(self, run_watches(db_path, kind=form.get("kind") or None, user_id=self.api_user_id()))
            elif path == "/api/event/add":
                keyword = clean_text(form.get("keyword", ""))
                title = clean_text(form.get("title", ""))
                url = clean_text(form.get("url", ""))
                snippet = clean_text(form.get("snippet", ""))
                if not is_public_fetch_url(url):
                    json_response(self, {"error": "Pick a public credential-free HTTP(S) event page."}, status=400)
                    return
                try:
                    event_keyword = keyword or title
                    watch = add_watch(
                        db_path,
                        event_keyword,
                        kind=WATCH_KIND_EVENT,
                        user_id=self.api_user_id(),
                    )
                    alerts = save_blocks(
                        db_path,
                        build_exact_event_blocks(event_keyword, title, url, snippet),
                        watch_id=watch.id,
                    )
                except (OSError, ValueError) as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, {"added": True, "alerts": alerts})
            elif path == "/subscribe":
                try:
                    add_subscription(
                        db_path,
                        form.get("watch", ""),
                        form.get("scope", NOTIFY_SCOPE_EVENT_ALL),
                        location=form.get("location", ""),
                        round_key=form.get("round_key", ""),
                        channels=notification_channels_from_form(form, NOTIFY_CHANNEL_PRESET),
                        lead_days=form.get("lead_days", "7,1,0"),
                        user_id=self.browser_user_id(),
                    )
                except ValueError:
                    pass
                redirect_response(self, safe_web_redirect(form.get("redirect", ""), "/settings"))
            elif path == "/subscribe/remove":
                identifier = form.get("identifier", "")
                if str(identifier).isdigit():
                    remove_subscription(db_path, int(identifier), user_id=self.browser_user_id())
                redirect_response(self, safe_web_redirect(form.get("redirect", ""), "/settings"))
            elif path == "/api/subscriptions":
                try:
                    subscription = add_subscription(
                        db_path,
                        form.get("watch", ""),
                        form.get("scope", NOTIFY_SCOPE_EVENT_ALL),
                        location=form.get("location", ""),
                        round_key=form.get("round_key", ""),
                        channels=form.get("channels", DEFAULT_NOTIFY_CHANNELS),
                        lead_days=form.get("lead_days", "7,1,0"),
                        user_id=self.api_user_id(),
                    )
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, dataclasses.asdict(subscription))
            elif path == "/api/subscriptions/remove":
                identifier = form.get("identifier", "")
                removed = (
                    remove_subscription(db_path, int(identifier), user_id=self.api_user_id())
                    if str(identifier).isdigit()
                    else False
                )
                json_response(self, {"removed": removed})
            elif path == "/api/calendar/token":
                user_id = self.api_user_id()
                if user_id <= 0:
                    json_response(self, {"error": "unauthorized"}, status=401)
                    return
                rotate = form.get("rotate", "").lower() in {"1", "true", "yes"}
                json_response(self, {"token": issue_calendar_token(db_path, user_id, rotate=rotate)})
            elif path == "/api/devices":
                try:
                    device = register_device(
                        db_path,
                        form.get("token", ""),
                        platform=form.get("platform", "android"),
                        label=form.get("label", ""),
                        user_id=self.api_user_id(),
                    )
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, dataclasses.asdict(device))
            elif path == "/api/notifications/run":
                json_response(self, run_notifications(db_path, user_id=self.api_user_id()))
            elif path == "/api/sources":
                try:
                    source = add_watch_source(
                        db_path,
                        form.get("watch", ""),
                        form.get("url", ""),
                        form.get("label", ""),
                        bool(form.get("private_note")),
                        user_id=self.api_user_id(),
                    )
                except ValueError as error:
                    json_response(self, {"error": str(error)}, status=400)
                    return
                json_response(self, dataclasses.asdict(source))
            elif path == "/api/sources/remove":
                json_response(
                    self,
                    {
                        "removed": remove_watch_source(
                            db_path, form.get("identifier", ""), user_id=self.api_user_id()
                        )
                    },
                )
            elif path == "/api/sources/mute":
                json_response(
                    self,
                    {
                        "muted": set_watch_source_muted(
                            db_path, form.get("identifier", ""), True, user_id=self.api_user_id()
                        )
                    },
                )
            elif path == "/api/sources/unmute":
                json_response(
                    self,
                    {
                        "unmuted": set_watch_source_muted(
                            db_path, form.get("identifier", ""), False, user_id=self.api_user_id()
                        )
                    },
                )
            else:
                json_response(self, {"error": "not found"}, status=404)

    return ChusennoteHandler


def create_web_server(db_path: str, port: int, host: str = "127.0.0.1") -> http.server.ThreadingHTTPServer:
    return http.server.ThreadingHTTPServer((host, port), make_web_handler(db_path))


def run_web(db_path: str, port: int, host: str = "127.0.0.1") -> None:
    require_postgres = os.environ.get("CHUSENNOTE_REQUIRE_POSTGRES", "").strip().lower() in {"1", "true", "yes"}
    if require_postgres and not is_postgres_url(resolve_target(db_path)):
        raise ValueError("CHUSENNOTE_REQUIRE_POSTGRES requires a PostgreSQL CHUSENNOTE_DATABASE_URL")
    server = create_web_server(db_path, port, host)
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    bind_note = f" (bound to {host})" if display_host != host else ""
    print(f"Serving chusennote at http://{display_host}:{server.server_port}{bind_note}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Chusennote server stopped.")
    finally:
        server.server_close()
