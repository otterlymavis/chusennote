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
    db_path = tmp_path / "otterpia.sqlite3"
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
    db_path = tmp_path / "otterpia.sqlite3"
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
    db_path = tmp_path / "otterpia.sqlite3"
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
    db_path = tmp_path / "otterpia.sqlite3"
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
