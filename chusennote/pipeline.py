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
import urllib.parse
from collections.abc import Sequence

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403
from .crud import *  # noqa: F401,F403
from .read_models import *  # noqa: F401,F403


SITE_DISCOVERY_HINTS = (
    "ticket", "live", "tour", "schedule", "event", "news", "information",
    "チケット", "公演", "ライブ", "ツアー", "スケジュール", "先行", "抽選", "発売",
)


def same_public_site(base_url: str, candidate_url: str) -> bool:
    try:
        return (
            len(candidate_url) <= MAX_SOURCE_VALUE_LENGTH
            and is_public_fetch_url(candidate_url)
            and urllib.parse.urlparse(base_url).hostname == urllib.parse.urlparse(candidate_url).hostname
        )
    except ValueError:
        return False


def site_discovery_manifests(page: Page, limit: int = 4) -> list[str]:
    links = list(page.discovery_links)
    links.extend(
        link
        for link in page.links
        if any(marker in f"{link.label} {link.url}".lower() for marker in ("sitemap", "rss", "atom", "/feed"))
    )
    manifests: list[str] = []
    for link in links:
        if link.url not in manifests and same_public_site(page.url, link.url):
            manifests.append(link.url)
        if len(manifests) >= limit:
            break
    return manifests


def fetch_site_discovery_pages(page: Page, keyword: str, limit: int = 4) -> list[Page]:
    """Follow bounded, declared same-site RSS/Atom/sitemap links from an official page."""
    manifest_queue = site_discovery_manifests(page)
    seen_manifests: set[str] = set()
    candidates: list[Link] = []
    while manifest_queue and len(seen_manifests) < 6:
        manifest_url = manifest_queue.pop(0)
        if manifest_url in seen_manifests:
            continue
        seen_manifests.add(manifest_url)
        try:
            xml_text = request_html(manifest_url)
        except (OSError, ValueError):
            continue
        pages, nested_manifests = parse_discovery_document(manifest_url, xml_text)
        candidates.extend(link for link in pages if same_public_site(page.url, link.url))
        manifest_queue.extend(
            url
            for url in nested_manifests
            if url not in seen_manifests and same_public_site(page.url, url)
        )

    ranked: list[tuple[int, int, Link]] = []
    seen_urls: set[str] = {page.url}
    for index, link in enumerate(candidates):
        if link.url in seen_urls:
            continue
        seen_urls.add(link.url)
        haystack = f"{link.label} {urllib.parse.unquote(link.url)}"
        score = (10 if keyword_matches_text(keyword, haystack) else 0) + sum(
            1 for hint in SITE_DISCOVERY_HINTS if hint in haystack.lower()
        )
        if score:
            ranked.append((score, -index, link))

    discovered: list[Page] = []
    for _, _, link in sorted(ranked, reverse=True):
        try:
            candidate_page = fetch_page(link.url)
        except (OSError, ValueError):
            continue
        if page_matches_keyword(keyword, candidate_page):
            discovered.append(candidate_page)
        if len(discovered) >= limit:
            break
    return discovered


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

    for page in tuple(official_pages):
        official_pages.extend(fetch_site_discovery_pages(page, keyword))

    event_info = build_event_info(keyword, official_pages)
    rounds: list[TicketRound] = []
    for page in official_pages:
        rounds.extend(extract_ticket_rounds_for_page(page))
    rounds.extend(fetch_ticket_link_rounds(event_info.ticket_links))
    rounds = list(clear_performance_window_rounds(rounds, event_info.event_dates))
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
    event_keyword = keyword or title or page.title
    pages = (page, *fetch_site_discovery_pages(page, event_keyword))
    event_info = build_event_info(event_keyword, pages)
    if not event_info.title:
        event_info = dataclasses.replace(event_info, title=title or page.title)
    rounds: list[TicketRound] = []
    for candidate_page in pages:
        rounds.extend(extract_ticket_rounds_for_page(candidate_page))
    rounds.extend(fetch_ticket_link_rounds(event_info.ticket_links))
    if snippet and not event_info.summary:
        event_info = dataclasses.replace(event_info, summary=snippet)
    rounds = list(clear_performance_window_rounds(rounds, event_info.event_dates))
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
    for page in tuple(official_pages):
        official_pages.extend(fetch_site_discovery_pages(page, keyword))
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


