import datetime as dt
import json
import pathlib
import sqlite3
import threading
import urllib.parse
import urllib.request

import lottery_monitor as lm


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_windows_task_scheduler_helpers_keep_expected_contract():
    install_script = (ROOT / "scripts" / "install-chusennote-monitor-task.ps1").read_text()
    show_script = (ROOT / "scripts" / "show-chusennote-monitor-task.ps1").read_text()
    uninstall_script = (ROOT / "scripts" / "uninstall-chusennote-monitor-task.ps1").read_text()

    assert "Register-ScheduledTask" in install_script
    assert "New-ScheduledTaskTrigger" in install_script
    assert '"event", "artist"' in install_script
    assert "lottery_monitor.py" in install_script
    assert '"run"' in install_script
    assert "Get-ScheduledTaskInfo" in show_script
    assert "Unregister-ScheduledTask" in uninstall_script


def test_extract_ticket_links_from_official_page():
    html = """
    <html><head><title>Example Musical Official</title></head>
    <body>
      <h1>Example Musical 2026</h1>
      <p>公演日 2026年7月10日 会場 Example Hall</p>
      <a href="https://eplus.jp/example-musical/">チケット抽選先行はこちら</a>
      <a href="/news">News</a>
    </body></html>
    """
    page = lm.parse_page("https://official.example/stage", html)

    info = lm.build_event_info("Example Musical", [page])

    assert info.title == "Example Musical Official"
    assert info.official_page == "https://official.example/stage"
    assert info.ticket_links[0].url == "https://eplus.jp/example-musical/"
    assert "公演日" in info.event_dates[0]
    assert "会場" in info.venues[0]


def test_extract_ticket_rounds_with_japanese_lottery_dates():
    html = """
    <html><head><title>Ticket</title></head><body>
      <section>
        <h2>第1次抽選先行</h2>
        <p>受付期間 2026年6月10日(水) 12:00 ～ 2026年6月18日(木) 23:59</p>
        <p>抽選結果発表 2026年6月22日(月)</p>
        <p>入金期間 2026年6月22日(月) ～ 2026年6月25日(木)</p>
      </section>
      <section>
        <h2>一般発売</h2>
        <p>発売日 2026/07/04 10:00</p>
      </section>
    </body></html>
    """
    page = lm.parse_page("https://t.pia.jp/pia/event/example", html)

    rounds = lm.extract_ticket_rounds(page)

    assert rounds[0].name == "第1次抽選先行"
    assert rounds[0].lottery_start == "2026-06-10"
    assert rounds[0].lottery_end == "2026-06-18"
    assert rounds[0].results_date == "2026-06-22"
    assert rounds[0].payment_deadline == "2026-06-22"
    assert any(round_.general_sale_date == "2026-07-04" for round_ in rounds)


def test_render_blocks_has_two_expected_app_blocks():
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-18",
                results_date="2026-06-22",
            ),
        ),
    )

    rendered = lm.render_blocks(blocks)

    assert "# General event info" in rendered
    assert "# Ticket / lottery info" in rendered
    assert "第1次抽選先行" in rendered


def test_save_blocks_persists_initial_monitoring_state(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-18",
                results_date="2026-06-22",
            ),
        ),
    )

    alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")

    assert {"type": "new_official_page", "event": "Example Tour", "url": "https://official.example/"} in alerts
    assert any(alert["type"] == "new_ticket_link" for alert in alerts)
    assert any(alert["type"] == "new_lottery_round" for alert in alerts)


def test_save_blocks_emits_alert_when_ticket_dates_change(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    original = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-18",
            ),
        ),
    )
    changed = lm.AppBlocks(
        general_info=original.general_info,
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-10",
                lottery_end="2026-06-20",
            ),
        ),
    )

    lm.save_blocks(str(db_path), original, now="2026-06-03T00:00:00+00:00")
    alerts = lm.save_blocks(str(db_path), changed, now="2026-06-04T00:00:00+00:00")

    assert alerts == [
        {
            "type": "ticket_field_changed",
            "event": "Example Tour",
            "round": "第1次抽選先行",
            "field": "lottery_end",
            "old": "2026-06-18",
            "new": "2026-06-20",
            "url": "https://t.pia.jp/example",
        }
    ]


