# chusennote Android app

A thin native client for the chusennote backend. It talks to the local web
server's REST API (watches, events, upcoming ticket dates, alerts) and receives
push reminders via Firebase Cloud Messaging.

The Android client supports both broad ticket watches and exact-event tracking.
Signed-in users can permanently delete their account from the Account section;
the app requires the current password and clears local credentials only after
the backend confirms that account-owned data was removed.
Use **Find Exact Event** to search for an official event page, open it for
review, and then add that specific event to the current account or local
workspace.

Use **Notify** on a tracked artist or event to create a feed-and-push reminder
subscription with the default 7/1/0-day lead times. The app lists subscriptions
and delivered reminders, shows the push devices registered to the current
account or local workspace, and **Run Due Reminders** triggers an immediate
delivery pass. Saved event details expose every ticket round, location, ticket
link, rule, price note, explicitly labeled organizer and cast/lineup fact, and
manual source returned by the backend. Official-resale windows appear in each
round's schedule. Use the round and location actions when a broad all-round
reminder would be too noisy.
Artist and event watch checks can also be run independently.

## Build

Requires a JDK (17+) and the Android SDK (`compileSdk 35`, `build-tools 35.x`).
The Gradle wrapper pins Gradle 8.14.3, so no separate Gradle install is needed.

```bash
cd android
./gradlew :app:assembleDebug        # gradlew.bat on Windows cmd/PowerShell
```

The debug APK is written to:

```
android/app/build/outputs/apk/debug/app-debug.apk
```

Install it on a connected device/emulator with `./gradlew :app:installDebug`
(or `adb install app-debug.apk`).

## Test

With an Android 13+ device or emulator connected, run:

```bash
./gradlew :app:connectedDebugAndroidTest
```

The instrumentation suite uses an in-process HTTP server. It verifies that the
entire form remains scroll-reachable and that adding an event sends the real
form request and renders the refreshed watch without requiring a backend.
The current suite passes 10/10 on an Android 15 Google APIs emulator; this is
local emulator proof, not physical-device or production Firebase delivery
proof.

The server status line includes the backend version, build, and schema when a
current server provides them. Older health responses remain supported and keep
showing the existing watch and alert counts.

## Release signing

`./gradlew :app:assembleRelease` always validates and builds the release
variant. It produces an unsigned APK unless all four signing variables are set:

```bash
export CHUSENNOTE_ANDROID_KEYSTORE=/absolute/path/to/release.keystore
export CHUSENNOTE_ANDROID_STORE_PASSWORD='...'
export CHUSENNOTE_ANDROID_KEY_ALIAS='...'
export CHUSENNOTE_ANDROID_KEY_PASSWORD='...'
./gradlew :app:assembleRelease
```

The keystore and passwords are read only from the environment and must not be
committed. A configured build writes `app/build/outputs/apk/release/app-release.apk`.
If only some variables are present, Gradle stops with an error instead of
silently producing an unsigned release.

## Connecting to the backend

The distribution build connects to the hosted production service by default:

- Production: `https://chusennote.onrender.com`

For local development, run the backend on your machine
(`python lottery_monitor.py web --port 8877`) and override the app's **API base
URL**:

- Emulator → host machine: `http://10.0.2.2:8877`
- Physical device on the same network: `http://<your-PC-LAN-IP>:8877`

Account credentials and FCM device tokens are sent only to HTTPS, localhost,
or literal private-network IP endpoints. Use HTTPS for any public backend.

## Push notifications (optional)

Firebase Cloud Messaging is wired but optional. Without
`app/google-services.json` the app still builds and runs; push registration is
simply disabled at runtime. See [README-notifications.md](README-notifications.md)
to enable it.
