# Push & email notifications setup

The chusennote backend generates ticket-date reminders (lottery open/close,
results, payment, general sale, performance dates) for your notification
subscriptions and delivers them to three channels: an in-app feed (always),
email (SMTP), and mobile push (Firebase Cloud Messaging).

The backend sends push notifications through Firebase Cloud Messaging's HTTP
v1 API. It obtains short-lived OAuth 2.0 credentials through Google
Application Default Credentials (ADC); legacy FCM server keys are not used.

## 1. Backend

Subscribe; the scheduled monitor task (`<kind> run`) and `watch loop` now
deliver due reminders automatically after each pass, so no extra scheduling is
needed. `notify run` is still available for a standalone delivery pass.

```
python lottery_monitor.py notify subscribe "<artist or event>" --scope event_all --channels feed,push,email
python lottery_monitor.py event run --db chusennote.sqlite3   # discovers updates AND delivers reminders
python lottery_monitor.py notify run --db chusennote.sqlite3  # delivery only (optional)
python lottery_monitor.py notify status --db chusennote.sqlite3  # configuration preflight
```

Configure the channels via environment variables:

| Variable | Purpose |
| --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional path to a Google service-account JSON file; omit on Google runtimes that provide ADC |
| `CHUSENNOTE_FIREBASE_PROJECT_ID` | Optional Firebase project override when ADC cannot infer the project |
| `CHUSENNOTE_SMTP_HOST` / `CHUSENNOTE_SMTP_PORT` | SMTP server (enables email) |
| `CHUSENNOTE_SMTP_USER` / `CHUSENNOTE_SMTP_PASSWORD` | SMTP login |
| `CHUSENNOTE_SMTP_FROM` | From address (defaults to the SMTP user) |
| `CHUSENNOTE_NOTIFY_EMAIL` | Recipient address |

If a channel is unconfigured it silently no-ops; the in-app feed
(`GET /api/notifications`) always works.
Failed external channels are retried by the next due-reminder pass on the same
day. Channels that already succeeded are not resent, and the feed row is
updated in place instead of duplicated. FCM tokens reported as `UNREGISTERED`
are removed; transient FCM failures retain the token and remain retryable.
Database-backed delivery claims prevent separate server/worker processes from
sending the same reminder concurrently. An interrupted claim is reclaimable
after 15 minutes.

## 2. Firebase project (push)

1. Create a Firebase project and add an **Android app** (`com.chusennote.mobile`)
   and an **iOS app** (your bundle id).
2. Enable the Firebase Cloud Messaging API and grant the backend identity
   permission to send messages to that project. On Google-hosted runtimes use
   the attached workload identity; elsewhere, set `GOOGLE_APPLICATION_CREDENTIALS`
   to a narrowly scoped service-account JSON file. Never commit that file.
3. Set `CHUSENNOTE_FIREBASE_PROJECT_ID` only when ADC does not infer the target
   project. The sender posts one message per device to
   `POST https://fcm.googleapis.com/v1/projects/<project-id>/messages:send`.

## 3. Android

The app's Account section (Register/Log In) is optional but recommended once
you're pointing it at a shared/hosted backend rather than a personal local
server: signing in scopes your watches, sources, subscriptions, and this
device's push registration to your account instead of the single shared
anonymous workspace every signed-out install shares.

Deploy the updated backend before the Android client: logout now sends this
device's FCM token to `/api/auth/logout` and requires confirmation that it was
detached before clearing the login. If the server cannot be reached, logout
shows a retry message and retains the credential so detachment can be retried.
While signed in, the app also locks the account fields and API base URL. Finish
logout first before switching accounts or servers, so the old device
registration cannot be left attached and continue receiving that account's
alerts.
Temporary failures checking account status also preserve the saved login.

In the app, tap **Notify** on a tracked artist or event to subscribe to feed and
push reminders with the default 7/1/0-day lead times. **Run Due Reminders**
performs an immediate delivery pass, and the app shows both the delivered feed
and active subscriptions for the current account or local workspace.

1. Download `google-services.json` from the Firebase console and place it at
   `android/app/google-services.json`.
2. Build & run. The app requests the notification permission, creates the
   `chusennote_reminders` channel, fetches its FCM token, and registers it with
   the backend (`POST /api/devices`). Incoming pushes show in the channel;
   tapping one opens the app. Foreground delivery is supported across the
   app's full Android 7.0+ version range.

The app **builds and runs without Firebase**: `android/app/build.gradle`
applies the `google-services` plugin only when `google-services.json` is
present, and push registration is skipped at runtime until then. Just drop the
file in and rebuild to enable push — no gradle edits needed.

## 4. iOS

1. Add `GoogleService-Info.plist` to the app target. Firebase Messaging is
   already linked through Swift Package Manager.
2. Enable the **Push Notifications** and **Background Modes → Remote
   notifications** capabilities.
3. `ChusennoteApp.swift` configures Firebase, requests authorization, and posts
   the FCM token to `POST /api/devices`. Without the plist, Firebase and device
   registration remain disabled; raw APNs tokens are never sent as FCM tokens.