def test_save_blocks_emits_lifecycle_alerts_for_upcoming_dates(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
                results_date="2026-06-03",
                general_sale_date="2026-06-04",
                payment_deadline="2026-06-04",
            ),
        ),
    )

    alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T09:00:00+00:00")
    alert_types = {alert["type"] for alert in alerts}

    assert "lottery_opened" in alert_types
    assert "lottery_closing_soon" in alert_types
    assert "results_today" in alert_types
    assert "general_sale_soon" in alert_types
    assert "payment_due_soon" in alert_types


def test_save_blocks_does_not_repeat_lifecycle_alerts(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="公演情報",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
                evidence="受付期間 2026年6月3日 ～ 2026年6月5日",
            ),
        ),
    )

    first_alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T09:00:00+00:00")
    second_alerts = lm.save_blocks(str(db_path), blocks, now="2026-06-03T10:00:00+00:00")

    assert any(alert["type"] == "lottery_opened" for alert in first_alerts)
    assert not any(alert["type"] in {"lottery_opened", "lottery_closing_soon"} for alert in second_alerts)


def test_init_db_migrates_existing_current_schema(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE watched_keywords (
                id INTEGER PRIMARY KEY,
                keyword TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE ticket_rounds (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                round_key TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                lottery_start TEXT,
                lottery_end TEXT,
                results_date TEXT,
                general_sale_date TEXT,
                payment_deadline TEXT,
                evidence TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        lm.init_db(connection)
        watched_columns = lm.table_columns(connection, "watched_keywords")
        event_columns = lm.table_columns(connection, "events")
        round_columns = lm.table_columns(connection, "ticket_rounds")
        source_columns = lm.table_columns(connection, "watch_sources")
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {"tags", "preferred_regions", "preferred_venues", "muted", "last_checked_at"} <= watched_columns
    assert {"event_dates_json", "venues_json"} <= event_columns
    assert {"platform", "application_start_at", "application_end_at", "confidence", "status"} <= round_columns
    assert {"watch_id", "url", "private_note", "muted"} <= source_columns
    assert user_version == lm.DB_SCHEMA_VERSION


def test_round_number_status_and_dedupe_timeline():
    ticket = lm.TicketRound(
        source="pia",
        platform="pia",
        url="https://t.pia.jp/example",
        name="第２次抽選先行",
        lottery_start="2026-06-01",
        lottery_end="2026-06-04",
    )

    normalized = lm.normalize_ticket_round(ticket, today=lm.dt.date(2026, 6, 3))
    deduped = lm.dedupe_ticket_rounds((ticket, ticket), today=lm.dt.date(2026, 6, 3))

    assert normalized.round_number == 2
    assert normalized.application_start_at == "2026-06-01"
    assert normalized.application_end_at == "2026-06-04"
    assert normalized.status == "closing_soon"
    assert len(deduped) == 1


def test_adapter_dispatch_labels_ticket_platform():
    html = """
    <html><body>
      <h2>第1次抽選先行</h2>
      <p>受付期間 2026年6月10日 ～ 2026年6月18日</p>
    </body></html>
    """
    page = lm.parse_page("https://eplus.jp/example", html)

    rounds = lm.extract_ticket_rounds_for_page(page)

    assert rounds[0].platform == "eplus"
    assert rounds[0].source == "eplus"
    assert rounds[0].confidence == 90


def test_adapter_dispatch_covers_additional_ticket_platforms():
    html = """
    <html><body>
      <h2>先行抽選</h2>
      <p>抽選申込期間 2026年6月10日 ～ 2026年6月18日</p>
      <p>結果発表 2026年6月22日</p>
      <p>支払期限 2026年6月25日</p>
    </body></html>
    """
    cases = (
        ("https://ticket.rakuten.co.jp/music/example", "rakuten"),
        ("https://ticket.tickebo.jp/example", "ticketboard"),
        ("https://www.cnplayguide.com/example", "cnplayguide"),
    )

    for url, platform in cases:
        rounds = lm.extract_ticket_rounds_for_page(lm.parse_page(url, html))

        assert rounds[0].platform == platform
        assert rounds[0].source == platform
        assert rounds[0].application_start_at == "2026-06-10"
        assert rounds[0].application_end_at == "2026-06-18"
        assert rounds[0].results_date == "2026-06-22"
        assert rounds[0].payment_end_at == "2026-06-25"


def example_blocks(keyword="Example"):
    return lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword=keyword,
            official_page="https://official.example/",
            title=f"{keyword} Tour",
            summary="公演情報",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Example Hall",),
            ticket_links=(lm.Link("Pia", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="第1次抽選先行",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
            ),
        ),
    )


def test_legacy_cli_invocation_still_renders_blocks(monkeypatch, capsys):
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["Example"]) == 0
    output = capsys.readouterr().out

    assert "# General event info" in output
    assert "Example Tour" in output


def test_search_cli_json_outputs_blocks(monkeypatch, capsys):
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["search", "Example", "--json"]) == 0
    output = capsys.readouterr().out

    assert '"keyword": "Example"' in output
    assert '"ticket_info"' in output