# A tour listing often names no venue per show; the venue lives on a per-show
# detail page linked from the schedule. Following those costs a fetch each, so
# cap how many we chase per artist run.
ARTIST_VENUE_LOOKUP_LIMIT = 6


def tour_detail_url(page: Page, title: str) -> str | None:
    """A per-show detail link on a schedule page whose label names the show.

    Returns ``None`` when no link clearly belongs to the show (e.g. multi-city
    tours that only carry nav/social links), so enrichment is a safe no-op
    rather than guessing a wrong page.
    """
    title_key = clean_text(title)
    if len(title_key) < 4:
        return None
    for link in page.links:
        if is_noisy_url(link.url) or link.url == page.url:
            continue
        label = clean_text(link.label)
        if len(label) < 4 or any(hint in link.url.lower() for hint in ("/news", "/profile", "/biography")):
            continue
        if keyword_matches_text(title_key, label) or keyword_matches_text(label, title_key):
            return link.url
    return None


def venue_from_detail_page(url: str) -> str:
    """Fetch a show's detail page and read a venue/city from it."""
    try:
        page = fetch_page(url)
    except (OSError, ValueError):
        return ""
    venues = extract_venues(page.text)
    if venues:
        return clean_text(str(venues[0]))
    return tour_venue_from_window(page.text[:400])


def build_artist_event_blocks(keyword: str, limit: int = 8) -> list[AppBlocks]:
    blocks: list[AppBlocks] = []
    seen_shows: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    venue_lookups = 0
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
        schedule_pages = fetch_schedule_pages(page) or fetch_site_discovery_pages(page, keyword)
        for schedule_page in (schedule_pages or [page]):
            for entry in extract_tour_dates(schedule_page):
                if entry.get("ended"):
                    continue
                key = (entry["date"], entry["title"])
                if key in seen_shows:
                    continue
                seen_shows.add(key)
                if not entry.get("venue") and venue_lookups < ARTIST_VENUE_LOOKUP_LIMIT:
                    detail_url = tour_detail_url(schedule_page, entry.get("title", ""))
                    if detail_url:
                        venue_lookups += 1
                        venue = venue_from_detail_page(detail_url)
                        if venue:
                            entry = {**entry, "venue": venue}
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
        for discovered_page in fetch_site_discovery_pages(page, watch.keyword):
            source_pages.append(discovered_page)
            extra_rounds.extend(extract_ticket_rounds_for_page(discovered_page))
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
    ticket_link_rounds = fetch_ticket_link_rounds(info.ticket_links) if source_pages else []
    all_rounds = clear_performance_window_rounds(
        base_rounds + tuple(extra_rounds) + tuple(ticket_link_rounds), merged_info.event_dates
    )
    return AppBlocks(general_info=merged_info, ticket_info=dedupe_ticket_rounds(all_rounds))


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


def run_watches(
    db_path: str,
    now: str | None = None,
    kind: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, str]]:
    timestamp = now or utc_now_iso()
    alerts: list[dict[str, str]] = []
    for watch in list_watches(db_path, kind=kind, user_id=user_id):
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
                saved_alerts = save_blocks(db_path, blocks, now=timestamp, watch_id=watch.id)
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
    notify_func=None,
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
                reminders = notify_func(db_path) if notify_func else []
                failed_reminders = sum(
                    isinstance(item.get("delivered"), dict)
                    and any(value is False for value in item["delivered"].values())
                    for item in reminders
                )
                if alerts_json:
                    payload = {"run": run_count, "alerts": alerts}
                    if notify_func:
                        payload["reminders"] = len(reminders)
                        payload["reminder_delivery_failures"] = failed_reminders
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    scope = kind or "all"
                    if notify_func and failed_reminders:
                        reminder_note = (
                            f" {len(reminders)} reminders processed; "
                            f"{failed_reminders} awaiting external delivery retry."
                        )
                    else:
                        reminder_note = f" {len(reminders)} reminders sent." if notify_func else ""
                    print(f"Run {run_count}: checked {scope} watches; {len(alerts)} alerts.{reminder_note}")
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
