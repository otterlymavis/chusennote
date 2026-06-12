# chusennote

A keyword-first assistant for tracking Japanese concert and musical ticket lotteries.

chusennote is split into two local tracking lanes:

- **Tracked artists**: follows artist/performer/company keywords and stores basic event discovery info such as title, official page, date clues, venue clues, status, and source confidence.
- **Tracked events**: follows a specific concert, stage show, musical, or event and stores ticket links, manual sources, lottery rounds, application windows, result dates, payment deadlines, general sale dates, and alert history.

Saved event records include extracted date and venue clues in SQLite and expose them through API/export output as `event_dates` and `venues`.

Instead of asking you to hand-maintain every ticket URL first, chusennote starts from the workflow you described:

1. You enter an artist/event/musical keyword.
2. The app searches for likely official pages.
3. It reads the official page for event details and ticket links.
4. It generates two blocks:
   - **General event info**: title, official page, date clues, venue clues, summary.
   - **Ticket / lottery info**: ticket links plus detected lottery rounds, start/end dates, result dates, general sale dates, and payment deadlines.

## Quick start

```bash
python3 lottery_monitor.py "your event keyword"
```

The same search is available through the explicit command form:

```bash
python3 lottery_monitor.py search "your event keyword"
```

JSON output for a future web/app UI:

```bash
python3 lottery_monitor.py search "your event keyword" --json
```

Save each run to SQLite so chusennote can detect changes over time:

```bash
python3 lottery_monitor.py search "your event keyword" --db chusennote.sqlite3
```

Output only alert changes for automation:

```bash
python3 lottery_monitor.py "your event keyword" --db chusennote.sqlite3 --alerts-json
```

Alert output includes newly discovered facts plus date-based lifecycle events such as `lottery_opened`, `lottery_closing_soon`, `results_today`, `payment_due_soon`, and `general_sale_soon`. Lifecycle alerts are recorded in SQLite so the same alert is not repeated on every run. If one watch fails during a batch run, chusennote emits `watch_failed` for that keyword and continues checking the rest of the watchlist.

Add keywords to the persistent watchlist and run all active watches:

```bash
python3 lottery_monitor.py artist add "artist keyword"
python3 lottery_monitor.py artist run
python3 lottery_monitor.py event add "specific event keyword"
python3 lottery_monitor.py event run --alerts-json
python3 lottery_monitor.py event mute "specific event keyword"
python3 lottery_monitor.py event unmute "specific event keyword"
```

The older `watch add/list/run` commands still work as compatibility aliases for tracked events. `remove` is a soft mute; use `mute` and `unmute` when you want that state change to be explicit. Use `--include-muted` on list commands to show muted rows in text or JSON output.

Run tracked events repeatedly in the foreground:

```bash
python3 lottery_monitor.py watch loop --db chusennote.sqlite3 --interval-minutes 60
```

On Windows:

```powershell
.\scripts\start-chusennote-monitor.ps1 -IntervalMinutes 60
```

Or install a recurring Windows Task Scheduler job that runs one saved-check pass per interval:

```powershell
.\scripts\install-chusennote-monitor-task.ps1 -IntervalMinutes 60
.\scripts\show-chusennote-monitor-task.ps1
.\scripts\uninstall-chusennote-monitor-task.ps1
```

The scheduled task defaults to tracked events and runs `python lottery_monitor.py event run --db chusennote.sqlite3` from this repository. Use `-Kind artist`, `-Db`, `-TaskName`, or `-Python` when you need a different lane, database path, task name, or Python executable.

Limit alert noise with preferences and venue/region filters:

```bash
python3 lottery_monitor.py event add "specific event keyword" --venues "Tokyo Garden Theater" --alerts "new_lottery_round,lottery_closing_soon,payment_due_soon"
```

Attach manual source URLs to a watch. Public sources are fetched during `watch run`; private notes are stored and shown, but not scraped:

```bash
python3 lottery_monitor.py watch source add "your event keyword" "https://ticket.example/show" --label "Ticket page"
python3 lottery_monitor.py watch source add "your event keyword" "https://fc.example/private" --label "Fan club note" --private-note
python3 lottery_monitor.py watch source list "your event keyword" --include-muted
```

