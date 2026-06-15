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
import http.server
import json
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


def render_event_detail_page(db_path: str, event_id: int) -> str:
    event = event_detail(db_path, event_id)
    if not event:
        return "<!doctype html><title>Not found</title><h1>Event not found</h1>"
    event_dates = [clean_text(str(item)) for item in event.get("event_dates", []) if clean_text(str(item))]
    venues = [clean_text(str(item)) for item in event.get("venues", []) if clean_text(str(item))]
    time_label = "; ".join(event_dates[:3]) if event_dates else "Unknown"
    venue_label = "; ".join(venues[:3]) if venues else "Unknown"
    location_label = infer_event_location(venues) or "Unknown"
    summary_text = str(event.get("summary") or "")
    ticket_rules = extract_ticket_rule_items(summary_text)
    ticket_prices = extract_ticket_price_items(summary_text)
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
        return f"""
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
          <p><small>Evidence: {html.escape(format_evidence_snippet(ticket.get('evidence')))}</small></p>
          {web_source_link(ticket.get('url'), 'Open source')}
        </article>
        """

    rounds_by_platform: dict[str, list[dict[str, object]]] = {}
    for ticket in event.get("rounds", []):
        if not isinstance(ticket, dict):
            continue
        platform = clean_text(str(ticket.get("platform") or ticket.get("source") or "unknown")) or "unknown"
        rounds_by_platform.setdefault(platform, []).append(ticket)
    round_items = "".join(
        f"""
        <div class="round-group">
          <div class="round-group-head">
            <h3>{html.escape(platform)}</h3>
            <small>{len(tickets)} round{'s' if len(tickets) != 1 else ''}</small>
          </div>
          <div class="round-group-list">{''.join(render_round_card(ticket) for ticket in tickets)}</div>
        </div>
        """
        for platform, tickets in rounds_by_platform.items()
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
    .round-group {{ display: grid; gap: 10px; }}
    .round-group-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 0 2px; }}
    .round-group-head h3 {{ margin: 0; font-size: 16px; }}
    .round-group-list {{ display: grid; gap: 10px; }}
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
    <section><h2>Ticket Rules</h2><ul>{ticket_rule_items}</ul></section>
    <section><h2>Ticket Price</h2><ul>{ticket_price_items}</ul></section>
    <section><h2>Ticket Links</h2><ul>{ticket_link_items}</ul></section>
    <section><h2>Lottery Rounds</h2><div class="rounds">{round_items}</div></section>
    <section><h2>Manual Sources</h2><ul>{manual_source_items}</ul></section>
  </main>
</body>
</html>"""


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
    event_count_by_artist_id: dict[int, int] = {}
    for event in events:
        if event.get("watch_kind") != WATCH_KIND_ARTIST or not event.get("watch_id"):
            continue
        artist_id = int(event["watch_id"])
        event_count_by_artist_id[artist_id] = event_count_by_artist_id.get(artist_id, 0) + 1
    artist_items = "\n".join(
        f"""
        <li class="watch-row">
          <span class="watch-copy"><a class="watch-title" href="/artists/{watch.id}" title="Open artist events">{html.escape(watch.keyword)}</a> <small>#{watch.id} | checked {html.escape(watch.last_checked_at or 'never')} | {event_count_by_artist_id.get(watch.id, 0)} results</small></span>
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
    active_dashboard_tab = selected_tab if selected_tab in {"attention", "artists", "events"} else "events" if event_search_keyword or event_search_error else "attention"
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
                html_response(self, render_web_page(db_path, selected_tab=query.get("tab", [""])[0]))
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
                redirect_response(self, f"/?tab={'artists' if form.get('kind') == WATCH_KIND_ARTIST else 'events'}")
            elif path == "/watch/remove":
                remove_watch(db_path, form.get("identifier", ""))
                redirect_response(self)
            elif path == "/watch/unmute":
                set_watch_muted(db_path, form.get("identifier", ""), False)
                redirect_response(self)
            elif path == "/watch/run":
                kind = form.get("kind") or None
                run_watches(db_path, kind=kind)
                redirect_response(self, f"/?tab={'artists' if kind == WATCH_KIND_ARTIST else 'events' if kind == WATCH_KIND_EVENT else 'attention'}")
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
