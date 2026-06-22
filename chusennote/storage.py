"""Database connection seam for chusennote.

A single place that opens database connections, so the persistence layer is
decoupled from a specific driver. Today it returns SQLite connections exactly as
before; a Postgres backend will plug in here behind the same ``connect`` call
without touching the CRUD/read-model code. Depends only on the leaf models
module to stay at the bottom of the import graph.
"""

from __future__ import annotations

import os
import sqlite3

from .models import DEFAULT_DB_PATH

# A full database URL (e.g. postgres://user:pass@host/db) overrides the default
# SQLite path. Unset keeps the zero-config local SQLite behaviour.
DATABASE_URL_ENV = "CHUSENNOTE_DATABASE_URL"


def resolve_target(target: str | None = None) -> str:
    """Resolve the database target: explicit arg, else env URL, else the default
    SQLite path. Callers pass a concrete path today, so the env is only consulted
    when no target is given."""
    return target or os.environ.get(DATABASE_URL_ENV) or DEFAULT_DB_PATH


def is_postgres_url(target: str) -> bool:
    return target.startswith(("postgres://", "postgresql://"))


def connect(target: str | None = None) -> sqlite3.Connection:
    """Open a database connection for ``target``.

    ``target`` is a SQLite file path today. A Postgres URL is recognised here so
    the dispatch point exists, but the Postgres backend is wired in a later step.
    """
    resolved = resolve_target(target)
    if is_postgres_url(resolved):
        raise NotImplementedError("Postgres backend is not wired up yet")
    return sqlite3.connect(resolved)
