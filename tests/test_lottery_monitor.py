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
    assert "Example Hall" in info.venues[0]


def test_extract_ticket_links_ignores_social_share_and_info_urls():
    html = """
    <html><body>
      <a href="http://twitter.com/share?text=チケット">Xで投稿する</a>
      <a href="http://line.me/R/msg/text/?チケット">LINEで送る</a>
      <a href="https://www.shiki.jp/applause/lionking/ticket_schedule/">チケット＆スケジュール</a>
    </body></html>
    """
    page = lm.parse_page("https://www.shiki.jp/applause/lionking/", html)

    links = lm.extract_ticket_links(page)

    assert links == ()


def test_extract_ticket_links_ignores_ticket_related_info_pages():
    html = """
    <html><body>
      <a href="https://horipro-stage.jp/stage/example/#schedule">Tickets &amp; Schedule</a>
      <a href="https://www.nissay-plus.co.jp/horipro-ticket-hoken?ch=abc">Ticket insurance</a>
      <a href="https://w.pia.jp/t/example/">Pia lottery</a>
      <a href="https://ticket.tv-asahi.co.jp/ex/project/example">TV Asahi ticket</a>
    </body></html>
    """
    page = lm.parse_page("https://horipro-stage.jp/stage/example/", html)

    links = lm.extract_ticket_links(page)

    assert [link.url for link in links] == [
        "https://w.pia.jp/t/example/",
        "https://ticket.tv-asahi.co.jp/ex/project/example",
    ]


def test_event_status_ignores_non_actionable_ticket_info_links():
    info = lm.EventInfo(
        keyword="Example",
        official_page="https://official.example/stage",
        title="Example",
        summary="",
        event_dates=(),
        venues=(),
        ticket_links=(lm.Link("Tickets & Schedule", "https://horipro-stage.jp/stage/example/#schedule"),),
    )

    assert lm.compute_event_status(info, ()) == "official_found"


def test_search_api_warns_when_configured_backend_fails(monkeypatch, capsys):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "brave")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "secret-key")

    def fail_request_json(url, headers=None):
        raise OSError("bad credentials")

    monkeypatch.setattr(lm, "request_json", fail_request_json)

    assert lm.search_api("Example") == []

    warning = capsys.readouterr().err
    assert "Warning:" in warning
    assert "CHUSENNOTE_SEARCH_PROVIDER='brave' failed" in warning
    assert "secret-key" not in warning


def test_search_api_warns_for_unsupported_provider(monkeypatch, capsys):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "unknown")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "secret-key")

    assert lm.search_api("Example") == []

    warning = capsys.readouterr().err
    assert "unsupported CHUSENNOTE_SEARCH_PROVIDER='unknown'" in warning
    assert "secret-key" not in warning


def test_event_dates_and_venues_ignore_ticket_sales_noise():
    text = """
    SCHEDULE & TICKETS スケジュール＆チケット 群馬公演 【一般前売開始】 2025年4月5日(土)
    ※車椅子席、介助席のご購入は、高崎芸術劇場チケットセンター（027-321-3900）まで電話でお問合せください。（6月10日追記）
    【料金】 S席 17,500円 A席 11,000円
    東宝ナビザーブ 先行抽選エントリー 3月18日(火)～3月21日(金)まで
    会場のご案内 高崎芸術劇場 大劇場 〒370-7302 群馬県高崎市栄町9-1 MAP 座席表
    """

    assert lm.extract_event_dates(text) == ()
    assert lm.extract_venues(text)[0] == "高崎芸術劇場 大劇場"


def test_extract_venues_stops_at_organizer_and_contact_noise():
    text = (
        "出演（柿澤勇人、石井一孝） 会場 梅田芸術劇場メインホール 主催 梅田芸術劇場 "
        "お問い合わせ 梅田芸術劇場 0570-077-0 ぜひ劇場でお楽しみください"
    )

    assert lm.extract_venues(text) == ("梅田芸術劇場メインホール",)


def test_extract_venues_handles_spaced_label_and_suffixless_venue():
    text = "出演（柿澤勇人） 会 場 EXシアター有明(東京ドリームパーク内) 座席表 上演時間 3時間"

    assert lm.extract_venues(text) == ("EXシアター有明(東京ドリームパーク内)",)


def test_extract_event_dates_captures_performance_period_not_ticketing():
    text = (
        "ミュージカル『例』 期 間 2026年7月25日(土)～8月23日(日) 会 場 例ホール "
        "【抽選先行】 受付 期間 2026年1月24日(土)～2月1日(日)"
    )

    dates = lm.extract_event_dates(text)

    assert "2026年7月25日(土)～8月23日(日)" in dates
    assert all("2026年1月24日" not in date for date in dates)


def test_extract_event_dates_captures_shiki_slash_performance_periods():
    text = "劇団四季自動予約 2026/1/2～2026/6/30 公演 No. 3016 2026/7/1～2026/12/31 公演 No. 6118"

    assert lm.extract_event_dates(text) == ("2026/1/2～2026/6/30", "2026/7/1～2026/12/31")


def test_extract_venues_prefers_concise_shiki_theater_name():
    text = "アラジン 東京 電通四季劇場［海］（汐留） 選択 北海道 青森 劇場アクセス 作品紹介"

    assert lm.extract_venues(text)[0] == "電通四季劇場［海］（汐留）"