Run the local web UI:

```bash
python3 lottery_monitor.py web --db chusennote.sqlite3 --port 8765
```

Then open <http://127.0.0.1:8765>.

The web UI can set and show watch tags, preferred regions, preferred venues, and event alert filters. It also shows muted watches and muted sources in separate restore sections, so removing a tracked artist, event, or manual source is reversible without using the CLI.

On Windows, the helper script starts the same server and can open the browser:

```powershell
.\scripts\start-chusennote.ps1 -Open
```

For testing from a physical phone on the same Wi-Fi, bind to your LAN interface:

```powershell
.\scripts\start-chusennote.ps1 -Lan
```

Then use one of the printed `LAN URL` values in the mobile app. You can smoke-test a running server with:

```powershell
.\scripts\check-chusennote.ps1
```

The smoke check verifies the home page, `/api/health`, watchlist, events, upcoming rows, alerts, sources, and calendar feed.

Native mobile client source is available in [`android/`](android/) and [`ios/`](ios/). See [`MOBILE.md`](MOBILE.md) for setup notes.

Export local data for other tools:

```bash
python3 lottery_monitor.py export events --db chusennote.sqlite3
python3 lottery_monitor.py export alerts --db chusennote.sqlite3
python3 lottery_monitor.py export artists --db chusennote.sqlite3
python3 lottery_monitor.py export tracked-events --db chusennote.sqlite3
python3 lottery_monitor.py export sources --db chusennote.sqlite3
python3 lottery_monitor.py export upcoming --db chusennote.sqlite3
python3 lottery_monitor.py export calendar --db chusennote.sqlite3 > chusennote.ics
```

Use `--include-muted` with `export events`, `export artists`, `export tracked-events`, `export sources`, `export upcoming`, or `export calendar` when you want muted watches, muted watch event history, muted embedded sources, muted manual sources, or muted watch ticket dates included.

The local web server also exposes the ticket timeline as an iCalendar feed at <http://127.0.0.1:8765/calendar.ics>. The feed contains tracked-event ticket dates such as lottery application windows, results dates, payment deadlines, and general sale dates.

Saved events include `match_reasons` explaining why chusennote kept them, ticket-round `evidence` snippets for public-page verification, and `export upcoming` / `/api/upcoming` show the highest-priority ticket dates first. Alert export/API rows include `alert_id`, `event_id`, event title, watch id, watch keyword, watch kind, and watch muted state for local linking. Watch and manual source `remove` commands are soft mutes; use `event unmute ID_OR_KEYWORD`, `artist unmute ID_OR_KEYWORD`, or `watch source unmute ID_OR_URL` to restore them. API watch/source lists return active rows by default; pass `include_muted=1` to `/api/watchlist` or `/api/sources` to include muted rows and sources attached to muted watches. `/api/events?include_muted=1`, `/api/upcoming?include_muted=1`, and `/calendar.ics?include_muted=1` also include muted watch event history; `/api/events?include_muted=1` includes muted embedded manual sources too. Direct web event detail links still resolve saved muted-watch events for alert and history review.

## How the current pipeline works

### 1. Search for the official page

`lottery_monitor.py` searches the web with a Japanese ticket-oriented query:

```text
<keyword> 公式 チケット 抽選 先行
```

It scores results higher when they look official (`公式`, `official`, `オフィシャル`, `公演`, known official hosts, `.co.jp`) and lower when they are social/noisy pages or ticket portal pages. Relevance is measured with character-bigram overlap so Japanese keywords (which have no word spaces) rank correctly. The top official-looking pages are fetched first.

#### Search backend (recommended)

By default the app scrapes DuckDuckGo/Bing HTML. Those endpoints aggressively bot-throttle and often return irrelevant results, so for reliable discovery configure a managed search API via two environment variables:

| Variable | Values |
| --- | --- |
| `CHUSENNOTE_SEARCH_PROVIDER` | `brave`, `bing`, or `serpapi` |
| `CHUSENNOTE_SEARCH_API_KEY` | the API key for that provider |

