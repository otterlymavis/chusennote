# chusennote iOS

This folder contains the SwiftUI iOS client for the chusennote API. It supports
the same watch, exact-event, ticket-detail, notification, account, and calendar
flows as the backend UI. Active event and artist watches expose an Edit action
for tags, region and venue filters, and alert choices (including official-resale
opening and closing alerts); those settings are
scoped to the signed-in account even when another account tracks the same
keyword.

Event detail includes organizer and cast/lineup sections when an official page
publishes those facts under explicit labels. The app does not infer people or
companies from unlabeled prose.

The app has three tabs: Home (upcoming ticket deadlines and recent updates),
Watching, and Settings. In Watching, tap + and choose Keyword or Official Page;
filters and reminder choices stay collapsed until needed. Swipe an event to edit
or remove it. Settings contains Account, notifications, the calendar feed, and,
under More, artist watches and your own sources. Their Add actions open a
separate editor, leaving saved items visible first.

Debug builds also include Settings → Developer Tools for the server override,
release/schema diagnostics, push-device tokens, and Send Due Reminders. These
controls are excluded from Release builds.

## Run in Xcode

1. Open `ios/Chusennote.xcodeproj` in Xcode.
The app defaults to the hosted production API at
`https://chusennote.onrender.com`. To develop against a local server, run:

```bash
python lottery_monitor.py web --db chusennote.sqlite3 --port 8877
```

Then set the Base URL in the Debug app's Settings → Developer Tools screen to
`http://127.0.0.1:8877` in the iOS simulator. On a physical device, use the
computer's private LAN address instead.

The shared `Chusennote` scheme includes the `ChusennoteTests` unit-test target.
Run the scheme's Test action in Xcode, or use `xcodebuild test`, to verify the
credential transport and redirect-rejection policy on an iOS simulator.

For local development on a physical device, set the base URL to your
computer's LAN IP, for example `http://192.168.1.20:8877`, and make sure both
devices are on the same network.

Account credentials, API/calendar tokens, and FCM device tokens are sent only
to HTTPS, localhost, or literal private-network IP endpoints. API, calendar,
and migrated push tokens are stored in the Keychain. Finish logout before
changing accounts or servers so the backend can revoke the session and detach
the device registration.

Signed-in users can permanently delete their account from Settings. The app
requires the current password and a destructive confirmation, waits for the
server to remove account-owned data, and clears its saved credentials and push
token only after the server confirms deletion.

## Firebase push setup

`FirebaseMessaging` is linked through Swift Package Manager and locked in
`Package.resolved`. To enable push on a real device:

1. Add an iOS app with bundle ID `com.chusennote.mobile` to the same Firebase
   project used by the backend and Android client.
2. Download `GoogleService-Info.plist` to `ios/Chusennote/`. The Xcode build
   copies it into the app when present; the ignored file does not need to be
   added to the project. Do not commit it unless that is an intentional
   repository policy.
3. Select an Apple development team and a provisioning profile with the Push
   Notifications capability. The target already declares `aps-environment`
   and the `remote-notification` background mode.
4. Configure the shared backend's FCM HTTP v1 sender and test delivery on a
   physical device. Simulator build/launch checks do not prove APNs/FCM
   delivery.

Unsigned CI/simulator builds remain buildable and launch-safe without the
plist: Firebase startup and token registration are skipped. Signed Release
builds require it, so an installable release cannot silently omit push
configuration.

## App Store archive

Create and export a distribution-signed archive from a Mac whose login
keychain contains the team's Apple Distribution certificate and private key,
and whose provisioning-profile directory contains `Chusennote App Store`:

```bash
xcodebuild -project ios/Chusennote.xcodeproj \
  -scheme Chusennote -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath /tmp/Chusennote.xcarchive \
  archive

xcodebuild -exportArchive \
  -archivePath /tmp/Chusennote.xcarchive \
  -exportPath /tmp/Chusennote-export \
  -exportOptionsPlist ios/ExportOptions.plist
```

The Release target and export configuration select the `Chusennote App Store`
profile, an Apple Distribution identity, and team `D8H3TBWH7P`. This keeps
archive/export reproducible without relying on an Xcode account while leaving
Debug signing automatic. A successful archive alone is not distribution proof:
verify that export produces an IPA signed by an Apple Distribution identity and
containing a production push entitlement.

## TestFlight acceptance on an iPhone

An uploaded, validated build is not installed automatically. In App Store
Connect, add the intended Apple ID to an internal TestFlight group containing
the specific build. On the iPhone, sign in to the TestFlight app with that same
Apple ID, accept the invitation, and install Chusennote. Record the build number
shown in TestFlight before testing; a simulator run or an older IPA does not
verify this distributed build.

For build `0.1.0 (1)`, check the following on the installed app:

1. Open the default hosted API without entering a local server URL. Verify
   Home/Notifications and Settings load without a connection error.
2. In Watch, add an event keyword, run checks, and inspect any ticket timeline
   and its official source link. Empty results alone are not a delivery failure.
3. In Watch → Add → Official Page, search for a known public event page, add one
   result, and verify the success/error message and resulting watch. A saved
   page still needs source verification before relying on ticket dates.
4. Edit the watch's tags, region/venue filters, and reminder choices; then
   confirm the saved values survive app restart. Remove and restore a watch.
5. Check account sign-in/out only with a disposable test account. Keep
   permanent account deletion for a separately authorized disposable account.
6. Check notification permission and token registration on the physical
   device. A visible alert requires a real due event plus working backend FCM
   and APNs configuration; an install or permission grant alone proves neither.

Record the device/iOS version, Apple ID used for TestFlight, build number,
steps, expected versus observed behavior, and screenshots for any failure.
Keep TestFlight installation, hosted API reachability, and real push delivery
as separate acceptance results.
