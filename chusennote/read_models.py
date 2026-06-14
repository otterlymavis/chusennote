"""Read models: aggregate query views and exports for chusennote.

Builds the dashboard event feed, the upcoming-ticket priority list, the alert
feed, the API health summary, event detail, and the iCalendar export. Reads
through :mod:`chusennote.crud` and :mod:`chusennote.schema`; nothing in the
write path depends on this module.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .netio import *  # noqa: F401,F403
from .search import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403
from .crud import *  # noqa: F401,F403


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
