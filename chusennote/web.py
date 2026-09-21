"""Local web UI: HTML rendering and the standard-library HTTP server.

Renders the dashboard, artist/event detail pages, and JSON/ICS API responses,
and wires them into a ThreadingHTTPServer. Depends on the persistence and
discovery layers (:mod:`chusennote.crud`, :mod:`chusennote.read_models`,
:mod:`chusennote.pipeline`) plus the lower leaf modules.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
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


def web_source_link(url: object, label: str = "Open") -> str:
    return (
        f'<a class="action-link" href="{html.escape(str(url))}">{html.escape(label)}</a>'
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
      <div class="round-actions">{subscribe_button(artist.id, NOTIFY_SCOPE_ARTIST_ALL, "Notify me for all shows", redirect=f"/artists/{artist.id}")}</div>
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
    time_label = "; ".join(event_dates[:3]) if event_dates else "Unknown"
    venue_label = "; ".join(venues[:3]) if venues else "Unknown"
    location_label = infer_event_location(venues) or "Unknown"
    summary_text = str(event.get("summary") or "")
    ticket_rules = tuple(
        clean_text(str(item)) for item in event.get("ticket_rules", []) if clean_text(str(item))
    ) or extract_ticket_rule_items(summary_text)
    ticket_prices = tuple(
        clean_text(str(item)) for item in event.get("ticket_prices", []) if clean_text(str(item))
    ) or extract_ticket_price_items(summary_text)
    organizers = tuple(clean_text(str(item)) for item in event.get("organizers", []) if clean_text(str(item)))
    lineup = tuple(clean_text(str(item)) for item in event.get("lineup", []) if clean_text(str(item)))
    ticket_rule_items = "".join(f"<li>{html.escape(item)}</li>" for item in ticket_rules) or "<li>Ticket rules not captured yet.</li>"
    ticket_price_items = "".join(f"<li>{html.escape(item)}</li>" for item in ticket_prices) or "<li>Ticket prices not captured yet.</li>"
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
    def render_round_card(ticket: dict[str, object]) -> str:
        # Render only the facts that have a value: a general-sale round carries
        # just a sale date, and many lottery rounds never publish result/payment
        # dates, so a fixed grid would be mostly "unknown".
        facts: list[tuple[str, str]] = [("Platform", str(ticket.get("platform") or ticket.get("source") or "unknown"))]
        for label, key in (
            ("Lottery opens", "application_start_at"),
            ("Lottery closes", "application_end_at"),
            ("Results", "results_date"),
            ("Payment due", "payment_end_at"),
            ("On sale", "general_sale_date"),
            ("Resale opens", "trade_start_at"),
            ("Resale closes", "trade_end_at"),
        ):
            value = clean_text(str(ticket.get(key) or ""))
            if value:
                facts.append((label, value))
        fact_grid = "".join(
            f"<div><small>{html.escape(label)}</small><strong>{html.escape(value)}</strong></div>"
            for label, value in facts
        )
        meta_parts: list[str] = []
        round_type = clean_text(str(ticket.get("round_type_label") or ""))
        if round_type:
            meta_parts.append(f"Type: {html.escape(round_type)}")
        membership = clean_text(str(ticket.get("membership_label") or ""))
        if membership:
            meta_parts.append(html.escape(membership))
        if ticket.get("confidence"):
            meta_parts.append(f"confidence {html.escape(str(ticket.get('confidence')))}")
        meta_line = f"<p><small>{' · '.join(meta_parts)}</small></p>" if meta_parts else ""
        evidence_snippet = format_evidence_snippet(ticket.get("evidence"))
        evidence_line = f"<p><small>Evidence: {html.escape(evidence_snippet)}</small></p>" if evidence_snippet != "none" else ""
        status = clean_text(str(ticket.get("status_label") or ticket.get("status") or ""))
        status_badge = f'<span class="status">{html.escape(status)}</span>' if status and status != "unknown" else ""
        return f"""
        <article class="round-card">
          <div class="round-head">
            <h3>{html.escape(str(ticket.get('name') or 'Ticket round'))}</h3>
            {status_badge}
          </div>
          <div class="fact-grid">{fact_grid}</div>
          {meta_line}
          {evidence_line}
          <div class="round-actions">
            {web_source_link(ticket.get('url'), 'Open source')}
            {subscribe_button(event.get('watch_id'), NOTIFY_SCOPE_ROUND, 'Notify me', round_key=str(ticket.get('round_key') or ''), redirect=f"/events/{event_id}")}
          </div>
        </article>
        """

    stops = tour_stops(venues, event_dates)
    # Group rounds by ticket website so membership-specific rounds stay attached
    # to the platform that publishes them.
    rounds_by_platform: dict[str, list[dict[str, object]]] = {}
    for ticket in event.get("rounds", []):
        if not isinstance(ticket, dict):
            continue
        platform = clean_text(str(ticket.get("platform") or ticket.get("source") or "unknown")) or "unknown"
        rounds_by_platform.setdefault(platform, []).append(ticket)

    platform_links: dict[str, list[dict[str, object]]] = {}
    for link in event.get("ticket_links", []):
        if not isinstance(link, dict):
            continue
        platform = clean_text(str(link.get("platform") or "unknown")) or "unknown"
        platform_links.setdefault(platform, []).append(link)
        rounds_by_platform.setdefault(platform, [])

    def platform_sort_key(platform: str) -> tuple[int, str]:
        latest = max((round_latest_date_ordinal(ticket) for ticket in rounds_by_platform.get(platform, [])), default=0)
        return (-latest, platform)

    def render_platform_links(platform: str) -> str:
        links = platform_links.get(platform, [])
        if not links:
            return ""
        return '<div class="round-platform-links">' + "".join(web_source_link(link.get("url"), "Open tickets") for link in links[:3]) + "</div>"

    round_items = "".join(
        f"""
        <div class="round-group">
          <div class="round-group-head">
            <h3>{html.escape(platform)}</h3>
            <small>{len(tickets)} round{'s' if len(tickets) != 1 else ''}</small>
          </div>
          {render_platform_links(platform)}
          <div class="round-group-list">{''.join(render_round_card(ticket) for ticket in tickets) or '<p>No parsed ticket rounds from this website yet.</p>'}</div>
        </div>
        """
        for platform in sorted(rounds_by_platform, key=platform_sort_key)
        for tickets in (rounds_by_platform[platform],)
    ) or "<p>No ticket rounds saved yet.</p>"
    tour_items = "".join(
        f"""
        <li>
          <span><strong>{html.escape(city or venue or 'Venue TBA')}</strong>
          <small>{html.escape(venue)}</small></span>
          <span class="mini-stat wide">{html.escape(date or 'dates TBA')}</span>
          {subscribe_button(event.get('watch_id'), NOTIFY_SCOPE_EVENT_LOCATION, 'Notify', location=(city or venue), redirect=f"/events/{event_id}")}
        </li>
        """
        for city, venue, date in stops
    )
    tour_section = (
        f'<section><h2>Locations &amp; Tour Dates</h2><ul class="tour-list">{tour_items}</ul></section>'
        if len(stops) > 1
        else ""
    )
    event_subscribe = subscribe_button(
        event.get("watch_id"), NOTIFY_SCOPE_EVENT_ALL, "Notify me for all rounds", redirect=f"/events/{event_id}"
    )
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
    related_items = "".join(
        f"""
        <li>
          <span><strong><a href="/events/{html.escape(str(related.get('id')))}">{html.escape(str(related.get('title') or 'Related event'))}</a></strong>
          <small>{html.escape(' · '.join(str(reason) for reason in related.get('recommendation_reasons', [])[:3]))}</small></span>
          {web_source_link(related.get('official_url'), 'Official')}
        </li>
        """
        for related in event.get("related_events", [])
        if isinstance(related, dict)
    )
    related_section = (
        f'<section><h2>Related Saved Events</h2><ul>{related_items}</ul></section>'
        if related_items
        else ""
    )
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
    .round-group {{ display: grid; gap: 10px; }}
    .round-group-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 0 2px; }}
    .round-group-head h3 {{ margin: 0; font-size: 16px; }}
    .round-platform-links {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .round-group-list {{ display: grid; gap: 10px; }}
    .round-card {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; box-shadow: var(--shadow); }}
    .round-actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 10px; }}
    .subscribe-form {{ margin: 0; }}
    button.action-link {{ cursor: pointer; }}
    .round-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; margin-bottom: 10px; }}
    @media (max-width: 720px) {{ header {{ padding: 12px 16px; }} main {{ padding: 18px 16px 42px; }} .summary-grid, .fact-grid {{ grid-template-columns: 1fr; }} li {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header><div class="topbar"><a class="back" href="/" title="Back" aria-label="Back">‹</a><span class="status">{html.escape(str(event.get('status_label') or event.get('status') or 'watching'))}</span></div></header>
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
        <div><small>Organizer</small><strong>{html.escape('; '.join(organizers) or 'Unknown')}</strong></div>
        <div><small>Cast &amp; lineup</small><strong>{html.escape('; '.join(lineup) or 'Unknown')}</strong></div>
      </div>
    </section>
    {tour_section}
    <section><h2>Ticket Rules</h2><ul>{ticket_rule_items}</ul></section>
    <section><h2>Ticket Price</h2><ul>{ticket_price_items}</ul></section>
    <section><h2>Ticket Links</h2><ul>{ticket_link_items}</ul></section>
    <section><h2>Ticket Rounds</h2><div class="round-actions">{event_subscribe}</div><div class="rounds">{round_items}</div></section>
    {related_section}
    <section><h2>Manual Sources</h2><ul>{manual_source_items}</ul></section>
  </main>
</body>
</html>"""


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
      <button class="action-link" type="submit" title="Notify me">{html.escape(label)}</button>
    </form>
    """


def notification_channels_from_form(form: dict[str, str], default: str) -> str:
    if form.get("channel_editor") != "1":
        return form.get("channels", default)
    selected = [channel for channel in NOTIFY_CHANNELS if form.get(f"channel_{channel}") == "1"]
    return ",".join(selected) or DEFAULT_NOTIFY_CHANNELS


def render_notifications_page(db_path: str, user_id: int = 0) -> str:
    watches = {watch.id: watch.keyword for watch in list_watches(db_path, include_muted=True, user_id=user_id)}
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
        location_text = f" · {html.escape(subscription.location)}" if subscription.location else ""
        account_note = (
            "Account subscriptions support private feed and push delivery."
            if user_id > 0
            else "Slack, Discord, and LINE use the server's local-workspace credentials."
        )
        return f"""
        <li class="subscription-row">
          <div class="subscription-copy"><strong>{html.escape(watches.get(subscription.watch_id, str(subscription.watch_id)))}</strong>
          <small>{html.escape(NOTIFY_SCOPE_LABELS.get(subscription.scope, subscription.scope))}{location_text} · {html.escape(subscription.channels)} · lead {html.escape(subscription.lead_days)}</small></div>
          <details class="subscription-editor">
            <summary>Edit delivery</summary>
            <form method="post" action="/subscribe">
              <input type="hidden" name="channel_editor" value="1">
              <input type="hidden" name="watch" value="{subscription.watch_id}">
              <input type="hidden" name="scope" value="{html.escape(subscription.scope)}">
              <input type="hidden" name="location" value="{html.escape(subscription.location, quote=True)}">
              <input type="hidden" name="round_key" value="{html.escape(subscription.round_key, quote=True)}">
              <input type="hidden" name="redirect" value="/notifications">
              <fieldset><legend>Channels</legend><div class="channel-options">{''.join(channel_controls)}</div></fieldset>
              <label class="lead-days">Reminder days before <input name="lead_days" value="{html.escape(subscription.lead_days, quote=True)}" inputmode="numeric" pattern="[0-9, ]+" required></label>
              <small>{account_note}</small>
              <button class="action-link" type="submit">Save delivery</button>
            </form>
          </details>
          <form method="post" action="/subscribe/remove">
            <input type="hidden" name="identifier" value="{subscription.id}">
            <input type="hidden" name="redirect" value="/notifications">
            <button class="action-link" type="submit">Remove</button>
          </form>
        </li>
        """

    subscription_items = "".join(render_subscription_item(subscription) for subscription in subscriptions) or "<li>No subscriptions yet. Open an event and tap “Notify me”.</li>"
    feed_items = "".join(
        f"""
        <li>
          <span><strong>{html.escape(str(item.get('title') or ''))}</strong>
          <small>{html.escape(str(item.get('body') or ''))}</small>
          <small>{html.escape(str(item.get('created_at') or ''))} · {html.escape(','.join(channel for channel, ok in (item.get('delivered') or {}).items() if ok))}</small></span>
        </li>
        """
        for item in feed
    ) or "<li>No reminders yet. They appear here as ticket dates approach.</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Notifications</title>
  <style>
    :root {{ --ink: #202126; --muted: #667085; --line: #d9dee8; --paper: #f6f7fb; --panel: #ffffff; --accent-strong: #9b2446; --shadow: 0 10px 28px rgba(28, 36, 52, 0.08); }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }}
    a {{ color: var(--accent-strong); font-weight: 850; text-decoration: none; }}
    header {{ background: rgba(255,255,255,0.96); border-bottom: 1px solid var(--line); padding: 16px 24px; }}
    .topbar, main {{ max-width: 900px; margin: 0 auto; }}
    .topbar {{ display: flex; align-items: center; gap: 12px; }}
    main {{ padding: 28px 24px 56px; display: grid; gap: 18px; }}
    .back {{ display: inline-flex; align-items: center; min-height: 36px; padding: 7px 10px; border-radius: 8px; background: white; border: 1px solid var(--line); color: var(--ink); font-weight: 850; }}
    section {{ border-top: 1px solid var(--line); padding-top: 18px; }}
    h1, h2 {{ margin-top: 0; }}
    ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
    li {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 13px; box-shadow: var(--shadow); overflow-wrap: anywhere; }}
    small {{ display: block; color: var(--muted); line-height: 1.45; }}
    .action-link {{ display: inline-flex; align-items: center; min-height: 32px; padding: 6px 10px; border-radius: 8px; background: white; border: 1px solid var(--line); color: var(--ink); font-size: 13px; font-weight: 850; cursor: pointer; }}
    form {{ margin: 0; }}
    .subscription-copy {{ min-width: 180px; flex: 1 1 240px; }}
    .subscription-editor {{ flex: 2 1 360px; }}
    .subscription-editor summary {{ cursor: pointer; font-weight: 850; }}
    .subscription-editor form {{ margin-top: 10px; display: grid; gap: 10px; }}
    fieldset {{ margin: 0; border: 1px solid var(--line); border-radius: 8px; }}
    legend {{ color: var(--muted); font-size: 13px; font-weight: 800; }}
    .channel-options {{ display: flex; flex-wrap: wrap; gap: 8px 14px; }}
    .channel-options label, .lead-days {{ font-size: 14px; font-weight: 700; }}
    .lead-days {{ display: grid; gap: 5px; }}
    .lead-days input {{ min-height: 36px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 8px; font: inherit; }}
    @media (max-width: 680px) {{ li.subscription-row {{ flex-direction: column; }} .subscription-editor {{ width: 100%; }} }}
  </style>
</head>
<body>
  <header><div class="topbar"><a class="back" href="/" title="Back">‹</a><strong>Notifications</strong></div></header>
  <main>
    <section><h1>Subscriptions</h1><ul>{subscription_items}</ul></section>
    <section><h2>Recent reminders</h2><ul>{feed_items}</ul></section>
  </main>
</body>
</html>"""


