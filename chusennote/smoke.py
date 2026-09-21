"""Read-only deployment smoke checks for the web, JSON API, and calendar."""

from __future__ import annotations

import ipaddress
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from .models import APP_BUILD, APP_VERSION, DB_SCHEMA_VERSION


SMOKE_ENDPOINTS = (
    ("home", "/", "html"),
    ("privacy", "/privacy", "privacy"),
    ("support", "/support", "support"),
    ("health", "/api/health", "health"),
    ("watchlist", "/api/watchlist?include_muted=1", "list"),
    ("events", "/api/events", "list"),
    ("upcoming", "/api/upcoming", "list"),
    ("alerts", "/api/alerts", "list"),
    ("notifications", "/api/notifications?limit=1", "list"),
    ("subscriptions", "/api/subscriptions", "list"),
    ("devices", "/api/devices", "list"),
    ("sources", "/api/sources?include_muted=1", "list"),
    ("calendar", "/calendar.ics", "calendar"),
)
SMOKE_RESPONSE_LIMIT = 1_000_000
_LOCAL_CREDENTIAL_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep credentials on the exact host selected by the operator."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _permits_credential_transport(parsed: urllib.parse.SplitResult) -> bool:
    if parsed.scheme == "https":
        return True
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "http":
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _LOCAL_CREDENTIAL_NETWORKS)


def _validate_smoke_payload(kind: str, payload: bytes, *, require_postgres: bool = False) -> None:
    if kind == "html":
        if "chusennote" not in payload.decode("utf-8", errors="replace").lower():
            raise ValueError("home page does not identify chusennote")
        return
    if kind in {"privacy", "support"}:
        text = payload.decode("utf-8", errors="replace").lower()
        required = (
            ("privacy policy", "does not sell personal information", "/support")
            if kind == "privacy"
            else ("chusennote support", "github.com/otterlymavis/chusennote/issues/new", "/privacy")
        )
        if not all(marker in text for marker in required):
            raise ValueError(f"{kind} page is missing required content")
        return
    if kind == "calendar":
        if b"BEGIN:VCALENDAR" not in payload or b"END:VCALENDAR" not in payload:
            raise ValueError("calendar response is not an iCalendar document")
        return
    decoded = json.loads(payload)
    if kind == "list":
        if not isinstance(decoded, list):
            raise ValueError("expected a JSON list")
        return
    if not isinstance(decoded, dict):
        raise ValueError("expected a JSON object")
    if decoded.get("status") != "ok":
        raise ValueError("health status is not ok")
    if decoded.get("version") != APP_VERSION or decoded.get("build") != APP_BUILD:
        raise ValueError(f"release metadata does not match {APP_VERSION} build {APP_BUILD}")
    if int(decoded.get("schema_version") or 0) < DB_SCHEMA_VERSION:
        raise ValueError(f"schema version is older than {DB_SCHEMA_VERSION}")
    if require_postgres and decoded.get("db_path") != "postgresql":
        raise ValueError("expected PostgreSQL backend")


def smoke_deployment(
    base_url: str,
    timeout: float = 10.0,
    api_token: str = "",
    opener: Callable[..., object] | None = None,
    require_postgres: bool = False,
) -> dict[str, object]:
    """Probe public read surfaces without mutating application state."""
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        return {"ok": False, "base_url": None, "checks": [], "errors": ["timeout must be finite and greater than zero"]}
    try:
        parsed_base = urllib.parse.urlsplit(base_url.strip())
    except ValueError:
        parsed_base = urllib.parse.SplitResult("", "", "", "", "")
    valid_base = (
        parsed_base.scheme in {"http", "https"}
        and bool(parsed_base.hostname)
        and not parsed_base.username
        and not parsed_base.password
        and not parsed_base.query
        and not parsed_base.fragment
    )
    if not valid_base:
        return {
            "ok": False,
            "base_url": None,
            "checks": [],
            "errors": ["base URL must be credential-free http or https without a query or fragment"],
        }
    if api_token.strip() and not _permits_credential_transport(parsed_base):
        return {
            "ok": False,
            "base_url": None,
            "checks": [],
            "errors": [
                "API tokens require HTTPS, localhost, or a literal private-network IP"
            ],
        }
    base = urllib.parse.urlunsplit(
        (parsed_base.scheme, parsed_base.netloc, parsed_base.path.rstrip("/"), "", "")
    )
    open_request = opener or urllib.request.build_opener(RejectRedirects()).open
    checks: list[dict[str, object]] = []
    errors: list[str] = []
    for name, endpoint, kind in SMOKE_ENDPOINTS:
        request = urllib.request.Request(base + endpoint, method="GET")
        if api_token.strip():
            request.add_header("Authorization", f"Bearer {api_token.strip()}")
        try:
            with open_request(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                payload = response.read(SMOKE_RESPONSE_LIMIT + 1)
            if status != 200:
                raise ValueError(f"HTTP {status}")
            if len(payload) > SMOKE_RESPONSE_LIMIT:
                raise ValueError("response exceeds 1 MB")
            _validate_smoke_payload(kind, payload, require_postgres=require_postgres)
            checks.append({"name": name, "endpoint": endpoint, "ok": True})
        except (OSError, ValueError, TypeError, json.JSONDecodeError, urllib.error.HTTPError) as error:
            message = f"{name}: {error}"
            checks.append({"name": name, "endpoint": endpoint, "ok": False})
            errors.append(message)
    return {"ok": not errors, "base_url": base, "checks": checks, "errors": errors}
