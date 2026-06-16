# Push & email notifications setup

The chusennote backend generates ticket-date reminders (lottery open/close,
results, payment, general sale, performance dates) for your notification
subscriptions and delivers them to three channels: an in-app feed (always),
email (SMTP), and mobile push (Firebase Cloud Messaging).

## 1. Backend

Subscribe and run the deliverer (schedule `notify run` next to `event run`):

```
python lottery_monitor.py notify subscribe "<artist or event>" --scope event_all --channels feed,push,email
python lottery_monitor.py notify run --db chusennote.sqlite3
```

Configure the channels via environment variables:

| Variable | Purpose |
| --- | --- |
| `CHUSENNOTE_FCM_SERVER_KEY` | Firebase Cloud Messaging server key (enables push) |
| `CHUSENNOTE_SMTP_HOST` / `CHUSENNOTE_SMTP_PORT` | SMTP server (enables email) |
| `CHUSENNOTE_SMTP_USER` / `CHUSENNOTE_SMTP_PASSWORD` | SMTP login |
| `CHUSENNOTE_SMTP_FROM` | From address (defaults to the SMTP user) |
| `CHUSENNOTE_NOTIFY_EMAIL` | Recipient address |

If a channel is unconfigured it silently no-ops; the in-app feed
(`GET /api/notifications`) always works.

## 2. Firebase project (push)

1. Create a Firebase project and add an **Android app** (`com.chusennote.mobile`)
   and an **iOS app** (your bundle id).
2. Copy the **Cloud Messaging server key** into `CHUSENNOTE_FCM_SERVER_KEY`.

## 3. Android

1. Download `google-services.json` from the Firebase console and place it at
   `android/app/google-services.json`.
2. Build & run. The app requests the notification permission, creates the
   `chusennote_reminders` channel, fetches its FCM token, and registers it with
   the backend (`POST /api/devices`). Incoming pushes show in the channel.

(The Firebase Messaging dependency and `google-services` plugin are already
wired in `android/build.gradle` and `android/app/build.gradle`.)

## 4. iOS

1. Add the **Firebase** Swift package
   (`https://github.com/firebase/firebase-ios-sdk`) and link
   `FirebaseMessaging` to the Chusennote target.
2. Add `GoogleService-Info.plist` to the app target.
3. Enable the **Push Notifications** and **Background Modes → Remote
   notifications** capabilities.
4. `ChusennoteApp.swift` already configures Firebase, requests authorization,
   and posts the FCM token to `POST /api/devices`. Without the Firebase package
   it falls back to registering the raw APNs token.
