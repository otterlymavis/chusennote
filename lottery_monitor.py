#!/usr/bin/env python3
"""Keyword-first Japanese ticket lottery monitor.

The intended flow is:
1. User enters a keyword for an artist/event/musical.
2. The app searches for likely official pages.
3. It extracts general event information and ticket links from official pages.
4. It builds two user-facing blocks: event info and ticket/lottery info.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import base64
import hashlib
import html
import http.server
import json
import os
import pathlib
import re
import shlex
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from html.parser import HTMLParser

from chusennote import extract, models, netio, search, storage, util, web  # noqa: F401  (re-exported for monkeypatch targets)
from chusennote.models import *  # noqa: F401,F403
from chusennote.util import *  # noqa: F401,F403
from chusennote.netio import *  # noqa: F401,F403
from chusennote.search import *  # noqa: F401,F403
from chusennote.extract import *  # noqa: F401,F403
from chusennote.storage import *  # noqa: F401,F403
from chusennote.web import *  # noqa: F401,F403


def render_blocks(blocks: AppBlocks) -> str:
    info = blocks.general_info
    lines = ["# General event info", ""]
    lines.append(f"- Keyword: {info.keyword}")
    lines.append(f"- Title: {info.title or 'Unknown'}")
    lines.append(f"- Official page: {info.official_page or 'Not found'}")
    if info.summary:
        lines.append(f"- Summary: {info.summary}")
    if info.event_dates:
        lines.append("- Event date clues:")
        lines.extend(f"  - {date}" for date in info.event_dates)
    if info.venues:
        lines.append("- Venue clues:")
        lines.extend(f"  - {venue}" for venue in info.venues)

    lines.extend(["", "# Ticket / lottery info", ""])
    if info.ticket_links:
        lines.append("- Ticket links found:")
        lines.extend(f"  - {link.label}: {link.url}" for link in info.ticket_links)
    else:
        lines.append("- Ticket links found: none")

    if blocks.ticket_info:
        lines.append("- Lottery / sales rounds:")
        for ticket in blocks.ticket_info:
            lines.append(f"  - {ticket.name} ({ticket.source})")
            lines.append(f"    - Link: {ticket.url}")
            lines.append(f"    - Lottery start: {ticket.lottery_start or 'Unknown'}")
            lines.append(f"    - Lottery end: {ticket.lottery_end or 'Unknown'}")
            lines.append(f"    - Results date: {ticket.results_date or 'Unknown'}")
            lines.append(f"    - General sale: {ticket.general_sale_date or 'Unknown'}")
            lines.append(f"    - Payment deadline: {ticket.payment_deadline or 'Unknown'}")
            if ticket.evidence:
                lines.append(f"    - Evidence: {ticket.evidence}")
    else:
        lines.append("- Lottery / sales rounds: none detected yet")
    return "\n".join(lines)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    cleaned_argv, session_log, session_log_dir = split_session_log_args(argv)
    if cleaned_argv and cleaned_argv[0] not in {"search", "watch", "artist", "event", "export", "web", "db", "-h", "--help"}:
        parser = argparse.ArgumentParser(description="Search Japanese event ticket lotteries by keyword.")
        parser.add_argument("keyword", help="Artist, event, or musical keyword to search for")
        parser.add_argument("--json", action="store_true", help="Output the two app blocks as JSON")
        parser.add_argument("--db", default=None, help="SQLite database path for saving watch/event/ticket history")
        parser.add_argument("--alerts-json", action="store_true", help="With --db, output only detected alert changes as JSON")
        parser.set_defaults(command="legacy")
        args = parser.parse_args(cleaned_argv)
        args.session_log = session_log
        args.session_log_dir = session_log_dir
        return args

    parser = argparse.ArgumentParser(description="Monitor Japanese event ticket lotteries.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search a single keyword")
    search_parser.add_argument("keyword", help="Artist, event, or musical keyword to search for")
    search_parser.add_argument("--db", default=None, help="SQLite database path for saving search results")
    search_parser.add_argument("--json", action="store_true", help="Output the two app blocks as JSON")
    search_parser.add_argument("--alerts-json", action="store_true", help="With --db, output only detected alert changes as JSON")

    watch_parser = subparsers.add_parser("watch", help="Manage and run watched keywords")
    watch_subparsers = watch_parser.add_subparsers(dest="watch_command", required=True)

    add_parser = watch_subparsers.add_parser("add", help="Add a keyword to the watchlist")
    add_parser.add_argument("keyword")
    add_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--regions", default="")
    add_parser.add_argument("--venues", default="")

    list_parser = watch_subparsers.add_parser("list", help="List watched keywords")
    list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    list_parser.add_argument("--json", action="store_true")
    list_parser.add_argument("--include-muted", action="store_true")

    remove_parser = watch_subparsers.add_parser("remove", help="Remove a watch by id or keyword")
    remove_parser.add_argument("identifier")
    remove_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    for watch_toggle in ("mute", "unmute"):
        watch_toggle_parser = watch_subparsers.add_parser(watch_toggle, help=f"{watch_toggle.title()} a watch by id or keyword")
        watch_toggle_parser.add_argument("identifier")
        watch_toggle_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    run_parser = watch_subparsers.add_parser("run", help="Run all active watched keywords")
    run_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    run_parser.add_argument("--alerts-json", action="store_true")

    loop_parser = watch_subparsers.add_parser("loop", help="Run active watches repeatedly in the foreground")
    loop_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    loop_parser.add_argument("--interval-minutes", type=non_negative_int, default=60)
    loop_parser.add_argument("--kind", choices=WATCH_KINDS, default=WATCH_KIND_EVENT)
    loop_parser.add_argument("--alerts-json", action="store_true")
    loop_parser.add_argument("--max-runs", type=positive_int)
    loop_parser.add_argument("--run-immediately", action=argparse.BooleanOptionalAction, default=True)
    loop_parser.add_argument("--stop-after-errors", type=positive_int)

    for command_name, kind, help_text in (
        ("artist", WATCH_KIND_ARTIST, "Manage tracked artists with basic event info"),
        ("event", WATCH_KIND_EVENT, "Manage tracked events with ticket and lottery info"),
    ):
        kind_parser = subparsers.add_parser(command_name, help=help_text)
        kind_parser.set_defaults(kind=kind)
        kind_subparsers = kind_parser.add_subparsers(dest="kind_command", required=True)

        kind_add_parser = kind_subparsers.add_parser("add", help=f"Add a tracked {kind}")
        kind_add_parser.add_argument("keyword")
        kind_add_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
        kind_add_parser.add_argument("--tags", default="")
        kind_add_parser.add_argument("--regions", default="")
        kind_add_parser.add_argument("--venues", default="")
        kind_add_parser.add_argument("--alerts", default=DEFAULT_ALERT_PREFERENCES)

        kind_list_parser = kind_subparsers.add_parser("list", help=f"List tracked {kind}s")
        kind_list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
        kind_list_parser.add_argument("--json", action="store_true")
        kind_list_parser.add_argument("--include-muted", action="store_true")

        kind_remove_parser = kind_subparsers.add_parser("remove", help=f"Remove a tracked {kind}")
        kind_remove_parser.add_argument("identifier")
        kind_remove_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

        for kind_toggle in ("mute", "unmute"):
            kind_toggle_parser = kind_subparsers.add_parser(kind_toggle, help=f"{kind_toggle.title()} a tracked {kind}")
            kind_toggle_parser.add_argument("identifier")
            kind_toggle_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

        kind_run_parser = kind_subparsers.add_parser("run", help=f"Run all active tracked {kind}s")
        kind_run_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
        kind_run_parser.add_argument("--alerts-json", action="store_true")

    source_parser = watch_subparsers.add_parser("source", help="Manage manual source URLs for a watch")
    source_subparsers = source_parser.add_subparsers(dest="source_command", required=True)

    source_add_parser = source_subparsers.add_parser("add", help="Add a manual source URL")
    source_add_parser.add_argument("watch")
    source_add_parser.add_argument("url")
    source_add_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    source_add_parser.add_argument("--label", default="")
    source_add_parser.add_argument("--private-note", action="store_true", help="Store the URL/note without scraping it")

    source_list_parser = source_subparsers.add_parser("list", help="List manual source URLs")
    source_list_parser.add_argument("watch", nargs="?")
    source_list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    source_list_parser.add_argument("--json", action="store_true")
    source_list_parser.add_argument("--include-muted", action="store_true")

    source_remove_parser = source_subparsers.add_parser("remove", help="Remove a manual source by id or URL")
    source_remove_parser.add_argument("identifier")
    source_remove_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    for source_toggle in ("mute", "unmute"):
        source_toggle_parser = source_subparsers.add_parser(source_toggle, help=f"{source_toggle.title()} a manual source by id or URL")
        source_toggle_parser.add_argument("identifier")
        source_toggle_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")

    web_parser = subparsers.add_parser("web", help="Run the local web UI")
    web_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    web_parser.add_argument("--port", type=int, default=8765, help="Local port to serve on")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")

    db_parser = subparsers.add_parser("db", help="Maintain the local SQLite database")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    cleanup_parser = db_subparsers.add_parser("cleanup", help="Remove stale fallback and orphaned rows")
    cleanup_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    cleanup_parser.add_argument("--json", action="store_true")

    export_parser = subparsers.add_parser("export", help="Export saved data")
    export_parser.add_argument("target", choices=("events", "alerts", "artists", "tracked-events", "sources", "calendar", "upcoming"))
    export_parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    export_parser.add_argument("--json", action="store_true", default=True)
    export_parser.add_argument("--include-muted", action="store_true", help="Include muted watches or embedded manual sources where supported")

    args = parser.parse_args(cleaned_argv)
    args.session_log = session_log
    args.session_log_dir = session_log_dir
    return args


def watches_to_json(watches: Sequence[Watch]) -> str:
    return json.dumps([dataclasses.asdict(watch) for watch in watches], ensure_ascii=False, indent=2)


def watch_sources_to_json(sources: Sequence[WatchSource]) -> str:
    return json.dumps([dataclasses.asdict(source) for source in sources], ensure_ascii=False, indent=2)


def render_watches(watches: Sequence[Watch]) -> str:
    if not watches:
        return "No active watches."
    lines = ["# Watchlist", ""]
    for watch in watches:
        checked = watch.last_checked_at or "never"
        muted_label = " [muted]" if watch.muted else ""
        lines.append(f"- {watch.id}: {watch.keyword}{muted_label} (last checked: {checked})")
    return "\n".join(lines)


def render_watch_sources(sources: Sequence[WatchSource]) -> str:
    if not sources:
        return "No manual sources."
    lines = ["# Manual sources", ""]
    for source in sources:
        mode = "private note" if source.private_note else source.platform
        muted_label = " [muted]" if source.muted else ""
        lines.append(f"- {source.id}: watch {source.watch_id} · {source.label}{muted_label} ({mode}) {source.url}")
    return "\n".join(lines)


def run_command(args: argparse.Namespace) -> int:
    if args.command == "web":
        run_web(args.db, args.port, args.host)
        return 0
    if args.command == "db":
        if args.db_command == "cleanup":
            counts = cleanup_database(args.db)
            if args.json:
                print(json.dumps(counts, ensure_ascii=False, indent=2))
            else:
                total = sum(counts.values())
                print(f"Database cleanup removed {total} rows.")
                for key, count in counts.items():
                    if count:
                        print(f"- {key}: {count}")
            return 0
    if args.command == "export":
        if args.target == "events":
            print(
                json.dumps(
                    recent_events(
                        args.db,
                        include_muted_sources=args.include_muted,
                        include_muted_watches=args.include_muted,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.target == "alerts":
            print(json.dumps(recent_alerts(args.db), ensure_ascii=False, indent=2))
        elif args.target == "artists":
            print(watches_to_json(list_watches(args.db, include_muted=args.include_muted, kind=WATCH_KIND_ARTIST)))
        elif args.target == "tracked-events":
            print(watches_to_json(list_watches(args.db, include_muted=args.include_muted, kind=WATCH_KIND_EVENT)))
        elif args.target == "sources":
            print(watch_sources_to_json(list_watch_sources(args.db, include_muted=args.include_muted)))
        elif args.target == "calendar":
            print(render_calendar_ics(args.db, include_muted_watches=args.include_muted), end="")
        elif args.target == "upcoming":
            print(json.dumps(upcoming_priority_rows(args.db, include_muted_watches=args.include_muted), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"artist", "event"}:
        if args.kind_command == "add":
            watch = add_watch(
                args.db,
                args.keyword,
                kind=args.kind,
                tags=args.tags,
                preferred_regions=args.regions,
                preferred_venues=args.venues,
                alert_preferences=args.alerts,
            )
            print(f"Added tracked {args.kind} {watch.id}: {watch.keyword}")
            return 0
        if args.kind_command == "list":
            watches = list_watches(args.db, include_muted=args.include_muted, kind=args.kind)
            print(watches_to_json(watches) if args.json else render_watches(watches))
            return 0
        if args.kind_command == "remove":
            removed = remove_watch(args.db, args.identifier)
            print(f"Removed tracked {args.kind}." if removed else f"Tracked {args.kind} not found.")
            return 0 if removed else 1
        if args.kind_command == "mute":
            muted = set_watch_muted(args.db, args.identifier, True)
            print(f"Muted tracked {args.kind}." if muted else f"Tracked {args.kind} not found.")
            return 0 if muted else 1
        if args.kind_command == "unmute":
            unmuted = set_watch_muted(args.db, args.identifier, False)
            print(f"Unmuted tracked {args.kind}." if unmuted else f"Tracked {args.kind} not found.")
            return 0 if unmuted else 1
        if args.kind_command == "run":
            alerts = run_watches(args.db, kind=args.kind)
            if args.alerts_json:
                print(json.dumps(alerts, ensure_ascii=False, indent=2))
            else:
                print(f"Ran {len(list_watches(args.db, kind=args.kind))} active tracked {args.kind}s; {len(alerts)} alerts.")
            return 0
    if args.command == "watch":
        if args.watch_command == "source":
            if args.source_command == "add":
                try:
                    source = add_watch_source(args.db, args.watch, args.url, args.label, args.private_note)
                except ValueError as error:
                    print(str(error))
                    return 1
                print(f"Added source {source.id}: {source.label}")
                return 0
            if args.source_command == "list":
                sources = list_watch_sources(args.db, args.watch, include_muted=args.include_muted)
                print(watch_sources_to_json(sources) if args.json else render_watch_sources(sources))
                return 0
            if args.source_command == "remove":
                removed = remove_watch_source(args.db, args.identifier)
                print("Removed source." if removed else "Source not found.")
                return 0 if removed else 1
            if args.source_command == "mute":
                muted = set_watch_source_muted(args.db, args.identifier, True)
                print("Muted source." if muted else "Source not found.")
                return 0 if muted else 1
            if args.source_command == "unmute":
                unmuted = set_watch_source_muted(args.db, args.identifier, False)
                print("Unmuted source." if unmuted else "Source not found.")
                return 0 if unmuted else 1
        if args.watch_command == "add":
            watch = add_watch(
                args.db,
                args.keyword,
                kind=WATCH_KIND_EVENT,
                tags=args.tags,
                preferred_regions=args.regions,
                preferred_venues=args.venues,
            )
            print(f"Added watch {watch.id}: {watch.keyword}")
            return 0
        if args.watch_command == "list":
            watches = list_watches(args.db, include_muted=args.include_muted)
            print(watches_to_json(watches) if args.json else render_watches(watches))
            return 0
        if args.watch_command == "remove":
            removed = remove_watch(args.db, args.identifier)
            print("Removed watch." if removed else "Watch not found.")
            return 0 if removed else 1
        if args.watch_command == "mute":
            muted = set_watch_muted(args.db, args.identifier, True)
            print("Muted watch." if muted else "Watch not found.")
            return 0 if muted else 1
        if args.watch_command == "unmute":
            unmuted = set_watch_muted(args.db, args.identifier, False)
            print("Unmuted watch." if unmuted else "Watch not found.")
            return 0 if unmuted else 1
        if args.watch_command == "run":
            alerts = run_watches(args.db)
            if args.alerts_json:
                print(json.dumps(alerts, ensure_ascii=False, indent=2))
            else:
                print(f"Ran {len(list_watches(args.db))} active watches; {len(alerts)} alerts.")
            return 0
        if args.watch_command == "loop":
            return run_watch_loop(
                args.db,
                interval_minutes=args.interval_minutes,
                kind=args.kind,
                alerts_json=args.alerts_json,
                max_runs=args.max_runs,
                run_immediately=args.run_immediately,
                stop_after_errors=args.stop_after_errors,
            )

    blocks = build_blocks(args.keyword)
    alerts: list[dict[str, str]] = []
    if args.db:
        alerts = save_blocks(args.db, blocks)
    if args.alerts_json:
        print(json.dumps(alerts, ensure_ascii=False, indent=2))
    else:
        print(blocks_to_json(blocks) if args.json else render_blocks(blocks))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv or sys.argv[1:])
    started_at = dt.datetime.now().astimezone()
    args = parse_args(raw_argv)
    exit_code = run_command(args)
    if args.session_log:
        append_session_log(raw_argv, args, exit_code, started_at)
    return exit_code


if __name__ == "__main__":
    configure_cli_stdio()
    raise SystemExit(main())
