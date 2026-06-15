"""Discovery pipeline and watch orchestration for chusennote.

Turns a keyword into populated app blocks (web search -> official-page fetch ->
event/ticket extraction) and runs batch passes over the watchlist, persisting
results and emitting alerts. Builds on the persistence layer
(:mod:`chusennote.schema`, :mod:`chusennote.crud`, :mod:`chusennote.read_models`);
that dependency is one-directional, so persistence has no knowledge of this module.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from collections.abc import Sequence

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403
from .crud import *  # noqa: F401,F403
from .read_models import *  # noqa: F401,F403


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
    for page in official_pages:
        rounds.extend(extract_ticket_rounds_for_page(page))
    rounds.extend(fetch_ticket_link_rounds(event_info.ticket_links))
    return AppBlocks(general_info=event_info, ticket_info=dedupe_ticket_rounds(rounds))


def fetch_ticket_link_rounds(links: Sequence[Link]) -> list[TicketRound]:
    rounds: list[TicketRound] = []
    for link in links:
        if is_portal_search_url(link.url):
            continue
        # A dead (404) or unreachable link contributes no rounds rather than a
        # "Fetch failed" placeholder, which only cluttered the round list.
        try:
            rounds.extend(extract_ticket_rounds_for_page(fetch_page(link.url)))
        except (OSError, ValueError):
            continue
    return rounds


def build_exact_event_blocks(keyword: str, title: str, url: str, snippet: str = "") -> AppBlocks:
    page = fetch_page(url)
    event_info = build_event_info(keyword or title or page.title, (page,))
    if not event_info.title:
        event_info = dataclasses.replace(event_info, title=title or page.title)
    rounds: list[TicketRound] = list(extract_ticket_rounds_for_page(page))
    rounds.extend(fetch_ticket_link_rounds(event_info.ticket_links))
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


def fetch_schedule_pages(page: Page, limit: int = 3) -> list[Page]:
    """Follow up to ``limit`` live/tour/schedule links from an official page."""
    pages: list[Page] = []
    seen: set[str] = set()
    for link in page.links:
        if len(pages) >= limit:
            break
        haystack = f"{link.label} {link.url}".lower()
        if not any(hint in haystack for hint in SCHEDULE_LINK_HINTS):
            continue
        if link.url == page.url or link.url in seen or is_noisy_url(link.url):
            continue
        seen.add(link.url)
        try:
            pages.append(fetch_page(link.url))
        except (OSError, ValueError):
            continue
    return pages


def artist_show_block(keyword: str, schedule_url: str, entry: dict[str, str]) -> AppBlocks:
    venue = entry.get("venue", "")
    title = entry.get("title", "")
    date_text = entry.get("date_text", "")
    iso_date = entry.get("date", "")
    # A per-show fragment keeps each date a distinct event under the artist
    # (events are keyed by watch_id + official_url) while staying clickable.
    fragment = f"{iso_date.replace('-', '')}-{stable_hash(f'{iso_date}|{title or venue}')[:6]}"
    info = EventInfo(
        keyword=keyword,
        official_page=f"{schedule_url}#{fragment}",
        title=title or venue or f"{keyword} live",
        summary=" ".join(part for part in (date_text, title, venue) if part),
        event_dates=(date_text or iso_date,),
        venues=(venue,) if venue else (),
        ticket_links=(),
    )
    return AppBlocks(general_info=info, ticket_info=())


def build_artist_event_blocks(keyword: str, limit: int = 8) -> list[AppBlocks]:
    blocks: list[AppBlocks] = []
    seen_shows: set[tuple[str, str]] = set()
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
        # Prefer a dedicated live/tour page; the landing page mixes news dates
        # that are not shows. Fall back to the landing page only if there is no
        # schedule sub-page.
        schedule_pages = fetch_schedule_pages(page)
        for schedule_page in (schedule_pages or [page]):
            for entry in extract_tour_dates(schedule_page):
                if entry.get("ended"):
                    continue
                key = (entry["date"], entry["title"])
                if key in seen_shows:
                    continue
                seen_shows.add(key)
                blocks.append(artist_show_block(keyword, schedule_page.url, entry))
    if not blocks:
        ticket_links = portal_search_links(keyword)
        info = EventInfo(
            keyword=keyword,
            official_page=ticket_links[0].url if ticket_links else None,
            title=f"{keyword} ticket search",
            summary="No upcoming shows found yet; trusted ticket portal searches for this artist.",
            event_dates=(),
            venues=(),
            ticket_links=ticket_links,
        )
        blocks.append(AppBlocks(general_info=info, ticket_info=()))
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
    ticket_link_rounds = fetch_ticket_link_rounds(info.ticket_links)
    return AppBlocks(general_info=merged_info, ticket_info=dedupe_ticket_rounds(base_rounds + tuple(extra_rounds) + tuple(ticket_link_rounds)))


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
