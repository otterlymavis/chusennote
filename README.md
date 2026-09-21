# chusennote

A keyword-first assistant for tracking Japanese concert and musical ticket lotteries.

chusennote is split into two local tracking lanes:

- **Tracked artists**: follows artist/performer/company keywords and stores basic event discovery info such as title, official page, date clues, venue clues, status, and source confidence.
- **Tracked events**: follows a specific concert, stage show, musical, or event and stores ticket links, manual sources, lottery rounds, application windows, result dates, payment deadlines, general sale dates, official resale/trade windows, and alert history.

Saved event records include extracted date, venue, organizer, and cast/lineup clues in SQLite and expose them through API/export output as `event_dates`, `venues`, `organizers`, and `lineup`.

Instead of asking you to hand-maintain every ticket URL first, chusennote starts from the workflow you described:

1. You enter an artist/event/musical keyword.
2. The app searches for likely official pages.
3. It reads the official page for event details and ticket links.
   Declared same-site RSS, Atom, and sitemap links are followed with strict
   page and manifest limits so ticket announcements can be found without
   turning discovery into an unbounded crawler.
4. It generates two blocks:
   - **General event info**: title, official page, date clues, venue clues, explicitly labeled organizers and cast/lineup, summary.
   - **Ticket / lottery info**: ticket links plus detected lottery and official-resale rounds, start/end dates, result dates, general sale dates, and payment deadlines.

## Quick start

```bash
python3 lottery_monitor.py "your event keyword"
```

The same search is available through the explicit command form:

```bash
python3 lottery_monitor.py search "your event keyword"
```

Inspect the backend release identity with `python3 lottery_monitor.py --version`.
Opt-in session logs redact PostgreSQL connection URLs, mobile push tokens, and
credential- or query-bearing URLs before writing command metadata.

Machine-readable JSON output for integrations and the included clients:

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

Append a lightweight command record to `history_logs/session_YYYY_MM_DD.md`:

```bash
python3 lottery_monitor.py search "your event keyword" --session-log
```

Use `--session-log-dir PATH` when you want logs somewhere other than `history_logs/`.

Alert output includes newly discovered facts plus date-based lifecycle events such as `lottery_opened`, `lottery_closing_soon`, `results_today`, `payment_due_soon`, `general_sale_soon`, `trade_opened`, and `trade_closing_soon`. Standalone resale/trade windows—including `リセール期間`, `リセール受付期間`, `リセール申込期間`, `定価リセール受付期間`, and official-trade variants—become resale rounds even when a page has no lottery heading. Lifecycle alerts are recorded in SQLite so the same alert is not repeated on every run. If one watch fails during a batch run, chusennote emits `watch_failed` for that keyword and continues checking the rest of the watchlist.

Discovery and ticket-change alerts are also retained in alert history, so the
CLI, web UI, and native clients show the same changes that a monitor run
reported. Saving unchanged state does not create another alert.

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

The scheduled task defaults to tracked events and runs `python lottery_monitor.py event run --db chusennote.sqlite3` from this repository. Use `-Kind artist`, `-Database`, `-TaskName`, or `-Python` when you need a different lane, database path, task name, or Python executable.

On macOS, install a per-user LaunchAgent that runs without an open terminal:

```bash
./scripts/install-chusennote-launchd.sh --interval-minutes 60 --kind event --env-file .env
./scripts/show-chusennote-launchd.sh
./scripts/uninstall-chusennote-launchd.sh
```

On Linux with systemd, install the equivalent per-user timer:

```bash
./scripts/install-chusennote-systemd.sh --interval-minutes 60 --kind event --env-file .env
./scripts/show-chusennote-systemd.sh
./scripts/uninstall-chusennote-systemd.sh
```

Both installers support `--dry-run` and validate their inputs before writing
user service files. They do not require root. The macOS LaunchAgent runs while
that user is logged in. For a Linux user timer that must continue after logout,
an administrator can enable systemd lingering for that user. Service logs stay
in the systemd journal on Linux and `~/Library/Logs` on macOS. Environment files
are parsed as data rather than executed by a shell.

Limit alert noise with preferences and venue/region filters:

```bash
python3 lottery_monitor.py event add "specific event keyword" --venues "Tokyo Garden Theater" --alerts "new_lottery_round,lottery_closing_soon,payment_due_soon"
```

Attach manual source URLs to a watch. Public sources must be credential-free public HTTP(S) URLs and are fetched during `watch run`; private notes are bounded free text that is stored and shown, but never scraped:

