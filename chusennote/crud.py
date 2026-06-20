"""Write operations and watch/event/source data access for chusennote.

Holds the watch/source/event/ticket-round upserts, mutations, row mappers, the
alert-recording path, and the write-side domain helpers. Builds on
:mod:`chusennote.schema`; it never calls into the read-model views, keeping the
persistence layering acyclic.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import urllib.parse

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403


def blocks_to_json(blocks: AppBlocks) -> str:
    return json.dumps(dataclasses.asdict(blocks), ensure_ascii=False, indent=2)


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
        INSERT INTO events(
            watch_id, canonical_title, official_url, summary, event_dates_json, venues_json,
            ticket_rules_json, ticket_prices_json, status, event_key, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(watch_id, official_url) DO UPDATE SET
            canonical_title = excluded.canonical_title,
            summary = excluded.summary,
            event_dates_json = excluded.event_dates_json,
            venues_json = excluded.venues_json,
            ticket_rules_json = excluded.ticket_rules_json,
            ticket_prices_json = excluded.ticket_prices_json,
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
            json.dumps(list(info.ticket_rules), ensure_ascii=False),
            json.dumps(list(info.ticket_prices), ensure_ascii=False),
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


def delete_stale_search_fallback_events(connection: sqlite3.Connection, watch_id: int, current_event_id: int) -> None:
    """Remove a "ticket search" portal-search fallback once a real event exists.

    The artist lane saves a portal-search link only when no shows are found;
    once a genuine event is discovered for the watch, that fallback is stale.
    """
    rows = connection.execute(
        "SELECT id, official_url FROM events WHERE watch_id = ? AND id != ?",
        (watch_id, current_event_id),
    ).fetchall()
    for event_id, official_url in rows:
        if not is_portal_search_url(str(official_url)):
            continue
        for table in ("sources", "ticket_rounds", "snapshots", "alert_log"):
            connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (int(event_id),))
        connection.execute("DELETE FROM events WHERE id = ?", (int(event_id),))


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
            "event_venues": 0,
            "ticket_round_dates": 0,
            "ticket_round_memberships": 0,
        }
        for table in ("sources", "ticket_rounds", "snapshots", "alert_log"):
            cursor = connection.execute(
                f"""
                DELETE FROM {table}
                WHERE event_id NOT IN (SELECT id FROM events)
                """
            )
            counts[table] += cursor.rowcount if cursor.rowcount >= 0 else 0
        # Legacy "Fetch failed" placeholder rounds are no longer written; remove
        # any that linger from older runs.
        cursor = connection.execute("DELETE FROM ticket_rounds WHERE name = 'Fetch failed'")
        counts["ticket_rounds"] += cursor.rowcount if cursor.rowcount >= 0 else 0
        stale_sources = connection.execute(
            """
            SELECT id, url, label
            FROM sources
            """
        ).fetchall()
        for source_id, url, label in stale_sources:
            if is_actionable_ticket_link(str(url), str(label or "")):
                continue
            cursor = connection.execute("DELETE FROM sources WHERE id = ?", (int(source_id),))
            counts["sources"] += cursor.rowcount if cursor.rowcount >= 0 else 0
        cursor = connection.execute(
            """
            DELETE FROM watch_sources
            WHERE watch_id NOT IN (SELECT id FROM watched_keywords)
            """
        )
        counts["watch_sources"] += cursor.rowcount if cursor.rowcount >= 0 else 0

        stale_fallbacks = connection.execute(
            """
            SELECT fallback.id, official.id
            FROM events AS fallback
            JOIN events AS official
              ON official.watch_id = fallback.watch_id
             AND official.id != fallback.id
             AND (
                official.official_url LIKE 'http://%'
                OR official.official_url LIKE 'https://%'
             )
            WHERE fallback.official_url LIKE 'keyword:%'
            """
        ).fetchall()
        for row in stale_fallbacks:
            event_id = int(row[0])
            official_event_id = int(row[1])
            connection.execute(
                """
                UPDATE OR IGNORE sources
                SET event_id = ?
                WHERE event_id = ?
                """,
                (official_event_id, event_id),
            )
            for table in ("sources", "ticket_rounds", "snapshots", "alert_log"):
                cursor = connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))
                counts[table] += cursor.rowcount if cursor.rowcount >= 0 else 0
            connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
            counts["keyword_fallback_events"] += 1
        venue_rows = connection.execute("SELECT id, venues_json FROM events").fetchall()
        for event_id, venues_json in venue_rows:
            try:
                venues = json.loads(venues_json or "[]")
            except json.JSONDecodeError:
                venues = []
            if not isinstance(venues, list):
                venues = []
            cleaned_venues = [str(venue) for venue in venues if clean_text(str(venue)) and not venue_looks_noisy(str(venue))]
            if cleaned_venues == venues:
                continue
            connection.execute(
                """
                UPDATE events
                SET venues_json = ?
                WHERE id = ?
                """,
                (json.dumps(cleaned_venues, ensure_ascii=False), int(event_id)),
            )
            counts["event_venues"] += 1
        round_rows = connection.execute(
            """
            SELECT id, source, url, name, round_number, platform, lottery_start, lottery_end,
                   results_date, general_sale_date, payment_deadline, application_start_at,
                   application_end_at, payment_start_at, payment_end_at, trade_start_at,
                   trade_end_at, confidence, status, round_type, membership_required, evidence
            FROM ticket_rounds
            """
        ).fetchall()
        for row in round_rows:
            ticket = TicketRound(
                source=row[1],
                url=row[2],
                name=row[3],
                round_number=row[4],
                platform=row[5],
                lottery_start=row[6],
                lottery_end=row[7],
                results_date=row[8],
                general_sale_date=row[9],
                payment_deadline=row[10],
                application_start_at=row[11],
                application_end_at=row[12],
                payment_start_at=row[13],
                payment_end_at=row[14],
                trade_start_at=row[15],
                trade_end_at=row[16],
                confidence=row[17],
                status=row[18],
                round_type=row[19],
                membership_required=row[20],
                evidence=row[21],
            )
            if "会員" in ticket.name and ticket.evidence:
                base_name = round_name_from_context(ticket.evidence, "先行")
                canonical_member_rounds = membership_rounds_from_context(
                    ticket.evidence,
                    base_name,
                    ticket.source,
                    ticket.url,
                    results_date=ticket.results_date,
                    general_sale_date=ticket.general_sale_date,
                    payment_deadline=ticket.payment_deadline,
                )
                canonical_names = {round_.name for round_ in canonical_member_rounds}
                inserted = 0
                for member_ticket in canonical_member_rounds:
                    member_ticket = normalize_ticket_round(
                        dataclasses.replace(
                            member_ticket,
                            platform=ticket.platform,
                            confidence=ticket.confidence,
                            round_type=ticket.round_type,
                            membership_required=ticket.membership_required,
                        )
                    )
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO ticket_rounds(
                            event_id, round_key, source, url, name, round_number, platform,
                            lottery_start, lottery_end, results_date, general_sale_date, payment_deadline,
                            application_start_at, application_end_at, payment_start_at, payment_end_at,
                            trade_start_at, trade_end_at, confidence, status, round_type, membership_required,
                            evidence, created_at, updated_at
                        )
                        SELECT event_id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, created_at, ?
                        FROM ticket_rounds
                        WHERE id = ?
                        """,
                        (
                            ticket_round_key(member_ticket),
                            member_ticket.source,
                            member_ticket.url,
                            member_ticket.name,
                            member_ticket.round_number,
                            member_ticket.platform,
                            member_ticket.lottery_start,
                            member_ticket.lottery_end,
                            member_ticket.results_date,
                            member_ticket.general_sale_date,
                            member_ticket.payment_deadline,
                            member_ticket.application_start_at,
                            member_ticket.application_end_at,
                            member_ticket.payment_start_at,
                            member_ticket.payment_end_at,
                            member_ticket.trade_start_at,
                            member_ticket.trade_end_at,
                            member_ticket.confidence,
                            member_ticket.status,
                            member_ticket.round_type,
                            member_ticket.membership_required,
                            member_ticket.evidence,
                            utc_now_iso(),
                            int(row[0]),
                        ),
                    )
                    inserted += cursor.rowcount if cursor.rowcount >= 0 else 0
                if canonical_member_rounds and ticket.name not in canonical_names:
                    cursor = connection.execute("DELETE FROM ticket_rounds WHERE id = ?", (int(row[0]),))
                    counts["ticket_round_memberships"] += inserted + (cursor.rowcount if cursor.rowcount >= 0 else 0)
                    continue
                if inserted:
                    counts["ticket_round_memberships"] += inserted
                    continue
            membership_rounds = membership_rounds_from_ticket(ticket)
            if membership_rounds:
                inserted = 0
                for member_ticket in membership_rounds:
                    member_key = ticket_round_key(member_ticket)
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO ticket_rounds(
                            event_id, round_key, source, url, name, round_number, platform,
                            lottery_start, lottery_end, results_date, general_sale_date, payment_deadline,
                            application_start_at, application_end_at, payment_start_at, payment_end_at,
                            trade_start_at, trade_end_at, confidence, status, round_type, membership_required,
                            evidence, created_at, updated_at
                        )
                        SELECT event_id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, created_at, ?
                        FROM ticket_rounds
                        WHERE id = ?
                        """,
                        (
                            member_key,
                            member_ticket.source,
                            member_ticket.url,
                            member_ticket.name,
                            member_ticket.round_number,
                            member_ticket.platform,
                            member_ticket.lottery_start,
                            member_ticket.lottery_end,
                            member_ticket.results_date,
                            member_ticket.general_sale_date,
                            member_ticket.payment_deadline,
                            member_ticket.application_start_at,
                            member_ticket.application_end_at,
                            member_ticket.payment_start_at,
                            member_ticket.payment_end_at,
                            member_ticket.trade_start_at,
                            member_ticket.trade_end_at,
                            member_ticket.confidence,
                            member_ticket.status,
                            member_ticket.round_type,
                            member_ticket.membership_required,
                            member_ticket.evidence,
                            utc_now_iso(),
                            int(row[0]),
                        ),
                    )
                    inserted += cursor.rowcount if cursor.rowcount >= 0 else 0
                if inserted:
                    connection.execute("DELETE FROM ticket_rounds WHERE id = ?", (int(row[0]),))
                    counts["ticket_round_memberships"] += inserted
                    continue
            normalized = normalize_ticket_round(ticket)
            if (
                normalized.application_start_at == row[11]
                and normalized.application_end_at == row[12]
                and normalized.status == row[18]
            ):
                continue
            connection.execute(
                """
                UPDATE ticket_rounds
                SET application_start_at = ?,
                    application_end_at = ?,
                    status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (normalized.application_start_at, normalized.application_end_at, normalized.status, utc_now_iso(), int(row[0])),
            )
            counts["ticket_round_dates"] += 1
        return counts


