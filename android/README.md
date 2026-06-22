# chusennote Android app

A thin native client for the chusennote backend. It talks to the local web
server's REST API (watches, events, upcoming ticket dates, alerts) and receives
push reminders via Firebase Cloud Messaging.

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

## Connecting to the backend

Run the backend on your machine (`python lottery_monitor.py web --port 8877`)
and set the app's **API base URL**:

- Emulator → host machine: `http://10.0.2.2:8877` (the default)
- Physical device on the same network: `http://<your-PC-LAN-IP>:8877`

## Push notifications (optional)

Firebase Cloud Messaging is wired but optional. Without
`app/google-services.json` the app still builds and runs; push registration is
simply disabled at runtime. See [README-notifications.md](README-notifications.md)
to enable it.