```bash
python3 lottery_monitor.py watch source add "your event keyword" "https://ticket.example/show" --label "Ticket page"
python3 lottery_monitor.py watch source add "your event keyword" "https://fc.example/private" --label "Fan club note" --private-note
python3 lottery_monitor.py watch source list "your event keyword" --include-muted
```

Run the local web UI:

```bash
python3 lottery_monitor.py web --db chusennote.sqlite3 --port 8877
```

Then open <http://127.0.0.1:8877>.

The web UI can register or sign in to an account, then keeps dashboard reads and
form actions scoped to that account with an HttpOnly, SameSite-strict session
cookie. It can also set and show watch tags, preferred regions, preferred
venues, and event alert filters. Re-enter an existing keyword in the watch
editor to update those preferences; accounts sharing the same canonical
keyword keep independent filters and alert choices. Muted watches and muted sources appear in
separate restore sections, so removing a tracked artist, event, or manual
source is reversible without using the CLI.

Browser passwords are accepted only on HTTPS, localhost, or a literal private
network IP. If the server is behind a trusted TLS reverse proxy, set
`CHUSENNOTE_TRUST_PROXY_HEADERS=1` and have the proxy replace (not append to)
`X-Forwarded-Proto`. Never expose the standard-library server directly on a
public cleartext connection.
All web, API, calendar, and redirect responses disable caching and framing,
prevent MIME sniffing, restrict browser capabilities and referrers, and carry a
content security policy. HSTS is emitted only for directly trusted HTTPS proxy
requests.
URL-encoded POST bodies are capped at 64 KiB and 100 fields; malformed lengths,
unsupported transfer encoding, and invalid UTF-8 are rejected before form
processing.
GET queries are capped at 100 fields. Notification feeds accept limits from 1
through 500, and managed event searches accept 1 through 20, preventing
unbounded reads or provider request amplification.
Watch keywords are whitespace-normalized, limited to 200 characters, and must
use the supported artist or event kind at the shared persistence boundary.
An absent bearer token continues to select the local anonymous workspace, but
any supplied malformed, expired, or revoked authorization is rejected with 401
instead of silently reading or mutating that workspace.

Deployment environment names are collected in [`.env.example`](.env.example).
The application does not automatically load that file: export the values from
your service manager, or copy it to the ignored `.env` filename and pass it to
Docker with `--env-file`. Run
`python3 lottery_monitor.py notify status --db PATH` after configuring
notification subscriptions; it verifies ADC/project
availability, device coverage, SMTP, and optional chat destinations without
printing secrets.

Before a release, inspect every integration without contacting providers or
printing credential values:

```bash
python3 lottery_monitor.py preflight
python3 lottery_monitor.py preflight --json
python3 lottery_monitor.py preflight --production
python3 lottery_monitor.py preflight \
  --require database --require discovery --require fcm-backend \
  --require ios-firebase --require ios-signing \
  --require android-firebase --require android-signing
```

The default report is informational because SMTP and chat destinations are
optional. `--production` requires the database, managed discovery, backend and
mobile Firebase configuration, and both native signing configurations in one
canonical configuration gate; it does not claim deployed-service or
physical-device acceptance. JSON output deliberately keeps
`production_configuration_ready` separate from `release_ready`. Repeated
`--require` flags can instead select individual components. The checks validate the PostgreSQL URL shape, Firebase client bundle and
package identities, backend/mobile Firebase project and sender alignment, iOS
team/production-push configuration, shared
backend/iOS/Android version and build metadata, the
complete release-file inventory, and the structure of an explicit ADC credential
file—not just file existence. A deployment that uses ambient workload identity must
still be verified in that runtime with `notify status` and a real device
delivery.

Build and run the backend image with a persistent SQLite volume like this:

```bash
docker build -t chusennote .
docker run --rm --init --ipc=host -p 8877:8877 \
  --env-file .env -v chusennote-data:/data chusennote
```

The image runs as a non-root user, exposes an `/api/health` container health
check, and includes the Playwright Python package matched to its bundled
Chromium version. Public-source HTTP redirects to obvious local/private targets
are rejected, and HTTP/JSON/browser response bodies are capped at 5 MiB. Health output identifies the database backend without
exposing PostgreSQL credentials or absolute SQLite paths. Local `.env` files, databases, credentials, native apps,
tests, and build outputs are excluded from its build context.

