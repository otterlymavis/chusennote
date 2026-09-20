# Mobile apps

chusennote now includes lightweight native mobile clients for the local web/API server.

## Start the API server

Run this from the repository root before opening either app:

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

Both mobile clients can add and remove tracked artists, add and remove tracked events, set and show tags plus preferred regions/venues, set and show event alert filters, restore muted tracked artists/events, add and remove manual public sources, store private source notes, open official event pages, web source URLs, and urgent ticket URLs, refresh current data, run tracked event checks, show server health, open the calendar feed, show “Needs Attention” ticket dates, show ticket evidence snippets, show event-detail manual sources where available, show official-resale windows, show organizer and cast/lineup sections from explicitly labeled public-page facts, and show recent alerts with watch context from the local server. The iOS client additionally provides dedicated official-resale alert toggles; the additive API fields remain safe for older clients. The Python server still performs the actual scraping, persistence, and alert generation. The apps remember the API base URL locally after you change it.

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

Watch and source removal in the apps is a local soft mute. Muted tracked artists/events appear in the mobile "Muted Watches" section, muted manual sources appear in "Muted Sources", and both can be restored there.

Recurring checks run on the desktop/server side with `python lottery_monitor.py watch loop ...` or `scripts/start-chusennote-monitor.ps1`. On Windows, `scripts/install-chusennote-monitor-task.ps1` can also register a local Task Scheduler job that runs saved checks periodically. The mobile apps read the saved local state; they do not schedule scraping themselves.

## Android

Open `android/` in Android Studio.

Default emulator URL:

```text
http://10.0.2.2:8877
```

For a physical Android device, change the base URL in the app to your computer's LAN IP, for example:

```text
http://192.168.1.20:8877
```

Start the server with LAN binding first:

```powershell
.\scripts\start-chusennote.ps1 -Lan
```

## iOS

Open `ios/Chusennote.xcodeproj` in Xcode.

Default simulator URL:

```text
http://127.0.0.1:8877
```

For a physical iPhone, change the base URL in the app to your computer's LAN IP.

Start the server with LAN binding first:

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
