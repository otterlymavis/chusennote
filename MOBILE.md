# Mobile apps

chusennote includes native iOS and Android clients. Both default to the hosted
API at `https://chusennote.onrender.com`; a local server is optional for
development, not required to use a distributed app.

## Check the hosted API

Before testing a distributed build, verify the public service and its
PostgreSQL backing store:

```bash
python3 lottery_monitor.py smoke \
  --base-url https://chusennote.onrender.com --require-postgres
```

This checks public API reachability, not account sign-in, scheduled checks, or
push delivery on a real device. A free hosted service may take time to wake.

## Optional local development server

To develop against local changes, run this from the repository root and change
the app's Base URL in Settings to the appropriate loopback or LAN address:

```bash
python lottery_monitor.py web --db chusennote.sqlite3 --port 8877
```

On Windows, you can use the helper script instead:

```powershell
.\scripts\start-chusennote.ps1 -Open
```

Check that the server is reachable:

```powershell
.\scripts\check-chusennote.ps1
```

On macOS, Linux, or Windows, the equivalent read-only check is:

```bash
python3 lottery_monitor.py smoke --base-url http://127.0.0.1:8877
```

The mobile apps read:

- `GET /api/health`
- `GET /api/watchlist?include_muted=1`
- `GET /api/events`
- `GET /api/upcoming`
- `GET /api/alerts`
- `GET /api/sources?include_muted=1`
- `GET /api/notifications?limit=100`
- `GET /api/subscriptions`
- `GET /api/devices`
- `GET /calendar.ics`
- `POST /api/auth/register`, `/api/auth/login`, and `/api/auth/logout`
- `POST /api/calendar/token`
- `POST /api/watchlist`
- `POST /api/run`
- `POST /api/sources`
- `POST /api/subscriptions`
- `POST /api/devices`
- `POST /api/notifications/run`
- `POST /api/watchlist/mute`
- `POST /api/watchlist/unmute`

The health response includes the backend version, build, and schema. The iOS
settings screen displays all three so operators can confirm which server
release the app is using; older servers remain decodable and show the release
as unavailable.

They display the same two product lanes as the web app:

- **Tracked Artists**: basic artist/event discovery watches with saved date and venue clues.
- **Tracked Events**: ticket and lottery timeline watches.

Both mobile clients can add and remove tracked artists, add and remove tracked events, set and show tags plus preferred regions/venues, set and show event alert filters, restore muted tracked artists/events, add and remove manual public sources, store private source notes, open official event pages, web source URLs, and urgent ticket URLs, refresh current data, run tracked event checks, show server health, open the calendar feed, show “Needs Attention” ticket dates, show ticket evidence snippets, show event-detail manual sources where available, show official-resale windows, show organizer and cast/lineup sections from explicitly labeled public-page facts, and show recent alerts with watch context from the configured server. The iOS client additionally provides dedicated official-resale alert toggles; the additive API fields remain safe for older clients. The Python server still performs the actual scraping, persistence, and alert generation. The apps remember the API base URL locally after you change it.

`/api/events` and `/api/upcoming` retain machine-readable `status` codes and
also expose `status_label` for display. `/api/alerts` likewise retains `type`
and adds `type_label`. Both mobile clients prefer the labels and map the known
event and alert codes locally when connected to an older server.

Both clients provide register/login/logout flows. Account and calendar tokens
are stored in platform secure storage, public servers require HTTPS for
credential-bearing requests, and signed-in clients lock server/account changes
until logout has revoked the session and detached that device's push token.

The server also exposes a standard iCalendar ticket timeline feed at `/calendar.ics` for calendar apps that can subscribe to a local URL.

Signed-in clients obtain a calendar-only token with authenticated
`POST /api/calendar/token` and use it in `/calendar.ics?token=...`. Issuing a
token on another device preserves existing subscription URLs. To deliberately
revoke all of an account's old calendar URLs, send `rotate=1` to that endpoint;
the returned token is the replacement. Schema version 15 preserves existing
calendar tokens while allowing multiple tokens per account. iOS reports a
calendar authorization failure instead of opening the anonymous feed.
Any supplied blank, invalid, duplicated, or rotated calendar token returns 401;
only a request with no token at all selects the local anonymous calendar.

Watch and source removal in the apps is a local soft mute. Muted tracked artists/events appear in a "Removed" section (shown only when there is something to restore), as do muted manual sources, and both can be restored there.

Recurring checks run server-side, not in the mobile apps. The hosted deployment
uses the separate `Hosted monitor` GitHub Actions workflow against the same
PostgreSQL database; its schedule and actual delivery must be verified
separately. For optional local development, use `python lottery_monitor.py
watch loop ...` or `scripts/start-chusennote-monitor.ps1`. On Windows,
`scripts/install-chusennote-monitor-task.ps1` can register a local Task
Scheduler job. The mobile apps read server state; they do not schedule
scraping themselves.

## Android

Open `android/` in Android Studio.

Default API URL:

```text
https://chusennote.onrender.com
```

Optional local emulator URL:

```text
http://10.0.2.2:8877
```

For local development on a physical Android device, change the base URL in the
app to your computer's LAN IP, for example:

```text
http://192.168.1.20:8877
```

Start the optional local server with LAN binding first:

```powershell
.\scripts\start-chusennote.ps1 -Lan
```

## iOS

Open `ios/Chusennote.xcodeproj` in Xcode.

Default API URL:

```text
https://chusennote.onrender.com
```

Optional local simulator URL:

```text
http://127.0.0.1:8877
```

For local development on a physical iPhone, change the base URL in the app to
your computer's LAN IP. Distributed builds should keep the hosted URL.

Start the optional local server with LAN binding first:

```powershell
.\scripts\start-chusennote.ps1 -Lan
```

The iOS target links Firebase Messaging through Swift Package Manager. Add the
Firebase project's `GoogleService-Info.plist` to the app target to enable FCM;
without it, Firebase initialization and push-token registration remain off.

## CI builds

GitHub Actions are configured for Python tests, Android JVM/instrumentation
tests, debug/test/release assembly, debug/release lint, and iOS simulator
tests/builds, static analysis, plus an unsigned generic Release archive. The
Android workflow uploads its debug APK and validation reports; the iOS workflow
uploads the simulator app and XCTest results. Local native builds still require
Android Studio/Gradle or Xcode on the development machine. Use
`python3 lottery_monitor.py preflight` from the repository root to inspect
release integration readiness without printing secrets.

The iOS signing gate requires a concrete `DEVELOPMENT_TEAM` and the production
push entitlement in the Xcode project. A signed archive must still be verified
on a machine holding the matching certificate and provisioning profile.

Preflight also requires the backend release identity to match the iOS and
Android version/build metadata. The deployment smoke check rejects a healthy
server running another release.
