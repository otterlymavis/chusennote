import json
import sqlite3
import threading
import urllib.parse
import urllib.request

import lottery_monitor as lm


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
            event_dates=(),
            venues=(),
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
        round_columns = lm.table_columns(connection, "ticket_rounds")
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert {"tags", "preferred_regions", "preferred_venues", "muted", "last_checked_at"} <= watched_columns
    assert {"platform", "application_start_at", "application_end_at", "confidence", "status"} <= round_columns
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


def example_blocks(keyword="Example"):
    return lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword=keyword,
            official_page="https://official.example/",
            title=f"{keyword} Tour",
            summary="公演情報",
            event_dates=(),
            venues=(),
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


def test_watch_run_cli_outputs_alerts_json(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["watch", "run", "--db", str(db_path), "--alerts-json"]) == 0
    output = capsys.readouterr().out

    assert '"type": "new_official_page"' in output
    assert '"type": "new_lottery_round"' in output


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
        watchlist = json_load_url(f"{base}/api/watchlist")
        events = json_load_url(f"{base}/api/events")
        alerts = json_load_url(f"{base}/api/alerts")

        assert "chusennote" in home
        assert watchlist[0]["keyword"] == "Example"
        assert events[0]["title"] == "Example Tour"
        assert alerts
    finally:
        server.shutdown()
        thread.join(timeout=5)


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

        run_alerts = post_form(f"{base}/api/run", {})
        assert any(alert["type"] == "new_lottery_round" for alert in run_alerts)

        removed = post_form(f"{base}/api/watchlist/remove", {"identifier": "Example"})
        assert removed["removed"] is True
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)


def json_load_url(url):
    return json.loads(urllib.request.urlopen(url, timeout=5).read().decode("utf-8"))


def post_form(url, values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))
