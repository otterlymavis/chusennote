"""Notification subscriptions, scheduling, and delivery for chusennote.

Turns notification subscriptions (per artist, event, location, or single round)
into concrete reminders for every upcoming ticket date — lottery application
open/close, results, payment deadline, general sale, and performance/show
dates — at configurable lead times (default 7 days before, 1 day before, and
the day itself). Reminders are deduplicated through ``notification_log`` and
dispatched to the feed (always), email (SMTP), and mobile push (FCM) channels.

Builds on the read-model and CRUD layers; nothing lower depends on this module.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import sqlite3
import urllib.request
from collections.abc import Sequence
from email.message import EmailMessage

from .models import *  # noqa: F401,F403
from .util import *  # noqa: F401,F403
from .extract import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403
from .crud import *  # noqa: F401,F403
from .read_models import *  # noqa: F401,F403


# Date fields on a round that deserve a reminder, with a human label.
NOTIFY_DATE_FIELDS = (
    ("application_start_at", "Lottery application opens"),
    ("application_end_at", "Lottery application closes"),
    ("results_date", "Lottery results announced"),
    ("payment_end_at", "Payment deadline"),
    ("general_sale_date", "General sale"),
)


def parse_lead_days(text: str) -> list[int]:
    days: list[int] = []
    for part in str(text or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in days:
            days.append(int(part))
    return days or [0]


def first_iso_date(text: str) -> str | None:
    for match in DATE_RE.finditer(str(text or "")):
        iso = normalized_iso_date(match.group(0))
        if iso:
            return iso
    return None


def subscription_occasions(subscription: NotificationSubscription, event: dict[str, object]) -> list[dict[str, object]]:
    """Every dated occasion a subscription covers within one event."""
    venues = [str(value) for value in event.get("venues", []) if value]
    occasions: list[dict[str, object]] = []

    def base(location: str, label: str, field: str, date: str, title: str, url: str, kind: str) -> dict[str, object]:
        return {
            "event_id": event.get("id"),
            "event_title": event.get("title"),
            "location": location,
            "label": label,
            "field": field,
            "date": date,
            "subject": title,
            "url": url or str(event.get("official_url") or ""),
            "kind": kind,
        }

    for round_info in event.get("rounds", []):
        if not isinstance(round_info, dict):
            continue
        if subscription.scope == NOTIFY_SCOPE_ROUND and round_info.get("round_key") != subscription.round_key:
            continue
        location = detect_round_location(round_info, venues) or UNSPECIFIED_LOCATION
        if (
            subscription.scope == NOTIFY_SCOPE_EVENT_LOCATION
            and subscription.location
            and location != subscription.location
        ):
            continue
        for field, label in NOTIFY_DATE_FIELDS:
            date_value = round_info.get(field)
            if date_value:
                occasions.append(
                    base(location, label, field, str(date_value), str(round_info.get("name") or "Ticket round"),
                         str(round_info.get("url") or ""), "lottery")
                )

    # Performance / show dates apply to artist and event subscriptions, never to
    # a single-round subscription.
    if subscription.scope in (NOTIFY_SCOPE_ARTIST_ALL, NOTIFY_SCOPE_EVENT_ALL, NOTIFY_SCOPE_EVENT_LOCATION):
        stops = tour_stops(venues, [str(value) for value in event.get("event_dates", [])])
        if stops:
            for city, venue, date_text in stops:
                location = city or venue
                if (
                    subscription.scope == NOTIFY_SCOPE_EVENT_LOCATION
                    and subscription.location
                    and location != subscription.location
                ):
                    continue
                iso = first_iso_date(date_text)
                if iso:
                    occasions.append(base(location, "Performance date", "show", iso, venue, "", "show"))
        else:  # artist events store the show date with no venue
            for date_text in event.get("event_dates", []):
                iso = first_iso_date(str(date_text))
                if iso:
                    occasions.append(base("", "Performance date", "show", iso, str(event.get("title") or ""), "", "show"))
    return occasions


def pending_notifications(db_path: str, now: str | None = None, lead_days: tuple[int, ...] = DEFAULT_LEAD_DAYS) -> list[dict[str, object]]:
    """Reminders due today that have not yet been recorded."""
    timestamp = now or utc_now_iso()
    today = parse_iso_date(timestamp) or dt.date.today()
    subscriptions = list_subscriptions(db_path, enabled_only=True)
    if not subscriptions:
        return []
    events_by_watch: dict[int, list[dict[str, object]]] = {}
    for event in recent_events(db_path, limit=500):
        events_by_watch.setdefault(int(event.get("watch_id") or 0), []).append(event)

    pending: list[dict[str, object]] = []
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        for subscription in subscriptions:
            leads = parse_lead_days(subscription.lead_days) or list(lead_days)
            for event in events_by_watch.get(subscription.watch_id, []):
                for occasion in subscription_occasions(subscription, event):
                    date = parse_iso_date(str(occasion["date"]))
                    if not date or date < today:
                        continue
                    days_until = (date - today).days
                    if days_until not in leads:
                        continue
                    key = stable_hash(
                        "|".join(
                            (
                                str(subscription.id),
                                str(occasion["field"]),
                                str(occasion["subject"]),
                                str(occasion["location"]),
                                str(occasion["date"]),
                                str(days_until),
                            )
                        )
                    )
                    if notification_already_sent(connection, key):
                        continue
                    pending.append(
                        {
                            **occasion,
                            "subscription_id": subscription.id,
                            "watch_id": subscription.watch_id,
                            "channels": subscription.channels,
                            "lead_days": days_until,
                            "notification_key": key,
                            "generated_at": timestamp,
                        }
                    )
    return pending


def notification_headline(notification: dict[str, object]) -> tuple[str, str]:
    lead = int(notification.get("lead_days") or 0)
    when = "today" if lead == 0 else f"in {lead} day{'s' if lead != 1 else ''}"
    location = str(notification.get("location") or "")
    suffix = f" ({location})" if location and location != UNSPECIFIED_LOCATION else ""
    title = f"{notification.get('label')} {when}"
    body = f"{notification.get('subject')}{suffix} — {notification.get('date')} · {notification.get('event_title')}"
    return title, body


def send_email_notification(notification: dict[str, object]) -> bool:
    host = os.environ.get(SMTP_HOST_ENV, "").strip()
    recipient = os.environ.get(NOTIFY_EMAIL_ENV, "").strip()
    if not host or not recipient:
        return False
    title, body = notification_headline(notification)
    message = EmailMessage()
    message["Subject"] = f"[chusennote] {title}"
    message["From"] = os.environ.get(SMTP_FROM_ENV, "").strip() or os.environ.get(SMTP_USER_ENV, "").strip() or recipient
    message["To"] = recipient
    url = str(notification.get("url") or "")
    message.set_content(f"{body}\n\n{url}".strip())
    port = int(os.environ.get(SMTP_PORT_ENV, "587") or "587")
    try:
        with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as server:
            server.starttls()
            user = os.environ.get(SMTP_USER_ENV, "").strip()
            password = os.environ.get(SMTP_PASSWORD_ENV, "").strip()
            if user and password:
                server.login(user, password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        return False
    return True


def send_push_notification(notification: dict[str, object], devices: Sequence[DeviceToken]) -> bool:
    server_key = os.environ.get(FCM_SERVER_KEY_ENV, "").strip()
    tokens = [device.token for device in devices if device.token]
    if not server_key or not tokens:
        return False
    title, body = notification_headline(notification)
    payload = {
        "registration_ids": tokens,
        "notification": {"title": title, "body": body},
        "data": {
            "event_id": notification.get("event_id"),
            "location": notification.get("location"),
            "field": notification.get("field"),
            "date": notification.get("date"),
            "url": notification.get("url"),
        },
    }
    request = urllib.request.Request(
        "https://fcm.googleapis.com/fcm/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"key={server_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except (OSError, ValueError):
        return False
    return True


def run_notifications(
    db_path: str,
    now: str | None = None,
    lead_days: tuple[int, ...] = DEFAULT_LEAD_DAYS,
    deliver: bool = True,
) -> list[dict[str, object]]:
    """Generate due reminders, dispatch them to each channel, and record them."""
    timestamp = now or utc_now_iso()
    pending = pending_notifications(db_path, timestamp, lead_days)
    if not pending:
        return []
    devices = list_devices(db_path)
    delivered: list[dict[str, object]] = []
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        for notification in pending:
            channels = {channel.strip() for channel in str(notification["channels"]).split(",") if channel.strip()}
            results = {"feed": True}
            if deliver and "email" in channels:
                results["email"] = send_email_notification(notification)
            if deliver and "push" in channels:
                results["push"] = send_push_notification(notification, devices)
            title, body = notification_headline(notification)
            payload = {
                "title": title,
                "body": body,
                "event_id": notification.get("event_id"),
                "event_title": notification.get("event_title"),
                "subject": notification.get("subject"),
                "location": notification.get("location"),
                "label": notification.get("label"),
                "field": notification.get("field"),
                "date": notification.get("date"),
                "lead_days": notification.get("lead_days"),
                "url": notification.get("url"),
                "delivered": results,
            }
            record_notification(
                connection,
                str(notification["notification_key"]),
                int(notification["subscription_id"]),
                notification.get("event_id"),
                str(notification["channels"]),
                payload,
                timestamp,
            )
            delivered.append(payload)
    return delivered


def notification_feed(db_path: str, limit: int = 100) -> list[dict[str, object]]:
    """Recent reminders for the in-app/mobile notifications feed."""
    with sqlite3.connect(db_path) as connection:
        init_db(connection)
        rows = connection.execute(
            """
            SELECT payload_json, channel, created_at
            FROM notification_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    feed: list[dict[str, object]] = []
    for payload_json, channel, created_at in rows:
        payload = json.loads(payload_json)
        payload["channel"] = channel
        payload["created_at"] = created_at
        feed.append(payload)
    return feed