def test_extract_venues_ignores_shiki_no_schedule_notice():
    text = (
        "チケット購入はできません。 ＞「有明四季劇場」交通アクセス・駐車場のご案内 "
        "現在、公演スケジュール情報はありません。 公演一覧はこちら Facebookでシェアする LINEで送る"
    )

    assert lm.extract_venues(text) == ()


def test_infer_event_location_prefers_parenthetical_area():
    assert lm.infer_event_location(("Venue Example Hall (Tokyo)",)) == "Tokyo"


def test_ticket_rule_and_price_extractors_read_summary_notes():
    summary = (
        "Schedule ※未就学児のご入場はご遠慮ください。"
        "※本公演のチケットは主催者の同意のない有償譲渡が禁止されています。"
        "【料金】 S席 17,500円 A席 11,000円"
    )

    assert "未就学児" in lm.extract_ticket_rule_items(summary)[0]
    assert "有償譲渡" in lm.extract_ticket_rule_items(summary)[1]
    assert lm.extract_ticket_price_items(summary) == ("S席 17,500円 A席 11,000円",)


def test_ticket_price_extractor_splits_structured_seat_tiers():
    summary = "チケット S席：平日14,000円／土日祝15,000円 A席：平日9,000円／土日祝10,000円 Yシート（20歳以下当日引換券）：2,000円＊ U-25（25歳以下当日引換券）：5,500円"

    assert lm.extract_ticket_price_items(summary) == (
        "S席：平日14,000円／土日祝15,000円",
        "A席：平日9,000円／土日祝10,000円",
        "Yシート（20歳以下当日引換券）：2,000円",
        "U-25（25歳以下当日引換券）：5,500円",
    )


def test_ticket_rule_extractor_merges_continuations_and_skips_notice_links():
    summary = (
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください。"
        "なお、車椅子スペースをご利用の場合は、S席をご購入ください。"
        "※【重要なお知らせ】高額転売チケットに関する注意喚起 ＞＞"
    )

    assert lm.extract_ticket_rule_items(summary) == (
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください なお、車椅子スペースをご利用の場合は、S席をご購入ください",
    )


def test_ticket_rule_extractor_dedupes_contained_notes_and_skips_cookie_consent():
    summary = (
        "※車椅子でご来場のお客様は、ご観劇日の1週間前までにホリプロチケットセンターまでご連絡ください。"
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください。"
        "なお、車椅子スペースをご利用の場合は、S席をご購入ください。"
        "※車椅子スペースをご利用のお客様は、空き状況をお問い合わせください なお、車椅子スペースをご利用の場合は、S席をご購入ください。"
        "サイトを閲覧いただく際には、クッキーの使用に同意いただく必要があります。"
    )

    rules = lm.extract_ticket_rule_items(summary)

    assert len(rules) == 2
    assert sum("車椅子スペース" in rule for rule in rules) == 1
    assert all("クッキー" not in rule for rule in rules)


def test_format_evidence_snippet_removes_notice_links_and_truncates():
    evidence = "noise before label ※【重要なお知らせ】高額転売チケットに関する注意喚起 ＞＞ 【抽選先行】 2026年1月24日(土)12:00～2月1日(日)23:59 https://example.com/source " + ("details " * 40)

    snippet = lm.format_evidence_snippet(evidence, limit=80)

    assert "重要なお知らせ" not in snippet
    assert "https://" not in snippet
    assert snippet.startswith("【抽選先行】")
    assert snippet.endswith("...")
    assert len(snippet) <= 83


def test_build_event_info_summary_keeps_ticket_price_notes():
    page = lm.parse_page(
        "https://official.example/stage",
        """
        <html><head><title>Example Stage</title></head><body>
          <p>公演日 2026年7月10日 会場 Example Hall</p>
          <p>チケット S席：平日14,000円／土日祝15,000円 A席：9,000円</p>
          <p>※未就学児のご入場はご遠慮ください。</p>
        </body></html>
        """,
    )

    info = lm.build_event_info("Example", [page])

    assert "14,000円" in (info.summary or "")
    assert "未就学児" in (info.summary or "")


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