For the included Render Blueprint, connect this repository and select
`render.yaml`. Supply a Neon PostgreSQL connection string for
`CHUSENNOTE_DATABASE_URL`, the Firebase project id, and the Tavily Search key in
Render's secret environment prompts. Upload the Firebase service-account JSON
as a Render secret file named `firebase-service-account.json`; the Blueprint
already points Application Default Credentials at its mounted path. Render
provides `PORT`, which the server and container health check use automatically.
The Blueprint also sets `CHUSENNOTE_REQUIRE_POSTGRES=1`, so a missing or
non-PostgreSQL database setting prevents startup instead of silently using
ephemeral SQLite. After deployment, run `smoke --require-postgres` against the
public HTTPS URL.
The Blueprint intentionally does not provision Render PostgreSQL or a
persistent disk, so application state remains in Neon when the free web service
sleeps or is replaced.

The free web service does not run scheduled checks while asleep. The separate
`Hosted monitor` GitHub Actions workflow runs saved watches and reminders once
per hour against the same Neon database. Before publishing the workflow, set
these repository secrets: `CHUSENNOTE_DATABASE_URL` (the same pooled Neon URL),
`CHUSENNOTE_SEARCH_API_KEY` (Tavily), `CHUSENNOTE_FIREBASE_PROJECT_ID`, and
`FIREBASE_SERVICE_ACCOUNT_JSON` (the complete Firebase service-account JSON).
The workflow writes the JSON only to its temporary runner, runs with read-only
repository permissions, and logs counts rather than watch terms or tokens.
Scheduled Actions may be delayed or disabled by GitHub, so this is not a
guaranteed minute-accurate reminder service; check workflow runs and delivery
on real devices before relying on it. The scheduled workflow must be on the
repository's default branch, and GitHub disables schedules in public
repositories after 60 days without repository activity. It is prepared here
but does not start until the work is published and the secrets are configured.

For a single-user local workspace, reminders can also be delivered through a
Slack incoming webhook, Discord incoming webhook, or LINE Messaging API push:

```bash
python3 lottery_monitor.py notify subscribe "your event keyword" --scope event_all --channels feed,slack
python3 lottery_monitor.py notify status --db chusennote.sqlite3
```