def test_watch_add_list_remove_cli(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"

    assert lm.main(["watch", "add", "Example", "--db", str(db_path), "--tags", "musical"]) == 0
    add_output = capsys.readouterr().out
    assert "Added watch 1: Example" in add_output

    assert lm.main(["watch", "list", "--db", str(db_path), "--json"]) == 0
    list_output = capsys.readouterr().out
    assert '"keyword": "Example"' in list_output
    assert '"tags": "musical"' in list_output

    assert lm.main(["watch", "remove", "Example", "--db", str(db_path)]) == 0
    remove_output = capsys.readouterr().out
    assert "Removed watch." in remove_output

    assert lm.main(["watch", "list", "--db", str(db_path)]) == 0
    assert "No active watches." in capsys.readouterr().out

    assert lm.main(["watch", "unmute", "Example", "--db", str(db_path)]) == 0
    unmute_output = capsys.readouterr().out
    assert "Unmuted watch." in unmute_output

    assert lm.main(["watch", "list", "--db", str(db_path), "--json"]) == 0
    restored_output = capsys.readouterr().out
    assert '"muted": false' in restored_output

    assert lm.main(["watch", "mute", "Example", "--db", str(db_path)]) == 0
    mute_output = capsys.readouterr().out
    assert "Muted watch." in mute_output

    assert lm.main(["watch", "list", "--db", str(db_path)]) == 0
    assert "No active watches." in capsys.readouterr().out


def test_kind_watch_mute_unmute_cli(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"

    assert lm.main(["event", "add", "Example Event", "--db", str(db_path)]) == 0
    capsys.readouterr()

    assert lm.main(["event", "mute", "Example Event", "--db", str(db_path)]) == 0
    assert "Muted tracked event." in capsys.readouterr().out

    assert lm.main(["event", "list", "--db", str(db_path)]) == 0
    assert "No active watches." in capsys.readouterr().out

    assert lm.main(["event", "unmute", "Example Event", "--db", str(db_path)]) == 0
    assert "Unmuted tracked event." in capsys.readouterr().out

    assert lm.main(["event", "list", "--db", str(db_path), "--json"]) == 0
    assert '"keyword": "Example Event"' in capsys.readouterr().out


def test_watch_run_cli_outputs_alerts_json(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["watch", "run", "--db", str(db_path), "--alerts-json"]) == 0
    output = capsys.readouterr().out

    assert '"type": "new_official_page"' in output
    assert '"type": "new_lottery_round"' in output


def test_watch_run_continues_after_single_watch_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Broken", now="2026-06-01T00:00:00+00:00")
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")

    def fake_build(db_path_value, watch):
        if watch.keyword == "Broken":
            raise OSError("network failed")
        return example_blocks(watch.keyword)

    monkeypatch.setattr(lm, "build_blocks_for_watch", fake_build)

    alerts = lm.run_watches(str(db_path), now="2026-06-03T00:00:00+00:00")
    watches = lm.list_watches(str(db_path))

    assert any(alert["type"] == "watch_failed" and alert["keyword"] == "Broken" for alert in alerts)
    assert any(alert["type"] == "new_official_page" and alert["event"] == "Example Tour" for alert in alerts)
    assert all(watch.last_checked_at == "2026-06-03T00:00:00+00:00" for watch in watches)


def test_watch_loop_runs_selected_kind_multiple_times(capsys):
    calls = []

    def fake_run(db_path, kind=None):
        calls.append((db_path, kind))
        return [{"type": "example"}]

    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=0,
        kind=lm.WATCH_KIND_ARTIST,
        max_runs=2,
        run_func=fake_run,
        sleep_func=lambda seconds: None,
    ) == 0

    assert calls == [("loop.sqlite3", lm.WATCH_KIND_ARTIST), ("loop.sqlite3", lm.WATCH_KIND_ARTIST)]
    output = capsys.readouterr().out
    assert "Run 1: checked artist watches; 1 alerts." in output
    assert "Run 2: checked artist watches; 1 alerts." in output


def test_watch_loop_outputs_json_batches(capsys):
    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=0,
        kind=lm.WATCH_KIND_EVENT,
        max_runs=1,
        alerts_json=True,
        run_func=lambda db_path, kind=None: [{"type": "example", "kind": kind}],
        sleep_func=lambda seconds: None,
    ) == 0

    output = capsys.readouterr().out
    assert '"run": 1' in output
    assert '"kind": "event"' in output


