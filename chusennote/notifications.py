"""Notification subscriptions, scheduling, and delivery for chusennote.

Turns notification subscriptions (per artist, event, location, or single round)
into concrete reminders for every upcoming ticket date — lottery application
open/close, results, payment deadline, general sale, official resale, and performance/show
dates — at configurable lead times (default 7 days before, 1 day before, and
the day itself). Reminders are deduplicated through ``notification_log`` and
dispatched to the feed (always), email (SMTP), mobile push (FCM), and optional
local-workspace Slack, Discord, or LINE channels.

Builds on the read-model and CRUD layers; nothing lower depends on this module.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
    ("trade_start_at", "Official resale opens"),
    ("trade_end_at", "Official resale closes"),
)
NOTIFICATION_CLAIM_LEASE_SECONDS = 15 * 60


def notification_timestamp(value: str | None = None) -> str:
    raw = value or utc_now_iso()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return utc_now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_lead_days(text: str) -> list[int]:
    days: list[int] = []
    for part in str(text or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in days:
            days.append(int(part))
    return days or [0]


def parsed_notification_channels(text: str) -> set[str]:
    return {
        channel
        for raw_channel in str(text or "").split(",")
        if (channel := raw_channel.strip().lower()) in NOTIFY_CHANNELS
    } or {DEFAULT_NOTIFY_CHANNELS}


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


def pending_notifications(
    db_path: str,
    now: str | None = None,
    lead_days: tuple[int, ...] = DEFAULT_LEAD_DAYS,
    user_id: int | None = None,
) -> list[dict[str, object]]:
    """Reminders due today that have not yet been recorded."""
    timestamp = notification_timestamp(now)
    today = parse_iso_date(timestamp) or dt.date.today()
    current_time = dt.datetime.fromisoformat(timestamp)
    stale_before = (current_time - dt.timedelta(seconds=NOTIFICATION_CLAIM_LEASE_SECONDS)).isoformat()
    subscriptions = list_subscriptions(db_path, enabled_only=True, user_id=user_id)
    if not subscriptions:
        return []
    events_by_watch: dict[int, list[dict[str, object]]] = {}
    event_user_id = user_id if user_id is not None else None
    for event in recent_events(db_path, limit=500, user_id=event_user_id):
        events_by_watch.setdefault(int(event.get("watch_id") or 0), []).append(event)

    pending: list[dict[str, object]] = []
    with connect(db_path) as connection:
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
                    exists, previous_delivery, processing_at, attempt_count = notification_delivery_claim_state(
                        connection, key
                    )
                    channels = parsed_notification_channels(subscription.channels)
                    delivery_complete = exists and (
                        previous_delivery.get("_legacy_complete", False)
                        or all(
                            channel == "feed" or previous_delivery.get(channel, False)
                            for channel in channels
                        )
                    )
                    if delivery_complete:
                        continue
                    if processing_at and processing_at > stale_before:
                        continue
                    pending_notification = {
                        **occasion,
                        "subscription_id": subscription.id,
                        "user_id": subscription.user_id,
                        "watch_id": subscription.watch_id,
                        "channels": subscription.channels,
                        "lead_days": days_until,
                        "notification_key": key,
                        "generated_at": timestamp,
                    }
                    if exists:
                        pending_notification["_previous_delivered"] = previous_delivery
                    pending_notification["_claim_existed"] = exists
                    pending_notification["_claim_attempt_count"] = attempt_count
                    pending_notification["_claim_stale_before"] = stale_before
                    pending.append(pending_notification)
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


class _NoExternalNotificationRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _open_external_notification_request(request: urllib.request.Request):
    opener = urllib.request.build_opener(_NoExternalNotificationRedirects())
    return opener.open(request, timeout=TIMEOUT_SECONDS)


def _validated_webhook_url(value: str, hosts: set[str], path_prefix: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in hosts
        or not parsed.path.startswith(path_prefix)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return ""
    return urllib.parse.urlunsplit(parsed)


def notification_message(notification: dict[str, object], limit: int) -> str:
    title, body = notification_headline(notification)
    url = str(notification.get("url") or "").strip()
    message = "\n".join(value for value in (title, body, url) if value)
    if len(message) <= limit:
        return message
    return message[: max(0, limit - 1)].rstrip() + "…"


def _post_external_json(url: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> bool:
    request_headers = {"Content-Type": "application/json; charset=UTF-8", **(headers or {})}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with _open_external_notification_request(request) as response:
            response.read()
    except (urllib.error.HTTPError, OSError, ValueError):
        return False
    return True


def send_slack_notification(notification: dict[str, object]) -> bool:
    url = _validated_webhook_url(
        os.environ.get(SLACK_WEBHOOK_URL_ENV, ""),
        {"hooks.slack.com", "hooks.slack-gov.com"},
        "/services/",
    )
    return bool(url) and _post_external_json(url, {"text": notification_message(notification, 3000)})


def send_discord_notification(notification: dict[str, object]) -> bool:
    url = _validated_webhook_url(
        os.environ.get(DISCORD_WEBHOOK_URL_ENV, ""),
        {"discord.com"},
        "/api/webhooks/",
    )
    return bool(url) and _post_external_json(url, {"content": notification_message(notification, 2000)})


def send_line_notification(notification: dict[str, object]) -> bool:
    access_token = os.environ.get(LINE_CHANNEL_ACCESS_TOKEN_ENV, "").strip()
    target_id = os.environ.get(LINE_TARGET_ID_ENV, "").strip()
    if not access_token or not target_id:
        return False
    retry_key = str(uuid.uuid5(uuid.NAMESPACE_URL, str(notification.get("notification_key") or "")))
    return _post_external_json(
        "https://api.line.me/v2/bot/message/push",
        {
            "to": target_id,
            "messages": [{"type": "text", "text": notification_message(notification, 5000)}],
        },
        {"Authorization": f"Bearer {access_token}", "X-Line-Retry-Key": retry_key},
    )


_fcm_credentials: object | None = None
_fcm_credentials_project_id = ""
_fcm_credentials_source = ""
_fcm_credentials_lock = threading.Lock()


def _load_fcm_credentials() -> tuple[object, str]:
    """Load Google Application Default Credentials without importing them at startup."""
    import google.auth  # type: ignore[import-not-found]

    credentials, project_id = google.auth.default(scopes=(FCM_OAUTH_SCOPE,))
    return credentials, str(project_id or "")


def _refresh_fcm_credentials(credentials: object) -> None:
    from google.auth.transport.requests import Request  # type: ignore[import-not-found]

    credentials.refresh(Request())  # type: ignore[attr-defined]


def _fcm_access_token() -> tuple[str, str]:
    """Return a short-lived bearer token and ADC's inferred project id."""
    global _fcm_credentials, _fcm_credentials_project_id, _fcm_credentials_source

    credentials_source = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    try:
        with _fcm_credentials_lock:
            if _fcm_credentials is None or credentials_source != _fcm_credentials_source:
                credentials, project_id = _load_fcm_credentials()
                _fcm_credentials = credentials
                _fcm_credentials_project_id = project_id
                _fcm_credentials_source = credentials_source
            if not bool(getattr(_fcm_credentials, "valid", False)):
                _refresh_fcm_credentials(_fcm_credentials)
            token = str(getattr(_fcm_credentials, "token", "") or "")
            return token, _fcm_credentials_project_id
    # Push is an optional delivery channel. Missing/invalid ADC or a token
    # refresh failure must not abort feed/email delivery for the same reminder.
    except Exception:
        return "", ""


