#!/usr/bin/env python3
"""Run one hosted monitoring pass without logging watch details in CI."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chusennote.notifications import notification_has_delivery_failure, run_notifications
from chusennote.pipeline import run_watches


@contextlib.contextmanager
def suppress_runtime_output():
    """Keep provider and browser diagnostics out of public Actions logs."""
    sys.stdout.flush()
    sys.stderr.flush()
    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
    finally:
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stdout_fd)
        os.close(stderr_fd)


def main() -> int:
    database_url = os.environ.get("CHUSENNOTE_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        print("Hosted monitor requires CHUSENNOTE_DATABASE_URL=postgresql://...", file=sys.stderr)
        return 2

    try:
        with suppress_runtime_output():
            alerts = run_watches(database_url)
            reminders = run_notifications(database_url)
    except Exception as error:
        # Provider/database exceptions may contain URLs, watch terms, or tokens.
        print(f"Hosted monitor failed ({type(error).__name__}); inspect the private runtime.", file=sys.stderr)
        return 1

    watch_failures = sum(alert.get("type") == "watch_failed" for alert in alerts)
    delivery_failures = sum(notification_has_delivery_failure(item) for item in reminders)
    print(
        f"Hosted monitor: {len(alerts)} alerts, {watch_failures} watch failures, "
        f"{len(reminders)} reminders, {delivery_failures} delivery failures."
    )
    return 1 if watch_failures or delivery_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