def test_extract_ticket_rounds_reads_shiki_dates_before_labels():
    text = (
        "東京公演はこちら 1月10日（火）～8月26日（土）長期保守点検 "
        "8月27日（日）～12月31日（日）公演分 "
        "2月19日（日）「四季の会」会員先行予約／2月26日（日）一般発売開始"
    )
    page = lm.Page("https://www.shiki.jp/applause/aladdin/ticket_schedule/", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert rounds[0].lottery_start == "2026-02-19"
    assert rounds[0].lottery_end is None
    assert rounds[0].general_sale_date == "2026-02-26"


def test_past_general_sale_round_is_closed():
    ticket = lm.TicketRound(source="official", url="https://example.test", name="一般発売", general_sale_date="2026-02-26")

    assert lm.compute_ticket_status(ticket, today=dt.date(2026, 6, 13)) == "closed"


def test_ticket_round_key_distinguishes_same_name_rounds_by_dates():
    first = lm.TicketRound(source="official", url="https://example.test", name="抽選", lottery_start="2026-01-24")
    second = lm.TicketRound(source="official", url="https://example.test", name="抽選", lottery_start="2026-03-24")

    assert lm.ticket_round_key(first) != lm.ticket_round_key(second)


def test_extract_ticket_rounds_reads_toho_advance_sale_labels():
    text = (
        "東宝ナビザーブ 先行抽選エントリー 3月18日(火)～3月21日(金)まで "
        "先行先着販売 3月30日(日)11:00より販売開始 "
        "一般前売 4月5日(土) 11:00販売開始"
    )
    page = lm.Page("https://www.tohostage.com/lesmiserables/ticket_gunma.html", "Ticket", text, ())

    rounds = lm.extract_ticket_rounds(page)

    assert any(round_.lottery_start == "2026-03-18" and round_.lottery_end == "2026-03-21" for round_ in rounds)
    assert any(round_.lottery_start == "2026-03-30" for round_ in rounds)
    assert any(round_.general_sale_date == "2026-04-05" for round_ in rounds)


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


def test_render_event_card_does_not_link_keyword_fallback_url():
    rendered = lm.render_event_card(
        {
            "id": 1,
            "title": "Example Tour",
            "status": "watching",
            "official_url": "keyword:Example Tour",
            "updated_at": "2026-06-04T00:00:00+00:00",
            "rounds": [],
        }
    )

    assert 'href="keyword:Example Tour"' not in rendered
    assert "Official page unavailable" in rendered


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


def test_save_blocks_removes_stale_keyword_fallback_after_official_page(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    fallback = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="",
            title="Example",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(lm.Link("Fallback ticket", "https://t.pia.jp/example"),),
        ),
        ticket_info=(
            lm.TicketRound(
                source="keyword",
                url="keyword:Example",
                name="Manual reminder",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
            ),
        ),
    )
    official = example_blocks("Example")

    lm.save_blocks(str(db_path), fallback, now="2026-06-03T00:00:00+00:00")
    lm.save_blocks(str(db_path), official, now="2026-06-04T00:00:00+00:00")

    with sqlite3.connect(db_path) as connection:
        events = connection.execute("SELECT id, official_url FROM events ORDER BY id").fetchall()
        fallback_children = connection.execute(
            """
            SELECT COUNT(*) FROM ticket_rounds
            WHERE url = 'keyword:Example'
            """
        ).fetchone()[0]

    assert [row[1] for row in events] == ["https://official.example/"]
    assert fallback_children == 0