class _NoFcmRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _open_fcm_request(request: urllib.request.Request):
    opener = urllib.request.build_opener(_NoFcmRedirects())
    return opener.open(request, timeout=TIMEOUT_SECONDS)


def _fcm_error_code(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (AttributeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    details = payload.get("error", {}).get("details", []) if isinstance(payload, dict) else []
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"])
    return ""


def send_push_notification(
    notification: dict[str, object],
    devices: Sequence[DeviceToken],
    invalid_tokens: set[str] | None = None,
) -> bool:
    tokens = list(dict.fromkeys(device.token.strip() for device in devices if device.token.strip()))
    if not tokens:
        return False
    access_token, inferred_project_id = _fcm_access_token()
    project_id = os.environ.get(FCM_PROJECT_ID_ENV, "").strip() or inferred_project_id
    if not access_token or not project_id:
        return False

    title, body = notification_headline(notification)
    data = {
        key: str(value)
        for key, value in {
            "event_id": notification.get("event_id"),
            "location": notification.get("location"),
            "field": notification.get("field"),
            "date": notification.get("date"),
            "url": notification.get("url"),
        }.items()
        if value is not None
    }
    endpoint = (
        "https://fcm.googleapis.com/v1/projects/"
        f"{urllib.parse.quote(project_id, safe='')}/messages:send"
    )
    all_sent = True
    for token in tokens:
        payload = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": data,
            }
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"},
            method="POST",
        )
        try:
            with _open_fcm_request(request) as response:
                response.read()
        except urllib.error.HTTPError as error:
            if _fcm_error_code(error) == "UNREGISTERED":
                if invalid_tokens is not None:
                    invalid_tokens.add(token)
                continue
            all_sent = False
        except (OSError, ValueError):
            all_sent = False
    return all_sent


