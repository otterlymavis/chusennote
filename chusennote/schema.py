"""Database connection schema and migrations for chusennote.

The lowest persistence layer: creates the SQLite tables, applies in-place
migrations, and provides the timestamp/hash primitives the CRUD and read-model
layers build on. Depends only on the leaf modules.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import sqlite3

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .storage import *  # noqa: F401,F403


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


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
            ticket_rules_json TEXT NOT NULL DEFAULT '[]',
            ticket_prices_json TEXT NOT NULL DEFAULT '[]',
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
            user_id INTEGER NOT NULL DEFAULT 0,
            watch_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 70,
            private_note INTEGER NOT NULL DEFAULT 0,
            muted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, watch_id, url),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );
        """
    )
    migrate_db(connection)


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if connection_dialect(connection) == "postgres":
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        )
        return {row[0] for row in rows}
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def sqlite_table_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return str(row[0] or "") if row else ""


def postgres_drop_old_notification_subscription_unique(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = ?
          AND c.contype = 'u'
          AND (
            SELECT array_agg(a.attname ORDER BY k.ordinality)
            FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
          ) = ARRAY['watch_id', 'scope', 'location', 'round_key']::name[]
        """,
        ("notification_subscriptions",),
    ).fetchall()
    for row in rows:
        constraint_name = str(row[0])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", constraint_name):
            continue
        connection.execute(f"ALTER TABLE notification_subscriptions DROP CONSTRAINT {constraint_name}")


def migrate_notification_subscriptions_for_user_scope(connection: sqlite3.Connection) -> None:
    add_column_if_missing(connection, "notification_subscriptions", "user_id", "INTEGER NOT NULL DEFAULT 0")
    if connection_dialect(connection) == "postgres":
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS notification_subscriptions_user_scope_idx
            ON notification_subscriptions(user_id, watch_id, scope, location, round_key)
            """
        )
        postgres_drop_old_notification_subscription_unique(connection)
        return
    if connection_dialect(connection) != "sqlite":
        return
    table_sql = sqlite_table_sql(connection, "notification_subscriptions")
    if "UNIQUE(user_id, watch_id, scope, location, round_key)" in table_sql.replace("\n", " "):
        return
    connection.executescript(
        """
        CREATE TABLE notification_subscriptions_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 0,
            watch_id INTEGER NOT NULL,
            scope TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            round_key TEXT NOT NULL DEFAULT '',
            channels TEXT NOT NULL DEFAULT 'feed',
            lead_days TEXT NOT NULL DEFAULT '7,1,0',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, watch_id, scope, location, round_key),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );
        INSERT OR IGNORE INTO notification_subscriptions_new(
            id, user_id, watch_id, scope, location, round_key, channels, lead_days, enabled, created_at, updated_at
        )
        SELECT id, COALESCE(user_id, 0), watch_id, scope, location, round_key, channels, lead_days, enabled, created_at, updated_at
        FROM notification_subscriptions;
        DROP TABLE notification_subscriptions;
        ALTER TABLE notification_subscriptions_new RENAME TO notification_subscriptions;
        """
    )


def migrate_watch_sources_for_user_scope(connection: sqlite3.Connection) -> None:
    add_column_if_missing(connection, "watch_sources", "user_id", "INTEGER NOT NULL DEFAULT 0")
    if connection_dialect(connection) == "postgres":
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS watch_sources_user_scope_idx
            ON watch_sources(user_id, watch_id, url)
            """
        )
        rows = connection.execute(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE t.relname = ?
              AND c.contype = 'u'
              AND (
                SELECT array_agg(a.attname ORDER BY k.ordinality)
                FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality)
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
              ) = ARRAY['watch_id', 'url']::name[]
            """,
            ("watch_sources",),
        ).fetchall()
        for row in rows:
            constraint_name = str(row[0])
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", constraint_name):
                connection.execute(f"ALTER TABLE watch_sources DROP CONSTRAINT {constraint_name}")
        return
    if connection_dialect(connection) != "sqlite":
        return
    table_sql = sqlite_table_sql(connection, "watch_sources")
    if "UNIQUE(user_id, watch_id, url)" in table_sql.replace("\n", " "):
        return
    connection.executescript(
        """
        CREATE TABLE watch_sources_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 0,
            watch_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 70,
            private_note INTEGER NOT NULL DEFAULT 0,
            muted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, watch_id, url),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );
        INSERT OR IGNORE INTO watch_sources_new(
            id, user_id, watch_id, url, label, platform, confidence, private_note, muted, created_at, updated_at
        )
        SELECT id, COALESCE(user_id, 0), watch_id, url, label, platform, confidence, private_note, muted, created_at, updated_at
        FROM watch_sources;
        DROP TABLE watch_sources;
        ALTER TABLE watch_sources_new RENAME TO watch_sources;
        """
    )