def source_confidence(link: Link) -> int:
    if is_actionable_ticket_link(link.url, link.label):
        return 90
    if any(hint.lower() in f"{link.label} {link.url}".lower() for hint in TICKET_LINK_HINTS):
        return 60
    return 40


def upsert_sources(connection: sqlite3.Connection, event_id: int, links: Sequence[Link], now: str) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for link in links:
        if not is_actionable_ticket_link(link.url, link.label):
            continue
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


def compute_event_status(info: EventInfo, rounds: Sequence[TicketRound], today: dt.date | None = None) -> str:
    if any(normalize_ticket_round(ticket, today).status in {"open", "closing_soon"} for ticket in rounds):
        return "lottery_open"
    if rounds:
        return "lottery_found"
    if any(is_actionable_ticket_link(link.url, link.label) for link in info.ticket_links):
        return "ticket_links_found"
    if info.official_page:
        return "official_found"
    return "watching"


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
    current_keys: set[str] = set()
    current_platforms: set[str] = set()
    for ticket in dedupe_ticket_rounds(rounds, parse_iso_date(now)):
        ticket = normalize_ticket_round(ticket, parse_iso_date(now))
        round_key = ticket_round_key(ticket)
        current_keys.add(round_key)
        current_platforms.add(ticket.platform or ticket.source or "")
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
    prune_stale_ticket_rounds(connection, event_id, current_keys, current_platforms)
    return alerts