def test_watch_loop_keyboard_interrupt_exits_cleanly(capsys):
    def interrupt(seconds):
        raise KeyboardInterrupt

    assert lm.run_watch_loop(
        "loop.sqlite3",
        interval_minutes=1,
        run_immediately=False,
        max_runs=1,
        sleep_func=interrupt,
        run_func=lambda db_path, kind=None: [],
    ) == 0

    assert "Watch loop stopped." in capsys.readouterr().out


def test_watch_loop_argparse_validation():
    for args in (
        ["watch", "loop", "--interval-minutes", "-1"],
        ["watch", "loop", "--max-runs", "0"],
        ["watch", "loop", "--stop-after-errors", "0"],
    ):
        try:
            lm.parse_args(args)
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError(f"expected argparse failure for {args}")


def test_artist_and_event_commands_are_separate_lanes(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(lm, "build_artist_blocks", lambda keyword: lm.AppBlocks(example_blocks(keyword).general_info, ()))
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["artist", "add", "Artist Name", "--db", str(db_path)]) == 0
    assert lm.main(["event", "add", "Event Name", "--db", str(db_path)]) == 0
    capsys.readouterr()

    assert lm.main(["artist", "list", "--db", str(db_path), "--json"]) == 0
    artist_output = capsys.readouterr().out
    assert '"keyword": "Artist Name"' in artist_output
    assert '"keyword": "Event Name"' not in artist_output

    assert lm.main(["event", "run", "--db", str(db_path), "--alerts-json"]) == 0
    event_alerts = capsys.readouterr().out
    assert '"type": "new_lottery_round"' in event_alerts

    assert lm.main(["artist", "run", "--db", str(db_path), "--alerts-json"]) == 0
    artist_alerts = capsys.readouterr().out
    assert '"type": "new_lottery_round"' not in artist_alerts