When set, the API is queried first and HTML scraping is used only as a fallback. Example (Brave, PowerShell — persists for your user account):

```powershell
[Environment]::SetEnvironmentVariable('CHUSENNOTE_SEARCH_PROVIDER','brave','User')
[Environment]::SetEnvironmentVariable('CHUSENNOTE_SEARCH_API_KEY','<your-brave-key>','User')
```

Get a Brave key at <https://api-dashboard.search.brave.com/>. Without a key the app still runs; discovery just relies on the (throttled) HTML fallback.

If discovery is unreliable for a specific title, attach the official URL manually instead (`watch source add`) and it will be scraped for lottery rounds directly.

### 2. Build the General event info block

From the official pages, the app extracts:

- page title / Open Graph title
- official page URL
- nearby text around event-date labels like `公演日`, `日程`, `開催日時`
- nearby text around venue labels like `会場`, `劇場`, `ホール`, `アリーナ`
- ticket links found on the official page

If no ticket links are found, the app adds fallback search links for Pia, eplus, and Lawson Ticket.

### 3. Build the Ticket / lottery info block

From each ticket link, the app looks for Japanese ticketing phrases such as:

- `第1次抽選先行`
- `先行`
- `プレオーダー`
- `受付期間`
- `申込期間`
- `抽選結果`, `結果発表`, `当落`
- `一般発売`
- `入金`, `支払`, `払込`

It then extracts nearby dates in formats like:

- `2026/06/10`
- `2026-06-10`
- `2026年6月10日`
- `6月10日` (year inferred from the current date)

## Example output shape

```markdown
# General event info

- Keyword: Example Musical
- Title: Example Musical Official
- Official page: https://official.example/stage
- Event date clues:
  - 公演日 2026年7月10日 会場 Example Hall

# Ticket / lottery info

- Ticket links found:
  - チケット抽選先行はこちら: https://eplus.jp/example-musical/
- Lottery / sales rounds:
  - 第1次抽選先行 (eplus)
    - Lottery start: 2026-06-10
    - Lottery end: 2026-06-18
    - Results date: 2026-06-22
```


## Product research

See [`docs/competitive_analysis.md`](docs/competitive_analysis.md) for an analysis of Songkick/Bandsintown patterns and a Japan-specific implementation roadmap for chusennote.

## Notes and limitations

- Japanese ticket sites often change HTML, use dynamic rendering, and hide details behind JavaScript or login gates.
- This version uses only the Python standard library, so it is easy to run anywhere, but site-specific parsers may be needed for high precision.
- SQLite persistence stores watched keywords, events, ticket sources, detected ticket rounds, compact JSON snapshots, and emitted lifecycle alerts.
- Saved events include lifecycle statuses like `watching`, `official_found`, `ticket_links_found`, `lottery_found`, and `lottery_open`.
- Ticket rounds include platform confidence, round type, membership-required metadata, and compact evidence snippets when chusennote can infer them.
- Saved events include local match reasons, and the web/API/mobile views include a “Needs Attention” ticket-date list.
- Per-watch alert preferences and venue/region filters keep batch monitoring quieter.
- The local web UI is intentionally standard-library only and runs on your machine.
- Windows helper scripts can run checks in the foreground or register a local Task Scheduler job; neither mode installs a hosted worker or external notification service.
- The best long-term approach is to keep this keyword-first pipeline and keep deepening dedicated public-page adapters for Pia, eplus, Lawson Ticket, Rakuten Ticket, Ticket Board, CN Playguide, official sites, and musical production sites.
- The MVP does not send Discord, LINE, Slack, or email notifications and does not scrape private/login-only fan-club pages; use private manual sources for those notes.

## Future upgrades

- Add Discord/LINE/Slack notifications for newly opened or closing lotteries.
- Deepen source-specific parsers with more site-specific evidence snippets and edge-case date labels.
- Support multiple rounds explicitly (`1st lottery`, `2nd lottery`, `official presale`, `general sale`).
- Add optional OS service helpers for users who want chusennote to run outside their login session.