def test_db_cleanup_cli_removes_stale_fallbacks_and_orphans(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    official = example_blocks("Example")

    lm.save_blocks(str(db_path), official, now="2026-06-04T00:00:00+00:00")
    with sqlite3.connect(db_path) as connection:
        watch_id = connection.execute("SELECT id FROM watched_keywords WHERE keyword = 'Example'").fetchone()[0]
        connection.execute(
            """
            INSERT INTO events(watch_id, canonical_title, official_url, summary, event_dates_json, venues_json, status, event_key, created_at, updated_at)
            VALUES (?, 'Example fallback', 'keyword:Example', '', '[]', '[]', 'watching', 'fallback', '2026-06-03', '2026-06-03')
            """,
            (watch_id,),
        )
        connection.execute(
            """
            INSERT INTO ticket_rounds(event_id, round_key, source, url, name, confidence, status, round_type, membership_required, created_at, updated_at)
            VALUES (999, 'orphan', 'manual', 'keyword:Orphan', 'Orphan', 50, 'unknown', 'unknown', 'unknown', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (999, 'https://orphan.example', 'Orphan', 'orphan.example', 40, 'low_confidence', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (1, 'https://official.example/news/ticket-info', 'Important ticket notice', 'official.example', 60, 'manual_public', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO sources(event_id, url, label, platform, confidence, provenance, created_at, updated_at)
            VALUES (2, 'https://eplus.jp/example', 'eplus', 'eplus', 90, 'ticket_primary', '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO snapshots(event_id, snapshot_hash, payload_json, created_at)
            VALUES (999, 'orphan', '{}', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO alert_log(event_id, alert_key, alert_type, payload_json, created_at)
            VALUES (999, 'orphan', 'orphan', '{}', '2026-06-03')
            """
        )
        connection.execute(
            """
            INSERT INTO watch_sources(watch_id, url, label, platform, confidence, private_note, muted, created_at, updated_at)
            VALUES (999, 'https://orphan.example', 'Orphan', 'orphan.example', 40, 0, 0, '2026-06-03', '2026-06-03')
            """
        )
        connection.execute(
            """
            UPDATE events
            SET venues_json = '["現在、公演スケジュール情報はありません。 公演一覧はこちら"]'
            WHERE official_url = 'https://official.example/'
            """
        )

    assert lm.main(["db", "cleanup", "--db", str(db_path), "--json"]) == 0

    output = capsys.readouterr().out
    counts = json.loads(output)
    assert counts["keyword_fallback_events"] == 1
    assert counts["ticket_rounds"] == 1
    assert counts["sources"] == 2
    assert counts["snapshots"] == 1
    assert counts["alert_log"] == 1
    assert counts["watch_sources"] == 1
    assert counts["event_venues"] == 1
    with sqlite3.connect(db_path) as connection:
        event_urls = [row[0] for row in connection.execute("SELECT official_url FROM events ORDER BY id")]
        orphan_rounds = connection.execute("SELECT COUNT(*) FROM ticket_rounds WHERE event_id = 999").fetchone()[0]
        merged_source = connection.execute("SELECT event_id FROM sources WHERE url = 'https://eplus.jp/example'").fetchone()[0]
        venues_json = connection.execute("SELECT venues_json FROM events WHERE official_url = 'https://official.example/'").fetchone()[0]

    assert event_urls == ["https://official.example/"]
    assert orphan_rounds == 0
    assert merged_source == 1
    assert json.loads(venues_json) == []


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
    recent_alerts = lm.recent_alerts(str(db_path))

    assert "lottery_opened" in alert_types
    assert "lottery_closing_soon" in alert_types
    assert "results_today" in alert_types
    assert "general_sale_soon" in alert_types
    assert "payment_due_soon" in alert_types
    assert recent_alerts[0]["alert_id"] >= 1
    assert recent_alerts[0]["event_id"] >= 1
    assert recent_alerts[0]["alert_type"] == recent_alerts[0]["type"]
    assert recent_alerts[0]["event_title"] == "Example Tour"
    assert recent_alerts[0]["watch_id"] >= 1
    assert recent_alerts[0]["watch_keyword"] == "Example"
    assert recent_alerts[0]["watch_kind"] == lm.WATCH_KIND_EVENT
    assert recent_alerts[0]["watch_muted"] is False


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


def test_session_log_flag_writes_daily_markdown_log(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "history_logs"
    monkeypatch.setattr(lm, "build_blocks", lambda keyword: example_blocks(keyword))

    assert lm.main(["search", "Example", "--json", "--session-log", "--session-log-dir", str(log_dir)]) == 0
    capsys.readouterr()

    logs = list(log_dir.glob("session_*.md"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "lottery_monitor.py search Example --json --session-log --session-log-dir" in content
    assert "- Target: `search`" in content
    assert "- Exit code: `0`" in content


def test_session_log_args_work_before_legacy_keyword():
    args = lm.parse_args(["--session-log", "--session-log-dir=logs", "Example"])

    assert args.command == "legacy"
    assert args.keyword == "Example"
    assert args.session_log is True
    assert args.session_log_dir == "logs"


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

    assert lm.main(["watch", "list", "--db", str(db_path), "--include-muted"]) == 0
    muted_list_output = capsys.readouterr().out
    assert "Example [muted]" in muted_list_output

    assert lm.main(["export", "tracked-events", "--db", str(db_path), "--include-muted"]) == 0
    export_output = capsys.readouterr().out
    assert '"keyword": "Example"' in export_output
    assert '"muted": true' in export_output


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
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))

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

    monkeypatch.setattr(lm.pipeline, "build_blocks_for_watch", fake_build)

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
    monkeypatch.setattr(lm.pipeline, "build_artist_blocks", lambda keyword: lm.AppBlocks(example_blocks(keyword).general_info, ()))
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))

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
    lm.add_watch_source(str(db_path), "Example", "https://fan.example/private", "FC", private_note=True)
    assert lm.remove_watch_source(str(db_path), "https://fan.example/private") is True

    assert lm.main(["export", "events", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out

    assert '"title": "Example Tour"' in output
    assert '"status": "lottery_open"' in output
    assert '"event_dates": [' in output
    assert '"match_reasons": [' in output
    assert '"manual_sources": []' in output

    assert lm.main(["export", "events", "--db", str(db_path), "--include-muted"]) == 0
    muted_output = capsys.readouterr().out
    assert '"label": "FC"' in muted_output
    assert '"muted": true' in muted_output

    assert lm.remove_watch(str(db_path), "Example") is True
    assert lm.main(["export", "events", "--db", str(db_path)]) == 0
    muted_watch_output = capsys.readouterr().out
    assert muted_watch_output.strip() == "[]"

    assert lm.main(["export", "events", "--db", str(db_path), "--include-muted"]) == 0
    included_muted_watch_output = capsys.readouterr().out
    assert '"title": "Example Tour"' in included_muted_watch_output


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

    assert lm.remove_watch(str(db_path), "Closing") is True
    assert lm.remove_watch(str(db_path), "Payment") is True
    assert lm.upcoming_priority_rows(str(db_path)) == []

    muted_rows = lm.upcoming_priority_rows(str(db_path), include_muted_watches=True)
    assert muted_rows[0]["event_title"] == "Closing Tour"

    assert lm.main(["export", "upcoming", "--db", str(db_path)]) == 0
    muted_output = capsys.readouterr().out
    assert muted_output.strip() == "[]"

    assert lm.main(["export", "upcoming", "--db", str(db_path), "--include-muted"]) == 0
    included_muted_output = capsys.readouterr().out
    assert '"event_title": "Closing Tour"' in included_muted_output


def test_upcoming_priority_rows_excludes_closed_rounds(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.save_blocks(
        str(db_path),
        lm.AppBlocks(
            general_info=lm.EventInfo(
                keyword="Closed",
                official_page="https://official.example/closed",
                title="Closed Tour",
                summary="",
                event_dates=(),
                venues=(),
                ticket_links=(),
            ),
            ticket_info=(
                lm.TicketRound(
                    source="official",
                    url="https://official.example/closed",
                    name="Closed lottery",
                    lottery_start="2026-01-24",
                    lottery_end="2026-02-01",
                ),
            ),
        ),
        now="2026-06-14T00:00:00+00:00",
    )

    assert lm.upcoming_priority_rows(str(db_path)) == []


def test_web_needs_attention_does_not_link_non_web_source_urls(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    blocks = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example",
            official_page="https://official.example/",
            title="Example Tour",
            summary="",
            event_dates=(),
            venues=(),
            ticket_links=(),
        ),
        ticket_info=(
            lm.TicketRound(
                source="manual",
                url="keyword:Example",
                name="Manual reminder",
                lottery_start="2026-06-03",
                lottery_end="2026-06-05",
            ),
        ),
    )
    lm.save_blocks(str(db_path), blocks, now="2026-06-03T00:00:00+00:00")

    home = lm.render_web_page(str(db_path))

    assert 'href="keyword:Example"' not in home
    assert "Tracked Artists" in home
    assert "Tracked Events" in home
    assert "Source unavailable" not in home


def test_tracked_event_display_key_prioritizes_official_pages():
    fallback_watch = lm.Watch(
        id=1,
        keyword="Fallback",
        kind=lm.WATCH_KIND_EVENT,
    )
    official_watch = lm.Watch(
        id=2,
        keyword="Official",
        kind=lm.WATCH_KIND_EVENT,
    )
    fallback_event = {"official_url": "keyword:Fallback", "ticket_links": [{}, {}, {}], "rounds": [], "event_dates": []}
    official_event = {"official_url": "https://official.example/", "ticket_links": [{}], "rounds": [], "event_dates": []}

    ordered = sorted(
        (fallback_watch, official_watch),
        key=lambda watch: lm.tracked_event_display_key(watch, fallback_event if watch.id == 1 else official_event),
    )

    assert [watch.keyword for watch in ordered] == ["Official", "Fallback"]


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

    assert lm.remove_watch(str(db_path), "Event") is True
    muted_health = lm.api_health(str(db_path))
    assert muted_health["manual_sources"] == 0


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

    assert lm.main(["watch", "source", "list", "Example", "--db", str(db_path), "--include-muted"]) == 0
    assert "FC note [muted]" in capsys.readouterr().out


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


def test_export_sources_respects_include_muted(tmp_path, capsys):
    db_path = tmp_path / "chusennote.sqlite3"
    lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://t.pia.jp/example", "Pia")
    lm.add_watch_source(str(db_path), "Example", "https://fan.example/private", "FC private", private_note=True)
    assert lm.remove_watch_source(str(db_path), "https://fan.example/private") is True

    assert lm.main(["export", "sources", "--db", str(db_path)]) == 0
    output = capsys.readouterr().out
    assert '"label": "Pia"' in output
    assert "FC private" not in output

    assert lm.main(["export", "sources", "--db", str(db_path), "--include-muted"]) == 0
    muted_output = capsys.readouterr().out
    assert '"label": "FC private"' in muted_output
    assert '"muted": true' in muted_output

    assert lm.remove_watch(str(db_path), "Example") is True
    assert lm.main(["export", "sources", "--db", str(db_path)]) == 0
    muted_watch_output = capsys.readouterr().out
    assert muted_watch_output.strip() == "[]"

    assert lm.main(["export", "sources", "--db", str(db_path), "--include-muted"]) == 0
    included_muted_watch_output = capsys.readouterr().out
    assert '"label": "Pia"' in included_muted_watch_output


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


def test_public_manual_source_is_authoritative_and_skips_discovery(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    watch = lm.add_watch(str(db_path), "Example", now="2026-06-01T00:00:00+00:00")
    lm.add_watch_source(str(db_path), "Example", "https://official.example/stage", "公式", private_note=False)

    def fail_discovery(keyword):
        raise AssertionError("web discovery must be skipped when a public manual source exists")

    monkeypatch.setattr(lm.pipeline, "build_blocks", fail_discovery)
    html = """
    <html><head><title>Example Official</title></head><body>
      <h1>Example</h1>
      <p>公演日 2026年7月10日 会場 Example Hall</p>
      <h2>第1次抽選先行</h2>
      <p>受付期間 2026年6月10日 ～ 2026年6月18日</p>
    </body></html>
    """
    monkeypatch.setattr(lm.pipeline, "fetch_page", lambda url: lm.parse_page(url, html))

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert blocks.general_info.official_page == "https://official.example/stage"
    assert blocks.general_info.title == "Example Official"
    assert any(round.lottery_end == "2026-06-18" for round in blocks.ticket_info)


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
    monkeypatch.setattr(lm.pipeline, "fetch_page", lambda url: lm.parse_page(url, html))

    blocks = lm.build_blocks_for_watch(str(db_path), watch)

    assert blocks.ticket_info[0].platform == "lawson"
    assert blocks.ticket_info[0].application_end_at == "2026-06-18"


def test_calendar_export_includes_tracked_event_ticket_dates(tmp_path, capsys):
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

    assert lm.remove_watch(str(db_path), "Example") is True
    active_calendar = lm.render_calendar_ics(str(db_path), generated_at=dt.datetime(2026, 6, 3, tzinfo=dt.UTC))
    muted_calendar = lm.render_calendar_ics(
        str(db_path),
        generated_at=dt.datetime(2026, 6, 3, tzinfo=dt.UTC),
        include_muted_watches=True,
    )
    assert "SUMMARY:Lottery application: Example Tour - First lottery" not in active_calendar
    assert "SUMMARY:Lottery application: Example Tour - First lottery" in muted_calendar

    assert lm.main(["export", "calendar", "--db", str(db_path)]) == 0
    cli_active_calendar = capsys.readouterr().out
    assert "Example Tour - First lottery" not in cli_active_calendar

    assert lm.main(["export", "calendar", "--db", str(db_path), "--include-muted"]) == 0
    cli_muted_calendar = capsys.readouterr().out
    assert "Example Tour - First lottery" in cli_muted_calendar


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
        muted_calendar = urllib.request.urlopen(f"{base}/calendar.ics?include_muted=1", timeout=5).read().decode("utf-8")

        assert "chusennote" in home
        assert "Tracked Artists" in home
        assert "Tracked Events" in home
        assert 'role="tablist"' in home
        assert 'data-tab-target="attention"' in home
        assert 'data-tab-target="artists"' in home
        assert 'data-tab-target="events"' in home
        assert "Search exact event" in home
        assert "Search events" in home
        assert 'href="/events/1"' in home
        assert "Calendar feed" not in home
        assert "Muted Watches" not in home
        assert "Muted Sources" not in home
        assert "Needs Attention" in home
        assert "Rounds 1" in home
        assert "Example Tour" in detail
        assert "General Info" in detail
        assert "Location" in detail
        assert "Time" in detail
        assert "Venue" in detail
        assert "Ticket Rules" in detail
        assert "Ticket Price" in detail
        assert "Ticket Links" in detail
        assert "Lottery Rounds" in detail
        assert "Evidence:" in detail
        assert "event Example" not in home
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
        assert alerts[0]["alert_id"] >= 1
        assert alerts[0]["event_id"] >= 1
        assert alerts[0]["event_title"] == "Example Tour"
        assert alerts[0]["watch_keyword"] == "Example"
        assert "text/calendar" in calendar_response.headers["Content-Type"]
        assert "BEGIN:VCALENDAR" in calendar
        assert "Example Tour" in muted_calendar
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_web_command_parses_explicit_host():
    args = lm.parse_args(["web", "--db", "local.sqlite3", "--port", "0", "--host", "0.0.0.0"])

    assert args.command == "web"
    assert args.db == "local.sqlite3"
    assert args.port == 0
    assert args.host == "0.0.0.0"


def test_web_event_search_adds_exact_event_with_detail_link(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(
        lm.web,
        "search_web",
        lambda keyword, limit=8: (
            lm.SearchResult("Example Musical Official", "https://official.example/stage", "official event page"),
        ),
    )
    monkeypatch.setattr(
        lm.web,
        "build_exact_event_blocks",
        lambda keyword, title, url, snippet="": example_blocks("Example Musical"),
    )
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        search_home = post_text(f"{base}/event/search", {"keyword": "Example Musical"})
        assert "Example Musical Official" in search_home
        assert "https://official.example/stage" in search_home
        assert 'title="Add exact event"' in search_home

        added_home = post_text(
            f"{base}/event/add",
            {
                "keyword": "Example Musical",
                "title": "Example Musical Official",
                "url": "https://official.example/stage",
                "snippet": "official event page",
            },
        )
        assert "Example Musical" in added_home
        assert 'href="/events/1"' in added_home
        assert "Tickets 1" in added_home
        assert "Rounds 1" in added_home
        detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert "Official page" in detail
        assert "https://t.pia.jp/example" in detail
        assert "第1次抽選先行" in detail
        assert "Lottery opens" in detail
        assert "Lottery closes" in detail
        assert "Payment due" in detail
        assert "On sale" in detail
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_artist_detail_lists_discovered_events_sorted_by_date(tmp_path):
    db_path = tmp_path / "chusennote.sqlite3"
    artist = lm.add_watch(str(db_path), "Example Artist", kind=lm.WATCH_KIND_ARTIST, now="2026-06-01T00:00:00+00:00")
    later = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/later",
            title="Later Show",
            summary="",
            event_dates=("公演日 2026年9月20日",),
            venues=("会場 Later Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    earlier = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/earlier",
            title="Earlier Show",
            summary="",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 Earlier Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    lm.save_blocks(str(db_path), later, now="2026-06-02T00:00:00+00:00")
    lm.save_blocks(str(db_path), earlier, now="2026-06-03T00:00:00+00:00")

    home = lm.render_web_page(str(db_path))
    detail = lm.render_artist_detail_page(str(db_path), artist.id)

    assert f'href="/artists/{artist.id}"' in home
    assert detail.index("Earlier Show") < detail.index("Later Show")
    assert "Date 2026-07-10" in detail
    assert "Date 2026-09-20" in detail
    assert "Venue 会場 Earlier Hall" in detail
    assert "Tickets 0" in detail
    assert "Rounds 0" in detail


def test_artist_run_saves_multiple_discovered_events_under_artist_watch(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    artist = lm.add_watch(str(db_path), "Example Artist", kind=lm.WATCH_KIND_ARTIST, now="2026-06-01T00:00:00+00:00")
    first = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/first",
            title="First Artist Event",
            summary="",
            event_dates=("公演日 2026年7月10日",),
            venues=("会場 First Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    second = lm.AppBlocks(
        general_info=lm.EventInfo(
            keyword="Example Artist",
            official_page="https://official.example/second",
            title="Second Artist Event",
            summary="",
            event_dates=("公演日 2026年8月10日",),
            venues=("会場 Second Hall",),
            ticket_links=(),
        ),
        ticket_info=(),
    )
    monkeypatch.setattr(lm.pipeline, "build_artist_event_blocks", lambda keyword: [second, first])

    lm.run_watches(str(db_path), now="2026-06-02T00:00:00+00:00", kind=lm.WATCH_KIND_ARTIST)

    events = lm.recent_events(str(db_path), include_muted_sources=True, include_muted_watches=True)
    artist_events = [event for event in events if event["watch_id"] == artist.id]
    detail = lm.render_artist_detail_page(str(db_path), artist.id)
    assert len(artist_events) == 2
    assert detail.index("First Artist Event") < detail.index("Second Artist Event")


def test_artist_event_blocks_fall_back_to_ticket_portal_searches(monkeypatch):
    monkeypatch.setattr(
        lm,
        "search_web",
        lambda keyword, limit=8: [lm.SearchResult("Unrelated article", "https://example.com/noise", "libido sodomie")],
    )
    monkeypatch.setattr(
        lm,
        "fetch_page",
        lambda url: lm.Page(url=url, title="Unrelated article", text="libido sodomie menopause", links=()),
    )

    blocks = lm.build_artist_event_blocks("yoasobi")

    assert len(blocks) == 1
    assert blocks[0].general_info.title == "yoasobi ticket search"
    assert [link.label for link in blocks[0].general_info.ticket_links] == [
        "Pia search",
        "eplus search",
        "Lawson Ticket search",
    ]


def test_web_server_add_remove_and_run_actions(tmp_path, monkeypatch):
    db_path = tmp_path / "chusennote.sqlite3"
    monkeypatch.setattr(lm.pipeline, "build_blocks", lambda keyword: example_blocks(keyword))
    server = lm.create_web_server(str(db_path), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        created_watch = post_form(
            f"{base}/api/watchlist",
            {
                "keyword": "Example",
                "tags": "musical",
                "regions": "",
                "venues": "Example Hall",
                "alerts": "new_lottery_round",
            },
        )
        assert created_watch["keyword"] == "Example"
        assert created_watch["tags"] == "musical"
        assert created_watch["preferred_regions"] == ""
        assert created_watch["preferred_venues"] == "Example Hall"
        assert created_watch["alert_preferences"] == "new_lottery_round"
        assert json_load_url(f"{base}/api/watchlist")[0]["keyword"] == "Example"
        home_with_preferences = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        assert "Example" in home_with_preferences
        assert "not searched yet" in home_with_preferences

        source = post_form(f"{base}/api/sources", {"watch": "Example", "url": "https://fan.example/private", "label": "FC", "private_note": "1"})
        assert source["private_note"] is True

        run_alerts = post_form(f"{base}/api/run", {})
        assert any(alert["type"] == "new_lottery_round" for alert in run_alerts)
        home_with_source = urllib.request.urlopen(f"{base}/", timeout=5).read().decode("utf-8")
        detail_with_source = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert '<a href="https://fan.example/private">Open</a>' not in home_with_source
        assert '<a class="action-link" href="https://fan.example/private">Open</a>' in detail_with_source

        removed_source = post_form(f"{base}/api/sources/remove", {"identifier": "1"})
        assert removed_source["removed"] is True
        source_list = json_load_url(f"{base}/api/sources")
        muted_source_list = json_load_url(f"{base}/api/sources?include_muted=1")
        events_without_muted_sources = json_load_url(f"{base}/api/events")
        events_with_muted_sources = json_load_url(f"{base}/api/events?include_muted=1")
        assert source_list == []
        assert muted_source_list[0]["muted"] is True
        assert events_without_muted_sources[0]["manual_sources"] == []
        assert events_with_muted_sources[0]["manual_sources"][0]["muted"] is True

        restored_source_home = post_text(f"{base}/source/unmute", {"identifier": "1"})
        assert "Tracked Artists" in restored_source_home
        assert "Tracked Events" in restored_source_home
        assert "Muted Sources" not in restored_source_home
        assert json_load_url(f"{base}/api/sources")[0]["muted"] is False

        removed = post_form(f"{base}/api/watchlist/remove", {"identifier": "Example"})
        assert removed["removed"] is True
        assert json_load_url(f"{base}/api/watchlist") == []
        assert json_load_url(f"{base}/api/watchlist?include_muted=1")[0]["muted"] is True

        unmuted = post_form(f"{base}/api/watchlist/unmute", {"identifier": "Example"})
        assert unmuted["unmuted"] is True
        assert json_load_url(f"{base}/api/watchlist")[0]["muted"] is False

        muted = post_form(f"{base}/api/watchlist/mute", {"identifier": "Example"})
        assert muted["muted"] is True
        assert json_load_url(f"{base}/api/watchlist") == []
        assert json_load_url(f"{base}/api/watchlist?include_muted=1")[0]["muted"] is True
        assert json_load_url(f"{base}/api/sources") == []
        assert json_load_url(f"{base}/api/sources?include_muted=1")[0]["label"] == "FC"
        assert json_load_url(f"{base}/api/events") == []
        assert json_load_url(f"{base}/api/events?include_muted=1")[0]["title"] == "Example Tour"
        assert json_load_url(f"{base}/api/upcoming") == []
        assert json_load_url(f"{base}/api/upcoming?include_muted=1") == []
        muted_detail = urllib.request.urlopen(f"{base}/events/1", timeout=5).read().decode("utf-8")
        assert "Example Tour" in muted_detail
        assert '<a class="action-link" href="https://fan.example/private">Open</a>' in muted_detail

        restored_home = post_text(f"{base}/watch/unmute", {"identifier": "Example"})
        assert "Tracked Artists" in restored_home
        assert "Tracked Events" in restored_home
        assert "Muted Watches" not in restored_home
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


def test_official_score_ranks_cjk_official_above_noise():
    keyword = "ミュージカル『ディア・エヴァン・ハンセン』"
    official = lm.SearchResult(
        "ミュージカル『ディア・エヴァン・ハンセン』公式サイト",
        "https://dearevanhansen.jp/",
        "公演情報・チケット抽選先行受付",
    )
    noise = lm.SearchResult("Stars : toute l'actu - Gala", "https://www.gala.fr/", "people")
    gmail = lm.SearchResult("Вход в Gmail", "https://support.google.com/mail", "help")

    assert lm.official_score(official, keyword) > lm.official_score(noise, keyword)
    assert lm.official_score(noise, keyword) == 0
    chosen = lm.choose_official_results([noise, gmail, official], keyword, limit=1)
    assert chosen[0].url == "https://dearevanhansen.jp/"


def test_choose_official_results_drops_unrelated_zero_score_results():
    keyword = "帝国劇場"
    results = [
        lm.SearchResult("Pompes Funèbres Ruffieux & Fils Monuments", "https://pfruffieux.ch/", ""),
        lm.SearchResult("CPU-Z | Softwares | CPUID", "https://www.cpuid.com/softwares/cpu-z.html", ""),
    ]

    assert lm.choose_official_results(results, keyword, limit=3) == []


def test_choose_official_results_requires_keyword_relevance_for_generic_hints():
    keyword = "帝国劇場"
    results = [
        lm.SearchResult("News & Politics - Odysee", "https://odysee.com/$/news", ""),
        lm.SearchResult("Live & TV - ZDF", "https://www.zdf.de/live-tv", ""),
        lm.SearchResult("Official support page", "https://support.microsoft.com/en-us", ""),
        lm.SearchResult("Get started with Google Maps", "https://support.google.com/maps/answer/144349", ""),
    ]

    assert lm.choose_official_results(results, keyword, limit=3) == []


def test_choose_official_results_ignores_incidental_low_overlap():
    result = lm.SearchResult(
        "AWS inaugura Gen AI Loft em São Paulo para impulsionar startups",
        "https://itforum.com.br/noticias/aws-inaugura-gen-ai-loft-em-sao-paulo/",
        "",
    )

    assert lm.choose_official_results([result], "YOASOBI ライブ 東京", limit=3) == []


def test_build_blocks_does_not_fetch_unrelated_zero_score_search_results(monkeypatch):
    keyword = "帝国劇場"
    results = [lm.SearchResult("Blender Italia", "https://www.blender.it/", "")]

    def fail_fetch(url):
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(lm, "fetch_page", fail_fetch)

    blocks = lm.build_blocks(keyword, search_results=results)

    assert blocks.general_info.official_page is None
    assert [link.label for link in blocks.general_info.ticket_links] == [
        "Pia search",
        "eplus search",
        "Lawson Ticket search",
    ]
    assert blocks.ticket_info == ()


def test_build_blocks_rejects_fetched_page_that_does_not_match_keyword(monkeypatch):
    keyword = "YOASOBI ライブ 東京"
    results = [
        lm.SearchResult(
            "YOASOBI official live result",
            "https://health.example/blood-pressure",
            "YOASOBI ライブ 東京",
        )
    ]

    monkeypatch.setattr(
        lm,
        "fetch_page",
        lambda url: lm.Page(
            url=url,
            title="What is Normal Blood Pressure by Age and Gender?",
            text="Health advice, diet, exercise, medical history, and appointments.",
            links=(),
        ),
    )

    blocks = lm.build_blocks(keyword, search_results=results)

    assert blocks.general_info.official_page is None
    assert blocks.ticket_info == ()


def test_keyword_overlap_is_high_for_matching_japanese_and_low_for_unrelated():
    keyword = "ディア・エヴァン・ハンセン"
    assert lm.keyword_overlap(keyword, "ディア・エヴァン・ハンセン 公演") > 0.8
    assert lm.keyword_overlap(keyword, "toute l'actu des stars Gala") == 0.0


def test_keyword_matches_latin_artist_by_name_not_incidental_bigrams():
    assert lm.keyword_matches_text("yoasobi", "YOASOBI official live schedule")
    assert not lm.keyword_matches_text("yoasobi", "Sexualité: libido, sodomie, ménopause")


def test_search_api_disabled_without_env(monkeypatch):
    monkeypatch.delenv(lm.SEARCH_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(lm.SEARCH_API_KEY_ENV, raising=False)
    assert lm.search_api("any keyword") == []


def test_search_api_parses_brave_payload(monkeypatch):
    monkeypatch.setenv(lm.SEARCH_PROVIDER_ENV, "brave")
    monkeypatch.setenv(lm.SEARCH_API_KEY_ENV, "test-key")
    captured = {}

    def fake_request_json(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return {
            "web": {
                "results": [
                    {
                        "title": "公式サイト",
                        "url": "https://official.example/stage",
                        "description": "公演 チケット 抽選",
                    },
                    {"title": "no url"},
                ]
            }
        }

    monkeypatch.setattr(lm.search, "request_json", fake_request_json)
    results = lm.search_api("ディア・エヴァン・ハンセン", limit=5)

    assert "api.search.brave.com" in captured["url"]
    assert captured["headers"]["X-Subscription-Token"] == "test-key"
    assert [r.url for r in results] == ["https://official.example/stage"]
    assert results[0].title == "公式サイト"


def test_search_web_prefers_api_results_over_scraping(monkeypatch):
    monkeypatch.setattr(
        lm.search,
        "search_api",
        lambda keyword, limit=8: [lm.SearchResult("api hit", "https://api.example/", "")],
    )

    def fail_scrape(*args, **kwargs):
        raise AssertionError("HTML scraping should not run when the API returns results")

    monkeypatch.setattr(lm.search, "request_html", fail_scrape)
    results = lm.search_web("any keyword")

    assert [r.url for r in results] == ["https://api.example/"]
