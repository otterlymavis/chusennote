from __future__ import annotations

import importlib.util
import os
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-hosted-monitor.py"
SPEC = importlib.util.spec_from_file_location("run_hosted_monitor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def test_hosted_monitor_requires_postgres(monkeypatch, capsys):
    monkeypatch.delenv("CHUSENNOTE_DATABASE_URL", raising=False)
    assert monitor.main() == 2
    assert "requires CHUSENNOTE_DATABASE_URL" in capsys.readouterr().err


def test_hosted_monitor_runs_both_lanes_and_reminders(monkeypatch, capsys):
    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "postgresql://example.invalid/db")
    calls = []
    monkeypatch.setattr(monitor, "run_watches", lambda db: calls.append(("watches", db)) or [{"type": "new_event"}])
    monkeypatch.setattr(monitor, "run_notifications", lambda db: calls.append(("reminders", db)) or [])
    assert monitor.main() == 0
    assert calls == [
        ("watches", "postgresql://example.invalid/db"),
        ("reminders", "postgresql://example.invalid/db"),
    ]
    assert "1 alerts, 0 watch failures" in capsys.readouterr().out


def test_hosted_monitor_redacts_provider_errors(monkeypatch, capsys):
    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "postgresql://example.invalid/db")

    def fail(_db):
        raise RuntimeError("secret://token@example.invalid")

    monkeypatch.setattr(monitor, "run_watches", fail)
    assert monitor.main() == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "token" not in output.err


def test_hosted_monitor_reports_watch_failure_without_watch_details(monkeypatch, capsys):
    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setattr(
        monitor,
        "run_watches",
        lambda _db: [{"type": "watch_failed", "keyword": "private artist", "error": "secret URL"}],
    )
    monkeypatch.setattr(monitor, "run_notifications", lambda _db: [])
    assert monitor.main() == 1
    output = capsys.readouterr().out
    assert "1 watch failures" in output
    assert "private artist" not in output
    assert "secret URL" not in output


def test_hosted_monitor_suppresses_library_stdout_and_stderr(monkeypatch, capfd):
    monkeypatch.setenv("CHUSENNOTE_DATABASE_URL", "postgresql://example.invalid/db")

    def noisy_watches(_db):
        print("private search term")
        os.write(1, b"private stdout URL\n")
        os.write(2, b"private stderr token\n")
        return []

    monkeypatch.setattr(monitor, "run_watches", noisy_watches)
    monkeypatch.setattr(monitor, "run_notifications", lambda _db: [])
    assert monitor.main() == 0
    output = capfd.readouterr()
    assert "Hosted monitor: 0 alerts" in output.out
    assert "private" not in output.out + output.err