def migrate_db(connection: sqlite3.Connection) -> None:
    add_column_if_missing(connection, "watched_keywords", "tags", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "kind", "TEXT NOT NULL DEFAULT 'artist'")
    add_column_if_missing(connection, "watched_keywords", "preferred_regions", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "preferred_venues", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "alert_preferences", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "watched_keywords", "muted", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(connection, "watched_keywords", "local_visible", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(connection, "watched_keywords", "local_muted", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(connection, "watched_keywords", "last_checked_at", "TEXT")
    add_column_if_missing(connection, "events", "status", "TEXT NOT NULL DEFAULT 'watching'")
    add_column_if_missing(connection, "events", "event_key", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(connection, "events", "event_dates_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing(connection, "events", "venues_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing(connection, "events", "ticket_rules_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing(connection, "events", "ticket_prices_json", "TEXT NOT NULL DEFAULT '[]'")
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
            user_id INTEGER NOT NULL DEFAULT 0,
            watch_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            label TEXT NOT NULL,
            platform TEXT NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 70,
            private_note INTEGER NOT NULL DEFAULT 0,
            muted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, watch_id, url),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );

        CREATE TABLE IF NOT EXISTS notification_subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 0,
            watch_id INTEGER NOT NULL,
            scope TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            round_key TEXT NOT NULL DEFAULT '',
            channels TEXT NOT NULL DEFAULT 'feed',
            lead_days TEXT NOT NULL DEFAULT '7,1,0',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, watch_id, scope, location, round_key),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );

        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY,
            notification_key TEXT NOT NULL UNIQUE,
            subscription_id INTEGER,
            event_id INTEGER,
            channel TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS device_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            token TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL DEFAULT 'android',
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        -- A separate, low-privilege token scoped to GET /calendar.ics only: it
        -- lives in its own table (never checked by user_for_token) so it can be
        -- put in a shareable subscription URL without carrying full API access
        -- the way an api_tokens bearer token would. One active token per user;
        -- issuing a new one replaces the old.
        CREATE TABLE IF NOT EXISTS calendar_tokens (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        -- Per-user subscriptions to the shared canonical watched_keywords rows:
        -- a popular keyword is one row (scraped once); each user subscribes to it.
        CREATE TABLE IF NOT EXISTS user_watches (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            watch_id INTEGER NOT NULL,
            muted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(user_id, watch_id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(watch_id) REFERENCES watched_keywords(id)
        );
        """
    )
    add_column_if_missing(connection, "user_watches", "muted", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(connection, "user_watches", "updated_at", "TEXT")
    connection.execute(
        """
        UPDATE watched_keywords
        SET local_visible = 1,
            local_muted = muted
        WHERE local_visible = 0
          AND NOT EXISTS (
              SELECT 1 FROM user_watches owner WHERE owner.watch_id = watched_keywords.id
          )
        """
    )
    migrate_notification_subscriptions_for_user_scope(connection)
    migrate_watch_sources_for_user_scope(connection)
    add_column_if_missing(connection, "device_tokens", "user_id", "INTEGER")
    # PRAGMA user_version is SQLite-only; Postgres reports the constant directly.
    if connection_dialect(connection) == "sqlite":
        connection.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