def prune_stale_ticket_rounds(
    connection: sqlite3.Connection,
    event_id: int,
    current_keys: set[str],
    current_platforms: set[str],
) -> int:
    """Drop rounds that vanished from a re-saved event.

    Pruning is scoped to the platforms present in this save so a transient
    fetch failure (which yields no rounds for a platform) cannot wipe a
    platform's previously captured rounds.
    """
    if not current_keys or not current_platforms:
        return 0
    key_slots = ",".join("?" * len(current_keys))
    platform_slots = ",".join("?" * len(current_platforms))
    cursor = connection.execute(
        f"""
        DELETE FROM ticket_rounds
        WHERE event_id = ?
          AND COALESCE(platform, source, '') IN ({platform_slots})
          AND round_key NOT IN ({key_slots})
        """,
        (event_id, *current_platforms, *current_keys),
    )
    return cursor.rowcount


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
            if not is_portal_search_url(info.official_page):
                delete_stale_search_fallback_events(connection, watch_id, event_id)
        event_title = info.title or info.keyword
        alerts: list[dict[str, str]] = []
        if new_event and info.official_page:
            alerts.append({"type": "new_official_page", "event": event_title, "url": info.official_page})
        alerts.extend(upsert_sources(connection, event_id, info.ticket_links, timestamp))
        alerts.extend(upsert_ticket_rounds(connection, event_id, event_title, normalized_rounds, timestamp))
        alerts.extend(record_lifecycle_alerts(connection, event_id, event_title, normalized_rounds, timestamp))
        save_snapshot(connection, event_id, dataclasses.replace(blocks, ticket_info=normalized_rounds), timestamp)
        return alerts


# --- Notification subscriptions, device tokens, and the delivery log ----------


