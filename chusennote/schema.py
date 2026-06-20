"""Database connection schema and migrations for chusennote.

The lowest persistence layer: creates the SQLite tables, applies in-place
migrations, and provides the timestamp/hash primitives the CRUD and read-model
layers build on. Depends only on the leaf modules.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403


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

        CREATE TABLE IF NOT EXISTS notification_subscriptions (
            id INTEGER PRIMARY KEY,
            watch_id INTEGER NOT NULL,
            scope TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            round_key TEXT NOT NULL DEFAULT '',
            channels TEXT NOT NULL DEFAULT 'feed',
            lead_days TEXT NOT NULL DEFAULT '7,1,0',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(watch_id, scope, location, round_key),
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
            token TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL DEFAULT 'android',
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