def send_security_headers(handler: http.server.BaseHTTPRequestHandler) -> None:
    """Apply browser/API protections consistently to every response shape."""
    handler.send_header("Cache-Control", "no-store")
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


def text_response(handler: http.server.BaseHTTPRequestHandler, body: str, content_type: str, status: int = 200) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    send_security_headers(handler)
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


def render_watch_preferences(watch: Watch, include_alerts: bool = False) -> str:
    parts = [
        f"tags {watch.tags or 'none'}",
        f"regions {watch.preferred_regions or 'none'}",
        f"venues {watch.preferred_venues or 'none'}",
    ]
    if include_alerts:
        parts.append(f"alerts {human_alert_preferences(watch.alert_preferences)}")
    parts.append(f"last checked {watch.last_checked_at or 'never'}")
    return " | ".join(parts)


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
        f'<button class="action-link" type="button" data-edit-watch {encoded} '
        'title="Edit watch preferences" aria-label="Edit watch preferences">Edit</button>'
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
    upcoming_rows = upcoming_priority_rows(db_path, limit=6, user_id=user_id)
    latest_event_by_watch_id = {
        int(event["watch_id"]): event
        for event in reversed(events)
        if event.get("watch_kind") == WATCH_KIND_EVENT and event.get("watch_id")
    }
    active_artist_watches = [watch for watch in watches if not watch.muted and watch.kind == WATCH_KIND_ARTIST]
    active_event_watches = [watch for watch in watches if not watch.muted and watch.kind == WATCH_KIND_EVENT]
    event_count_by_artist_id: dict[int, int] = {}
    for event in events:
        if event.get("watch_kind") != WATCH_KIND_ARTIST or not event.get("watch_id"):
            continue
        artist_id = int(event["watch_id"])
        event_count_by_artist_id[artist_id] = event_count_by_artist_id.get(artist_id, 0) + 1
    artist_items = "\n".join(
        f"""
        <li class="watch-row">
          <span class="watch-copy"><a class="watch-title" href="/artists/{watch.id}" title="Open artist events">{html.escape(watch.keyword)}</a> <small>#{watch.id} | {html.escape(render_watch_preferences(watch))} | {event_count_by_artist_id.get(watch.id, 0)} results</small></span>
          <span class="row-actions"><a class="action-link" href="/artists/{watch.id}" title="Open artist events" aria-label="Open artist events">Open</a>{render_watch_edit_button(watch)}<form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><button class="icon-button danger" title="Remove artist" aria-label="Remove artist"><span aria-hidden="true">x</span></button></form></span>
        </li>
        """
        for watch in active_artist_watches
    ) or '<li class="empty-row">No tracked artists.</li>'
    def render_tracked_event_item(watch: Watch) -> str:
        event = latest_event_by_watch_id.get(watch.id)
        if not event:
            return f"""
        <li class="watch-row">
          <span class="watch-copy"><strong>{html.escape(watch.keyword)}</strong> <small>#{watch.id} | not searched yet | {html.escape(render_watch_preferences(watch, include_alerts=True))}</small></span>
          <span class="row-actions">{render_watch_edit_button(watch)}<form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><button class="icon-button danger" title="Remove event" aria-label="Remove event"><span aria-hidden="true">x</span></button></form></span>
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
            <small>{html.escape(watch.keyword)} | {html.escape(render_watch_preferences(watch, include_alerts=True))}</small>
            <span class="watch-meta">
              <span class="mini-stat" title="Official page">{html.escape(official_label)}</span>
              <span class="mini-stat" title="Ticket links">Tickets {ticket_count}</span>
              <span class="mini-stat" title="Lottery rounds">Rounds {round_count}</span>
              <span class="mini-stat wide" title="First date clue">Date {html.escape(date_label)}</span>
            </span>
          </span>
          <span class="row-actions"><a class="action-link" href="{detail_url}" title="Open event details" aria-label="Open event details">Open</a>{render_watch_edit_button(watch)}<form method="post" action="/watch/remove"><input type="hidden" name="identifier" value="{watch.id}"><button class="icon-button danger" title="Remove event" aria-label="Remove event"><span aria-hidden="true">x</span></button></form></span>
        </li>
        """

    active_event_watches.sort(key=lambda watch: tracked_event_display_key(watch, latest_event_by_watch_id.get(watch.id)))
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
              <span class="mini-stat" title="Ticket status">{html.escape(str(row.get('status_label') or row.get('status') or 'unknown'))}</span>
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
    active_dashboard_tab = selected_tab if selected_tab in {"attention", "artists", "events"} else "events" if event_search_keyword or event_search_error else "attention"
    if account_email:
        account_panel = f"""
        <div class="account-panel signed-in">
          <span>Signed in as <strong>{html.escape(account_email)}</strong></span>
          <form method="post" action="/account/logout"><button class="secondary-button" type="submit">Log out</button></form>
        </div>
        """
    else:
        account_panel = f"""
        <details class="account-panel"{' open' if account_error else ''}>
          <summary>Sign in or create an account</summary>
          {f'<p class="message">{html.escape(account_error)}</p>' if account_error else ''}
          <form method="post" action="/account/login">
            <input name="email" type="email" autocomplete="username" placeholder="Email" maxlength="{MAX_EMAIL_LENGTH}" required>
            <input name="password" type="password" autocomplete="current-password" placeholder="Password" minlength="8" maxlength="{MAX_PASSWORD_LENGTH}" required>
            <button class="secondary-button" type="submit">Log in</button>
            <button class="secondary-button" type="submit" formaction="/account/register">Register</button>
          </form>
        </details>
        """
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
    .account-panel {{ margin: 0 0 18px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; box-shadow: var(--shadow); }}
    .account-panel summary {{ cursor: pointer; color: var(--ink); font-weight: 900; }}
    .account-panel form {{ margin-top: 10px; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto auto; }}
    .account-panel.signed-in {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .account-panel.signed-in form {{ display: block; margin: 0; }}
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
    .watch-editor {{ margin: 0 0 14px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    .watch-editor summary {{ cursor: pointer; font-weight: 900; }}
    .watch-editor form {{ margin: 10px 0 7px; }}
    @media (max-width: 820px) {{
      header {{ padding: 12px 16px; }}
      main {{ padding: 18px 16px 42px; }}
      .dashboard-intro {{ align-items: flex-start; flex-direction: column; }}
      .summary-strip {{ justify-content: flex-start; }}
      .account-panel form {{ grid-template-columns: 1fr; }}
      .account-panel.signed-in {{ align-items: flex-start; flex-direction: column; }}
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
      <a class="back" href="/notifications" title="Notifications">Notifications</a>
    </div>
  </header>
  <main>
    {account_panel}
    <div class="dashboard-intro">
      <div>
        <h1>Watch dashboard</h1>
        <p>Track official pages, ticket links, and lottery rounds from one place.</p>
      </div>
      <div class="summary-strip" aria-label="Dashboard summary">
        <span class="summary-pill">{len(active_artist_watches)} active artists</span>
        <span class="summary-pill">{len(active_event_watches)} active events</span>
        <span class="summary-pill">{len(events)} saved events</span>
        <span class="summary-pill" title="Server release">v{html.escape(APP_VERSION)} ({APP_BUILD}) · schema {DB_SCHEMA_VERSION}</span>
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
      <details class="watch-editor" id="artist-watch-editor">
        <summary>Add or update an artist watch</summary>
        <form method="post" action="/watch/add">
          <input type="hidden" name="kind" value="artist">
          <input id="artist-watch-keyword" name="keyword" placeholder="Artist or production company" aria-label="Artist or production company" maxlength="{MAX_KEYWORD_LENGTH}" required>
          <input id="artist-watch-tags" name="tags" placeholder="Tags, comma separated" aria-label="Tags">
          <input id="artist-watch-regions" name="regions" placeholder="Preferred regions" aria-label="Preferred regions">
          <input id="artist-watch-venues" name="venues" placeholder="Preferred venues" aria-label="Preferred venues">
          <input id="artist-watch-alerts" name="alerts" placeholder="Alert types, comma separated" aria-label="Alert types" value="{html.escape(DEFAULT_ALERT_PREFERENCES)}">
          <button class="secondary-button" title="Save artist watch" aria-label="Save artist watch">Save</button>
        </form>
        <small>Enter an existing artist keyword to update only your account's filters and alerts.</small>
      </details>
      <form class="run-form" method="post" action="/watch/run"><input type="hidden" name="kind" value="artist"><button class="secondary-button" title="Run artists" aria-label="Run artists">Run artists</button></form>
      <ul>{artist_items}</ul>
    </section>
    <section class="dashboard-panel {'is-active' if active_dashboard_tab == 'events' else ''}" id="panel-events" role="tabpanel" aria-labelledby="tab-events" data-tab-panel="events">
      <div class="section-head"><h2>Tracked Events</h2><span class="status">{len(active_event_watches)} active</span></div>
      <details class="watch-editor" id="event-watch-editor">
        <summary>Add or update a keyword watch</summary>
        <form method="post" action="/watch/add">
          <input type="hidden" name="kind" value="event">
          <input id="event-watch-keyword" name="keyword" placeholder="Event keyword" aria-label="Event keyword" maxlength="{MAX_KEYWORD_LENGTH}" required>
          <input id="event-watch-tags" name="tags" placeholder="Tags, comma separated" aria-label="Tags">
          <input id="event-watch-regions" name="regions" placeholder="Preferred regions" aria-label="Preferred regions">
          <input id="event-watch-venues" name="venues" placeholder="Preferred venues" aria-label="Preferred venues">
          <input id="event-watch-alerts" name="alerts" placeholder="Alert types, comma separated" aria-label="Alert types" value="{html.escape(DEFAULT_ALERT_PREFERENCES)}">
          <button class="secondary-button" title="Save watch" aria-label="Save watch">Save</button>
        </form>
        <small>Enter an existing keyword to update only your account's filters and alerts.</small>
      </details>
      <form method="post" action="/event/search">
        <input name="keyword" placeholder="Search exact event" value="{html.escape(event_search_keyword)}" maxlength="{MAX_KEYWORD_LENGTH}" required>
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
    document.querySelectorAll('[data-edit-watch]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const kind = button.dataset.kind === 'artist' ? 'artist' : 'event';
        showDashboardTab(kind === 'artist' ? 'artists' : 'events');
        const editor = document.getElementById(`${{kind}}-watch-editor`);
        if (editor) editor.open = true;
        ['keyword', 'tags', 'regions', 'venues', 'alerts'].forEach((field) => {{
          const input = document.getElementById(`${{kind}}-watch-${{field}}`);
          if (input) input.value = button.dataset[field] || '';
        }});
        document.getElementById(`${{kind}}-watch-keyword`)?.focus();
      }});
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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · Chusennote</title>
  <style>
    :root {{ --ink: #202126; --muted: #667085; --line: #d9dee8; --paper: #f6f7fb; --panel: #ffffff; --accent: #9b2446; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--paper); color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; }}
    header {{ border-bottom: 1px solid var(--line); background: var(--panel); }}
    header div, main, footer {{ width: min(760px, calc(100% - 32px)); margin: 0 auto; }}
    header div {{ display: flex; align-items: center; justify-content: space-between; min-height: 64px; gap: 16px; }}
    main {{ padding: 36px 0 48px; }}
    article {{ padding: 28px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel); }}
    h1 {{ margin: 0 0 8px; line-height: 1.2; }}
    h2 {{ margin-top: 28px; line-height: 1.3; }}
    p, li {{ color: #3f4653; }}
    a {{ color: var(--accent); font-weight: 750; }}
    .effective {{ margin-top: 0; color: var(--muted); }}
    footer {{ padding: 0 0 36px; color: var(--muted); }}
    footer a {{ margin-right: 16px; }}
    @media (max-width: 600px) {{ article {{ padding: 21px; }} main {{ padding-top: 22px; }} }}
  </style>
</head>
<body>
  <header><div><a href="/">Chusennote</a><strong>{html.escape(title)}</strong></div></header>
  <main><article>{content}</article></main>
  <footer><a href="/privacy">Privacy</a><a href="/support">Support</a></footer>
</body>
</html>"""


def render_privacy_page() -> str:
    return render_information_page(
        "Privacy Policy",
        """
        <h1>Privacy Policy</h1>
        <p class="effective">Effective September 22, 2026</p>
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
            elif path == "/notifications":
                html_response(self, render_notifications_page(db_path, self.browser_user_id()))
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
                        render_web_page(
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
                        html_response(self, render_web_page(db_path, account_error=str(error)), status=400)
                        return
                else:
                    user = verify_user(db_path, email, password)
                    if user is None:
                        html_response(self, render_web_page(db_path, account_error="Invalid credentials."), status=401)
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
                redirect_response(self, f"/?tab={'artists' if form.get('kind') == WATCH_KIND_ARTIST else 'events'}")
            elif path == "/watch/remove":
                remove_watch(db_path, form.get("identifier", ""), user_id=self.browser_user_id())
                redirect_response(self)
            elif path == "/watch/unmute":
                set_watch_muted(db_path, form.get("identifier", ""), False, user_id=self.browser_user_id())
                redirect_response(self)
            elif path == "/watch/run":
                kind = form.get("kind") or None
                run_watches(db_path, kind=kind, user_id=self.browser_user_id())
                redirect_response(self, f"/?tab={'artists' if kind == WATCH_KIND_ARTIST else 'events' if kind == WATCH_KIND_EVENT else 'attention'}")
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
                redirect_response(self, safe_web_redirect(form.get("redirect", ""), "/notifications"))
            elif path == "/subscribe/remove":
                identifier = form.get("identifier", "")
                if str(identifier).isdigit():
                    remove_subscription(db_path, int(identifier), user_id=self.browser_user_id())
                redirect_response(self, safe_web_redirect(form.get("redirect", ""), "/notifications"))
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