_notification_run_lock = threading.Lock()


def _run_notifications_unlocked(
    db_path: str,
    now: str | None = None,
    lead_days: tuple[int, ...] = DEFAULT_LEAD_DAYS,
    deliver: bool = True,
    user_id: int | None = None,
) -> list[dict[str, object]]:
    """Generate due reminders, dispatch them to each channel, and record them."""
    timestamp = notification_timestamp(now)
    pending = pending_notifications(db_path, timestamp, lead_days, user_id=user_id)
    if not pending:
        return []
    owner_ids = {int(notification.get("user_id") or 0) for notification in pending}
    devices_by_user = {owner_id: list_devices(db_path, user_id=owner_id) for owner_id in owner_ids}
    delivered: list[dict[str, object]] = []
    with connect(db_path) as connection:
        init_db(connection)
        for notification in pending:
            previous_results = notification.get("_previous_delivered", {})
            previous_delivery = {
                str(channel): value is True
                for channel, value in previous_results.items()
                if not str(channel).startswith("_")
            } if isinstance(previous_results, dict) else {}
            claimed = claim_notification_delivery(
                connection,
                str(notification["notification_key"]),
                int(notification["subscription_id"]),
                notification.get("event_id"),
                str(notification["channels"]),
                previous_delivery,
                timestamp,
                str(notification["_claim_stale_before"]),
                existed=bool(notification["_claim_existed"]),
                expected_attempt_count=int(notification["_claim_attempt_count"]),
            )
            if not claimed:
                continue
            connection.commit()
            channels = parsed_notification_channels(str(notification["channels"]))
            results = dict(previous_delivery)
            results["feed"] = True
            try:
                owner_id = int(notification.get("user_id") or 0)
                if deliver and "email" in channels:
                    if not results.get("email", False):
                        results["email"] = send_email_notification(notification) if owner_id == 0 else False
                if deliver and "push" in channels:
                    if not results.get("push", False):
                        invalid_tokens: set[str] = set()
                        results["push"] = send_push_notification(
                            notification,
                            devices_by_user[owner_id],
                            invalid_tokens,
                        )
                        if invalid_tokens:
                            for token in invalid_tokens:
                                connection.execute("DELETE FROM device_tokens WHERE token = ?", (token,))
                            devices_by_user[owner_id] = [
                                device
                                for device in devices_by_user[owner_id]
                                if device.token not in invalid_tokens
                            ]
                for channel, sender in (
                    ("slack", send_slack_notification),
                    ("discord", send_discord_notification),
                    ("line", send_line_notification),
                ):
                    if deliver and channel in channels and not results.get(channel, False):
                        results[channel] = sender(notification) if owner_id == 0 else False
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
                connection.commit()
                delivered.append(payload)
            except Exception:
                connection.execute(
                    "UPDATE notification_log SET processing_at = NULL WHERE notification_key = ?",
                    (str(notification["notification_key"]),),
                )
                connection.commit()
                raise
    return delivered


def run_notifications(
    db_path: str,
    now: str | None = None,
    lead_days: tuple[int, ...] = DEFAULT_LEAD_DAYS,
    deliver: bool = True,
    user_id: int | None = None,
) -> list[dict[str, object]]:
    """Serialize delivery in one server process to avoid duplicate sends."""
    with _notification_run_lock:
        return _run_notifications_unlocked(db_path, now, lead_days, deliver, user_id)


def notification_has_delivery_failure(notification: dict[str, object]) -> bool:
    delivered = notification.get("delivered")
    return isinstance(delivered, dict) and any(value is False for value in delivered.values())


