"""SQLite persistence and read models for chusennote.

Owns the schema/migrations, all watch/event/source/ticket-round CRUD, the alert
lifecycle, snapshot storage, and the calendar/ICS + API read models. Pure
persistence: it never drives web discovery, so the discovery pipeline can build
on top of it without an import cycle.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import sqlite3
import urllib.parse
from collections.abc import Iterable, Sequence

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403


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
                        if is_actionable_ticket_link(str(link[1]), str(link[0] or ""))
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
            if status not in UPCOMING_STATUS_ORDER or status in {"closed", "unknown"}:
                continue
            relevant_date = upcoming_relevant_date(round_info)
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