Configure the matching values in `.env.example`. Slack and Discord destinations
must be official HTTPS webhook URLs; redirects and lookalike hosts are rejected.
LINE uses its fixed push endpoint with a deterministic retry key. These global
destinations are deliberately limited to anonymous/local subscriptions so one
hosted account cannot send another account's reminders to a shared webhook.
The local web UI exposes channel and lead-day controls under **Notifications →
Edit delivery**; signed-in accounts intentionally see only feed and push.
Provider setup references: [Slack incoming webhooks](https://api.slack.com/messaging/webhooks),
[Discord webhooks](https://docs.discord.com/developers/resources/webhook), and
[LINE push messages](https://developers.line.biz/en/reference/messaging-api/#send-push-message).

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

The same read-only acceptance check is available on every supported desktop:

```bash
python3 lottery_monitor.py smoke --base-url http://127.0.0.1:8877
```

For a hosted PostgreSQL deployment, add `--require-postgres` (or
`-RequirePostgres` with the PowerShell helper). This rejects a healthy-looking
server that has silently fallen back to SQLite.

The PowerShell helper delegates to this same checker, so both commands validate
the identical endpoint and security contract.

It verifies the home, privacy, and support pages; release- and schema-aware
health; watchlist; events; upcoming rows; alerts; notification feed;
subscriptions; devices; sources; and calendar feed. Redirects and responses
larger than 1 MB are rejected. For an
authenticated deployment, provide the bearer token through the temporary
`CHUSENNOTE_SMOKE_API_TOKEN` environment variable; the command never includes
that value in its output. Token-authenticated public smoke checks require HTTPS;
cleartext HTTP remains available only for localhost and literal private-network
addresses.

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

The local web server also exposes the ticket timeline as an iCalendar feed at <http://127.0.0.1:8877/calendar.ics>. The feed contains tracked-event ticket dates such as lottery application windows, results dates, payment deadlines, and general sale dates.

Saved events include `match_reasons` explaining why chusennote kept them, ticket-round `evidence` snippets for public-page verification, and both machine-readable `status` plus user-facing `status_label` values. `export upcoming` / `/api/upcoming` show the highest-priority ticket dates first. Alert export/API rows retain stable `type` identifiers, add user-facing `type_label`, and include `alert_id`, `event_id`, event title, watch id, watch keyword, watch kind, and watch muted state for local linking. Watch and manual source `remove` commands are soft mutes; use `event unmute ID_OR_KEYWORD`, `artist unmute ID_OR_KEYWORD`, or `watch source unmute ID_OR_URL` to restore them. API watch/source lists return active rows by default; pass `include_muted=1` to `/api/watchlist` or `/api/sources` to include muted rows and sources attached to muted watches. `/api/events?include_muted=1`, `/api/upcoming?include_muted=1`, and `/calendar.ics?include_muted=1` also include muted watch event history; `/api/events?include_muted=1` includes muted embedded manual sources too. Direct web event detail links still resolve saved muted-watch events for alert and history review.

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
| `CHUSENNOTE_SEARCH_PROVIDER` | `tavily`, `brave`, `bing`, or `serpapi` |
| `CHUSENNOTE_SEARCH_API_KEY` | the API key for that provider |

When set, the API is queried first and HTML scraping is used only as a fallback. Tavily is the recommended free default because its Researcher plan currently includes 1,000 monthly credits without requiring a credit card. Example (Tavily, PowerShell — persists for your user account):

```powershell
[Environment]::SetEnvironmentVariable('CHUSENNOTE_SEARCH_PROVIDER','tavily','User')
[Environment]::SetEnvironmentVariable('CHUSENNOTE_SEARCH_API_KEY','<your-tavily-key>','User')
```

Get a Tavily key at <https://app.tavily.com/>. Without a key the app still runs; discovery just relies on the (throttled) HTML fallback. Brave remains supported, but its monthly credits require card verification.

If discovery is unreliable for a specific title, attach its credential-free public HTTP(S) official URL manually instead (`watch source add`) and it will be scraped for lottery rounds directly. Exact-event URLs submitted through the web or API follow the same rule.

### 2. Build the General event info block

From the official pages, the app extracts:

- page title / Open Graph title
- Schema.org Event JSON-LD when an official page publishes it
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
- Pia `いち早プレリザーブ`, `申込受付期間`, and `お支払い期限`
- eplus `プレオーダー（抽選）申込期間` and `抽選結果確認期間`
- Lawson `プレリク先行`, `抽選結果発表日時`, and `店頭入金期間`
- Rakuten Ticket `抽選先行受付`, `受付期間`, and `結果発表日時`
- Ticket Board `先行抽選受付`, `申込期間`, and `当選発表日`
- CN Playguide numbered/official `先行抽選予約` rounds and `抽選予約受付期間`
- Shiki membership `「四季の会」会員先行予約` and date-before-label sale schedules
- Horipro Stage `最速抽選先行` / `最終抽選先行` rounds and result dates
- Toho Stage `東宝ナビザーブ 先行抽選エントリー`, result, and payment labels
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
- Tracking, parsing, SQLite, and the web UI use the Python standard library. The declared `google-auth[requests]` runtime dependency is used only for Firebase HTTP-v1 authentication; site-specific parsers may still be needed for high precision.
- SQLite persistence stores watched keywords, events, ticket sources, detected ticket rounds, compact JSON snapshots, and emitted lifecycle alerts.
- Saved events include lifecycle statuses like `watching`, `official_found`, `ticket_links_found`, `lottery_found`, and `lottery_open`.
- Ticket rounds include platform confidence, round type, membership-required metadata, and compact evidence snippets when chusennote can infer them.
- General event records retain explicitly labeled organizer and cast/lineup facts from public pages; unlabeled names in prose are not guessed into those fields.
- Official-page adapters consume valid Schema.org Event JSON-LD for canonical title, description, dates, venue, organizer, performer, and actionable ticket-offer links, with visible page text as the fallback; malformed or oversized blocks are ignored.
- Saved events include local match reasons, and the web/API/mobile views include a “Needs Attention” ticket-date list.
- Reliable exact-event records can recommend related saved events when they
  share an organizer, performer, venue, or tracked keyword. Recommendations
  are explainable, account-scoped, and never trigger speculative web searches.
- Per-watch alert preferences and venue/region filters keep batch monitoring quieter.
- The local web UI is intentionally standard-library only and runs on your machine.
- Windows Task Scheduler, macOS LaunchAgent, and Linux systemd helpers can run recurring local checks; none installs a hosted worker or creates provider resources.
- The best long-term approach is to keep this keyword-first pipeline and keep deepening dedicated public-page adapters for Pia, eplus, Lawson Ticket, Rakuten Ticket, Ticket Board, CN Playguide, official sites, and musical production sites.
- The app does not scrape private/login-only fan-club pages; use private manual sources for those notes. Email (SMTP), in-app feed, Firebase HTTP-v1 push, and local-workspace Slack, Discord, or LINE delivery are supported when configured.

## Future upgrades

- Deepen source-specific parsers with more site-specific evidence snippets and edge-case date labels.