def notification_configuration_status(db_path: str) -> dict[str, object]:
    """Check delivery configuration without exposing credentials or tokens."""
    subscriptions = list_subscriptions(db_path, enabled_only=True)
    push_subscriptions = [
        subscription
        for subscription in subscriptions
        if "push" in parsed_notification_channels(subscription.channels)
    ]
    email_subscriptions = [
        subscription
        for subscription in subscriptions
        if "email" in parsed_notification_channels(subscription.channels)
    ]
    external_subscriptions = {
        channel: [
            subscription
            for subscription in subscriptions
            if channel in parsed_notification_channels(subscription.channels)
        ]
        for channel in ("slack", "discord", "line")
    }
    push_owner_ids = sorted({subscription.user_id for subscription in push_subscriptions})
    missing_device_owner_ids = [
        owner_id for owner_id in push_owner_ids if not list_devices(db_path, user_id=owner_id)
    ]

    access_token, inferred_project_id = _fcm_access_token() if push_subscriptions else ("", "")
    project_id = os.environ.get(FCM_PROJECT_ID_ENV, "").strip() or inferred_project_id
    push_credentials_ready = bool(access_token and project_id)
    smtp_host_configured = bool(os.environ.get(SMTP_HOST_ENV, "").strip())
    smtp_recipient_configured = bool(os.environ.get(NOTIFY_EMAIL_ENV, "").strip())
    anonymous_email_subscriptions = sum(subscription.user_id <= 0 for subscription in email_subscriptions)
    account_email_subscriptions = len(email_subscriptions) - anonymous_email_subscriptions
    slack_ready = bool(
        _validated_webhook_url(
            os.environ.get(SLACK_WEBHOOK_URL_ENV, ""),
            {"hooks.slack.com", "hooks.slack-gov.com"},
            "/services/",
        )
    )
    discord_ready = bool(
        _validated_webhook_url(
            os.environ.get(DISCORD_WEBHOOK_URL_ENV, ""),
            {"discord.com"},
            "/api/webhooks/",
        )
    )
    line_ready = bool(
        os.environ.get(LINE_CHANNEL_ACCESS_TOKEN_ENV, "").strip()
        and os.environ.get(LINE_TARGET_ID_ENV, "").strip()
    )
    external_ready = {"slack": slack_ready, "discord": discord_ready, "line": line_ready}

    issues: list[str] = []
    if push_subscriptions and not push_credentials_ready:
        issues.append("FCM ADC credentials or Firebase project id are unavailable")
    if missing_device_owner_ids:
        issues.append("push subscriptions exist without a registered device")
    if anonymous_email_subscriptions and not (smtp_host_configured and smtp_recipient_configured):
        issues.append("anonymous email subscriptions require SMTP host and recipient settings")
    if account_email_subscriptions:
        issues.append("account-scoped email delivery is not configured; use feed or push")
    for channel, channel_subscriptions in external_subscriptions.items():
        anonymous_count = sum(subscription.user_id <= 0 for subscription in channel_subscriptions)
        account_count = len(channel_subscriptions) - anonymous_count
        if anonymous_count and not external_ready[channel]:
            issues.append(f"{channel} subscriptions require valid local delivery credentials")
        if account_count:
            issues.append(f"account-scoped {channel} delivery is not configured; use feed or push")

    return {
        "ok": not issues,
        "push": {
            "subscriptions": len(push_subscriptions),
            "owner_count": len(push_owner_ids),
            "owners_without_devices": len(missing_device_owner_ids),
            "credentials_ready": push_credentials_ready,
            "project_id_available": bool(project_id),
        },
        "email": {
            "anonymous_subscriptions": anonymous_email_subscriptions,
            "account_subscriptions": account_email_subscriptions,
            "smtp_host_configured": smtp_host_configured,
            "recipient_configured": smtp_recipient_configured,
        },
        "external": {
            channel: {
                "anonymous_subscriptions": sum(
                    subscription.user_id <= 0 for subscription in channel_subscriptions
                ),
                "account_subscriptions": sum(
                    subscription.user_id > 0 for subscription in channel_subscriptions
                ),
                "configured": external_ready[channel],
            }
            for channel, channel_subscriptions in external_subscriptions.items()
        },
        "issues": issues,
    }


def notification_feed(db_path: str, limit: int = 100, user_id: int | None = None) -> list[dict[str, object]]:
    """Recent reminders for the in-app/mobile notifications feed."""
    with connect(db_path) as connection:
        init_db(connection)
        if user_id is None:
            rows = connection.execute(
                """
                SELECT payload_json, channel, created_at
                FROM notification_log
                WHERE processing_at IS NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT n.payload_json, n.channel, n.created_at
                FROM notification_log n
                JOIN notification_subscriptions s ON s.id = n.subscription_id
                WHERE s.user_id = ? AND n.processing_at IS NULL
                ORDER BY n.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
    feed: list[dict[str, object]] = []
    for payload_json, channel, created_at in rows:
        payload = json.loads(payload_json)
        payload["channel"] = channel
        payload["created_at"] = created_at
        feed.append(payload)
    return feed
