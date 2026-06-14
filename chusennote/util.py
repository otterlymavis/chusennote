"""Pure helpers for chusennote: text, URL classification, and session logging.

Depends only on :mod:`chusennote.models`, never on netio/storage/web/cli, so it
stays a safe mid-level leaf in the import DAG.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import shlex
import sys
import urllib.parse
from collections.abc import Sequence

from .models import (
    DEFAULT_SESSION_LOG_DIR,
    OFFICIAL_HINTS,
    OFFICIAL_HOST_HINTS,
    SOCIAL_OR_NOISY_DOMAINS,
    TICKET_DOMAINS,
)


def configure_cli_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def split_session_log_args(argv: Sequence[str]) -> tuple[list[str], bool, str]:
    cleaned: list[str] = []
    enabled = False
    log_dir = DEFAULT_SESSION_LOG_DIR
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--session-log":
            enabled = True
        elif item == "--session-log-dir":
            index += 1
            if index >= len(argv):
                raise SystemExit("--session-log-dir requires a value")
            log_dir = argv[index]
        elif item.startswith("--session-log-dir="):
            log_dir = item.split("=", 1)[1]
        else:
            cleaned.append(item)
        index += 1
    return cleaned, enabled, log_dir


def format_shell_args(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in argv)


def session_log_path(log_dir: str = DEFAULT_SESSION_LOG_DIR, now: dt.datetime | None = None) -> pathlib.Path:
    timestamp = now or dt.datetime.now().astimezone()
    return pathlib.Path(log_dir) / f"session_{timestamp:%Y_%m_%d}.md"


def append_session_log(
    argv: Sequence[str],
    args: argparse.Namespace,
    exit_code: int,
    started_at: dt.datetime,
    ended_at: dt.datetime | None = None,
) -> pathlib.Path:
    ended_at = ended_at or dt.datetime.now().astimezone()
    path = session_log_path(args.session_log_dir, ended_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = format_shell_args(("lottery_monitor.py", *argv))
    db_path = getattr(args, "db", None)
    target = getattr(args, "command", "unknown")
    details = [
        f"## {started_at.isoformat(timespec='seconds')}",
        "",
        f"- Command: `{command}`",
        f"- Target: `{target}`",
        f"- Exit code: `{exit_code}`",
        f"- Duration seconds: `{(ended_at - started_at).total_seconds():.3f}`",
    ]
    if db_path:
        details.append(f"- Database: `{db_path}`")
    details.append("")
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write("\n".join(details))
    return path


def absolute_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def hostname(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def is_noisy_url(url: object) -> bool:
    host = hostname(str(url or ""))
    return any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS)


def is_ticket_url(url: str) -> bool:
    host = hostname(url)
    return any(any(domain in host for domain in domains) for domains in TICKET_DOMAINS.values())


def is_actionable_ticket_link(url: str, label: str = "") -> bool:
    if is_noisy_url(url) or is_generic_ticket_info_url(url):
        return False
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    fragment = parsed.fragment.lower()
    haystack = f"{label} {url}".lower()
    if "hoken" in haystack or "insurance" in haystack:
        return False
    if host in OFFICIAL_HOST_HINTS and (fragment in {"schedule", "ticket", "tickets"} or "/stage/" in path):
        return False
    return is_ticket_url(url)


def is_portal_search_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    return (
        (host == "t.pia.jp" and path.endswith("/search_all.do"))
        or (host == "eplus.jp" and path.startswith("/sf/search"))
        or (host == "l-tike.com" and path.startswith("/search/"))
    )


def is_shiki_stage_schedule_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host == "shiki.jp" and parsed.path == "/stage_schedule/"


def is_generic_ticket_info_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return host == "shiki.jp" and (
        path in {"/tickets", "/tickets_guide", "/door", "/special/info/warning"}
        or path.startswith("/tickets_guide/")
    )


def source_name_for_url(url: str) -> str:
    host = hostname(url)
    for name, domains in TICKET_DOMAINS.items():
        if any(domain in host for domain in domains):
            return name
    return host or "unknown"


def source_provenance(url: str, label: str = "") -> str:
    platform = source_name_for_url(url)
    haystack = f"{label} {url}".lower()
    if platform in TICKET_DOMAINS:
        return "ticket_primary"
    if any(hint.lower() in haystack for hint in OFFICIAL_HINTS):
        return "official"
    if platform == "unknown":
        return "low_confidence"
    return "manual_public"


def is_web_url(url: object) -> bool:
    parsed = urllib.parse.urlparse(str(url or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def platform_confidence(platform: str) -> int:
    if platform in TICKET_DOMAINS:
        return 90
    if platform in {"official", "manual"}:
        return 70
    return 50


def infer_round_type(name: str) -> str:
    text = name.lower()
    if "fc" in text or "ファンクラブ" in text:
        return "fc"
    if "一般発売" in name:
        return "general"
    if "トレード" in name or "リセール" in name:
        return "trade"
    if "公式" in name or "オフィシャル" in name:
        return "official"
    if any(token in text for token in ("pia", "ぴあ", "e+", "ローソン", "lawson")):
        return "platform"
    if any(token in name for token in ("抽選", "先行", "プレオーダー")):
        return "platform"
    return "unknown"


def infer_membership_required(name: str, evidence: str = "") -> str:
    text = f"{name} {evidence}".lower()
    if "fc" in text or "ファンクラブ" in text or "会員" in text:
        return "yes"
    if "一般発売" in name:
        return "no"
    return "unknown"


def normalize_round_name(name: str) -> str:
    return clean_text(name).lower()


JP_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def parse_round_number(value: str) -> int | None:
    match = re.search(r"([0-9０-９一二三四五六七八九十]+)\s*次", value)
    if not match:
        return None
    raw = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw.startswith("十") and len(raw) == 2:
        return 10 + JP_NUMERALS.get(raw[1], 0)
    if raw.endswith("十") and len(raw) == 2:
        return JP_NUMERALS.get(raw[0], 0) * 10
    if "十" in raw and len(raw) == 3:
        return JP_NUMERALS.get(raw[0], 0) * 10 + JP_NUMERALS.get(raw[2], 0)
    return JP_NUMERALS.get(raw)