def test_alert_preferences_and_venue_filters_reduce_noise(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(
        str(db_path),
        "Example",
        kind=lm.WATCH_KIND_EVENT,
        preferred_venues="Different Hall",
        alert_preferences="new_lottery_round",
        now="2026-06-01T00:00:00+00:00",
    )
    monkeypatch.setattr(lm, "build_blocks_for_watch", lambda db_path_value, watch: example_blocks(watch.keyword))

    alerts = lm.run_watches(str(db_path), now="2026-06-03T00:00:00+00:00")

    assert alerts == [
        {
            "type": "watch_filtered",
            "watch_id": "1",
            "keyword": "Example",
            "reason": "preferred region/venue did not match",
        }
    ]


def test_source_provenance_and_round_metadata_are_exported(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = example_blocks("Example")

    lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")
    events = lm.recent_events(str(db_path))

    assert events[0]["status"] == "lottery_open"
    assert events[0]["event_dates"] == ["公演日 2026年7月10日"]
    assert events[0]["venues"] == ["会場 Example Hall"]
    assert any(reason.startswith("keyword match: Example") for reason in events[0]["match_reasons"])
    assert any(reason.startswith("date clue:") for reason in events[0]["match_reasons"])
    assert any(reason.startswith("venue clue:") for reason in events[0]["match_reasons"])
    assert events[0]["rounds"][0]["round_type"] == "platform"
    assert events[0]["rounds"][0]["membership_required"] == "unknown"
    assert "evidence" in events[0]["rounds"][0]


def test_export_cli_outputs_saved_events(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(str(db_path), example_blocks("Example"), now="2026-06-03T00:00:00+00:00")

    assert lm.main(["export", "events", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out

    assert '"title": "Example Tour"' in output
    assert '"status": "lottery_open"' in output
    assert '"event_dates": [' in output
    assert '"match_reasons": [' in output


def test_upcoming_export_sorts_urgent_ticket_rounds(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(
        str(db_path),
        lm.AppBlocks(
            general_info=example_blocks("Closing").general_info,
            ticket_info=(
                lm.TicketRound(
                    source="pia",
                    url="https://t.pia.jp/closing",
                    name="Closing soon",
                    lottery_start="2026-06-01",
                    lottery_end="2026-06-04",
                ),
            ),
        ),
        now="2026-06-03T00:00:00+00:00",
    )
    lm.save_blocks(
        str(db_path),
        lm.AppBlocks(
            general_info=example_blocks("Payment").general_info,
            ticket_info=(
                lm.TicketRound(
                    source="eplus",
                    url="https://eplus.jp/payment",
                    name="Payment due",
                    lottery_start="2026-05-01",
                    lottery_end="2026-05-02",
                    payment_deadline="2026-06-04",
                ),
            ),
        ),
        now="2026-06-03T00:00:00+00:00",
    )

    rows = lm.upcoming_priority_rows(str(db_path))

    assert rows[0]["status"] == "closing_soon"
    assert rows[0]["event_title"] == "Closing Tour"
    assert rows[1]["status"] == "payment_due"
    assert rows[1]["relevant_date"] == "2026-06-04"

    assert lm.main(["export", "upcoming", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out
    assert '"event_title": "Closing Tour"' in output
    assert '"match_reasons": [' in output


def test_api_health_reports_database_counts(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Artist", kind=lm.WATCH_KIND_ARTIST)
    lm.add_watch(str(db_path), "Event", kind=lm.WATCH_KIND_EVENT)
    lm.add_watch_source(str(db_path), "Event", "https://t.pia.jp/example", "Pia")
    lm.save_blocks(str(db_path), example_blocks("Event"), now="2026-06-03T00:00:00+00:00")

    health = lm.api_health(str(db_path))

    assert health["app"] == "chusennote"
    assert health["status"] == "ok"
    assert health["schema_version"] == lm.DB_SCHEMA_VERSION
    assert health["tracked_artists"] == 1
    assert health["tracked_events"] >= 1
    assert health["saved_events"] >= 1
    assert health["manual_sources"] == 1


def test_watch_source_cli_add_list_remove(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")

    assert lm.main(["watch", "source", "add", "Example", "https://fan.example/note", "--db", str(db_path), "--label", "FC note", "--private-note"]) == 0
    assert "Added source 1: FC note" in capsys.readouterr().out

    assert lm.main(["watch", "source", "list", "Example", "--db", str(db_path), "--json"]) == 0
    source_output = capsys.readouterr().out
    assert '"label": "FC note"' in source_output
    assert '"private_note": true' in source_output

    assert lm.main(["watch", "source", "remove", "1", "--db", str(db_path)]) == 0
    assert "Removed source." in capsys.readouterr().out


def test_watch_source_cli_mute_unmute_preserves_source_row(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://t.pia.jp/example", "Pia")

    assert lm.main(["watch", "source", "mute", "1", "--db", str(db_path)]) == 0
    assert "Muted source." in capsys.readouterr().out
    assert lm.list_watch_sources(str(db_path)) == []
    muted_sources = lm.list_watch_sources(str(db_path), include_muted=True)
    assert muted_sources[0].muted is True

    assert lm.main(["watch", "source", "unmute", "1", "--db", str(db_path)]) == 0
    assert "Unmuted source." in capsys.readouterr().out
    assert lm.list_watch_sources(str(db_path))[0].muted is False


def test_private_note_sources_are_not_scraped(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://fan.example/private", "FC private", private_note=True)
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    def fail_fetch(url):
        raise AssertionError(f"private source should not be fetched: {url}")

    monkeypatch.setattr(lm, "fetch_page", fail_fetch)

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert any(link.url == "https://fan.example/private" for link in blocks.general_info.ticket_links)


def test_public_manual_source_adds_ticket_round(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://l-tike.com/example", "Lawson", private_note=False)
    monkeypatch.setattr(
        lm,
        "build_blocks",
        lambda keyword: lm.AppBlocks(
            general_info=example_blocks(keyword).general_info,
            ticket_info=(),
        ),
    )
    html = """
    <html><body>
      <h2>第1次抽選先行</h2>
      <p>受付期間 2026年6月10日 ～ 2026年6月18日</p>
    </body></html>
    """
    monkeypatch.setattr(lm, "fetch_page", lambda url: lm.parse_page(url, html))

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert blocks.ticket_info[0].platform == "lawson"
    assert blocks.ticket_info[0].application_end_at == "2026-06-18"


def test_calendar_export_includes_tracked_event_ticket_dates(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=example_blocks("Example").general_info,
        ticket_info=(
            lm.TicketRound(
                source="pia",
                url="https://t.pia.jp/example",
                name="First lottery",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
                evidence="受付期間 2026年6月3日 ～ 2026年6月5日",
                results_date="2026-06-08",
                general_sale_date="2026-06-20",
                payment_deadline="2026-06-10",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")

    calendar = lm.render_calendar_ics(str(db_path), generated_at=dt.datetime(2026, 6, 3, tzinfo=dt.UTC))

    assert "BEGIN:VCALENDAR" in calendar
    assert "DTSTAMP:20260603T000000Z" in calendar
    assert "SUMMARY:Lottery application: Example Tour - First lottery" in calendar
    assert "DTSTART;VALUE=DATE:20260603" in calendar
    assert "DTEND;VALUE=DATE:20260606" in calendar
    assert "SUMMARY:Lottery results: Example Tour - First lottery" in calendar
    assert "SUMMARY:Payment due: Example Tour - First lottery" in calendar
    assert "SUMMARY:General sale: Example Tour - First lottery" in calendar
    assert "URL:https://t.pia.jp/example" in calendar


def test_web_server_serves_home_and_api_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.save_blocks(str(db_path), example_blocks("Example"), now="2026-06-03T00:00:00+00:00")
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        home = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        health = json_load_url(f"{base}/api/health")
        watchlist = json_load_url(f"{base}/api/watchlist")
        sources = json_load_url(f"{base}/api/sources")
        active_sources = json_load_url(f"{base}/api/sources?include_muted=0")
        events = json_load_url(f"{base}/api/events")
        upcoming = json_load_url(f"{base}/api/upcoming")
        alerts = json_load_url(f"{base}/api/alerts")
        calendar_response = urllib.request.urlopen(f"{base}/calendar.ics", timeout=5)
        calendar = calendar_response.read().decode("utf-8")

        assert "chusennote" in home
        assert "Calendar feed" in home
        assert "Tracked Artists" in home
        assert "Tracked Events" in home
        assert "Muted Watches" in home
        assert "Needs Attention" in home
        assert "Dates:" in home
        assert "Venues:" in home
        assert "Evidence:" in home
        assert "Example Tour" in detail
        assert health["status"] == "ok"
        assert health["tracked_events"] >= 1
        assert watchlist[0]["keyword"] == "Example"
        assert sources == []
        assert active_sources == []
        assert events[0]["title"] == "Example Tour"
        assert "match_reasons" in events[0]
        assert "evidence" in events[0]["rounds"][0]
        assert upcoming[0]["event_title"] == "Example Tour"
        assert alerts
        assert "text/calendar" in calendar_response.headers["Content-Type"]
        assert "BEGIN:VCALENDAR" in calendar
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_command_parses_explicit_host():
    args = lm.parse_args(["web", "--db", "local.sqlite3", "--port", "0", "--host", "0.0.0.0"])

    assert args.command == "web"
    assert args.db == "local.sqlite3"
    assert args.port == 0
    assert args.host == "0.0.0.0"


def test_web_server_add_remove_and_run_actions(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        post_form(f"{base}/api/watchlist", {"keyword": "Example"})
        assert json_load_url(f"{base}/api/watchlist")[0]["keyword"] == "Example"

        source = post_form(f"{base}/api/sources", {"watch": "Example", "url": "https://fan.example/private", "label": "FC", "private_note": "1"})
        assert source["private_note"] is True

        run_alerts = post_form(f"{base}/api/run", {})
        assert any(alert["type"] == "new_lottery_round" for alert in run_alerts)

        removed_source = post_form(f"{base}/api/sources/remove", {"identifier": "1"})
        assert removed_source["removed"] is True

        removed = post_form(f"{base}/api/watchlist/remove", {"identifier": "Example"})
        assert removed["removed"] is True
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is True

        unmuted = post_form(f"{base}/api/watchlist/unmute", {"identifier": "Example"})
        assert unmuted["unmuted"] is True
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is False

        muted = post_form(f"{base}/api/watchlist/mute", {"identifier": "Example"})
        assert muted["muted"] is True
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is True

        restored_home = post_text(f"{base}/watch/unmute", {"identifier": "Example"})
        assert "Muted Watches" in restored_home
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)


def json_load_url(url):
    return json.loads(urllib.request.urlopen(url, timeout=5).read().decode("utf-8"))


def post_form(url, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))


def post_text(url, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    return urllib.request.urlopen(request, timeout=5).read().decode("utf-8")
