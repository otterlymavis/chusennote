# Mobile apps

chusennote now includes lightweight native mobile clients for the local web/API server.

## Start the API server

Run this from the repository root before opening either app:

```bash
python lottery_monitor.py web --db chusennote.sqlite3 --port 8765
```

The mobile apps read:

- `GET /api/watchlist`
- `GET /api/events`
- `GET /api/alerts`
- `POST /api/watchlist`
- `POST /api/run`
- `POST /api/sources`

They display the same two product lanes as the web app:

- **Tracked Artists**: basic artist/event discovery watches.
- **Tracked Events**: ticket and lottery timeline watches.

Both mobile clients can add and remove tracked artists, add and remove tracked events, add and remove manual public sources, refresh current data, run tracked event checks, and show recent alerts from the local server. The Python server still performs the actual scraping, persistence, and alert generation. The apps remember the API base URL locally after you change it.

## Android

Open `android/` in Android Studio.

Default emulator URL:

```text
http://10.0.2.2:8765
```

For a physical Android device, change the base URL in the app to your computer's LAN IP, for example:

```text
http://192.168.1.20:8765
```

## iOS

Open `ios/Chusennote.xcodeproj` in Xcode.

Default simulator URL:

```text
http://127.0.0.1:8765
```

For a physical iPhone, change the base URL in the app to your computer's LAN IP.

## CI builds

GitHub Actions are configured for Python tests, Android debug APK builds, and iOS simulator builds. The Android workflow uploads a debug APK artifact, and the iOS workflow uploads a simulator app artifact. Local native builds still require Android Studio/Gradle or Xcode on the development machine.