def subscription_from_row(row: sqlite3.Row | tuple[object, ...]) -> NotificationSubscription:
    return NotificationSubscription(
        id=int(row[0]),
        watch_id=int(row[1]),
        scope=str(row[2]),
        location=str(row[3] or ""),
        round_key=str(row[4] or ""),
        channels=str(row[5] or DEFAULT_NOTIFY_CHANNELS),
        lead_days=str(row[6] or "7,1,0"),
        enabled=bool(row[7]),
    )


def add_subscription(
    db_path: str,
    watch_identifier: str,
    scope: str,
    location: str = "",
    round_key: str = "",
    channels: str = DEFAULT_NOTIFY_CHANNELS,
    lead_days: str = "7,1,0",
    now: str | None = None,
) -> NotificationSubscription:
    if scope not in NOTIFY_SCOPES:
        raise ValueError(f"Unknown notification scope: {scope}")
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        watch = resolve_watch(connection, watch_identifier)
        if not watch:
            raise ValueError(f"Watch not found: {watch_identifier}")
        connection.execute(
            """
            INSERT INTO notification_subscriptions(
                watch_id, scope, location, round_key, channels, lead_days, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(watch_id, scope, location, round_key) DO UPDATE SET
                channels = excluded.channels,
                lead_days = excluded.lead_days,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (watch.id, scope, location, round_key, channels, lead_days, timestamp, timestamp),
        )
        row = connection.execute(
            """
            SELECT id, watch_id, scope, location, round_key, channels, lead_days, enabled
            FROM notification_subscriptions
            WHERE watch_id = ? AND scope = ? AND location = ? AND round_key = ?
            """,
            (watch.id, scope, location, round_key),
        ).fetchone()
        return subscription_from_row(row)


def list_subscriptions(db_path: str, watch_id: int | None = None, enabled_only: bool = False) -> list[NotificationSubscription]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        clauses: list[str] = []
        params: list[object] = []
        if watch_id is not None:
            clauses.append("watch_id = ?")
            params.append(watch_id)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = connection.execute(
            f"""
            SELECT id, watch_id, scope, location, round_key, channels, lead_days, enabled
            FROM notification_subscriptions
            {where}
            ORDER BY id
            """,
            params,
        ).fetchall()
        return [subscription_from_row(row) for row in rows]


def remove_subscription(db_path: str, subscription_id: int) -> bool:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        cursor = connection.execute("DELETE FROM notification_subscriptions WHERE id = ?", (subscription_id,))
        return cursor.rowcount > 0


def set_subscription_enabled(db_path: str, subscription_id: int, enabled: bool, now: str | None = None) -> bool:
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        cursor = connection.execute(
            "UPDATE notification_subscriptions SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), timestamp, subscription_id),
        )
        return cursor.rowcount > 0


def device_token_from_row(row: sqlite3.Row | tuple[object, ...]) -> DeviceToken:
    return DeviceToken(id=int(row[0]), token=str(row[1]), platform=str(row[2] or "android"), label=str(row[3] or ""))


def register_device(db_path: str, token: str, platform: str = "android", label: str = "", now: str | None = None) -> DeviceToken:
    token = clean_text(token)
    if not token:
        raise ValueError("Device token is required")
    timestamp = now or utc_now_iso()
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        connection.execute(
            """
            INSERT INTO device_tokens(token, platform, label, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET platform = excluded.platform, label = excluded.label, updated_at = excluded.updated_at
            """,
            (token, platform, label, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT id, token, platform, label FROM device_tokens WHERE token = ?", (token,)
        ).fetchone()
        return device_token_from_row(row)


def list_devices(db_path: str) -> list[DeviceToken]:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        rows = connection.execute("SELECT id, token, platform, label FROM device_tokens ORDER BY id").fetchall()
        return [device_token_from_row(row) for row in rows]


def remove_device(db_path: str, identifier: str) -> bool:
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        cursor = connection.execute(
            "DELETE FROM device_tokens WHERE token = ? OR id = ?",
            (identifier, identifier if str(identifier).isdigit() else -1),
        )
        return cursor.rowcount > 0


def notification_already_sent(connection: sqlite3.Connection, notification_key: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM notification_log WHERE notification_key = ?", (notification_key,)
    ).fetchone() is not None


def record_notification(
    connection: sqlite3.Connection,
    notification_key: str,
    subscription_id: int | None,
    event_id: int | None,
    channel: str,
    payload: dict[str, object],
    now: str,
) -> bool:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO notification_log(notification_key, subscription_id, event_id, channel, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (notification_key, subscription_id, event_id, channel, json.dumps(payload, ensure_ascii=False, sort_keys=True), now),
    )
    return cursor.rowcount > 0
