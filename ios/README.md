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

## Run in Xcode

1. Open `ios/Chusennote.xcodeproj` in Xcode.
The app defaults to the hosted production API at
`https://chusennote.onrender.com`. To develop against a local server, run:

```bash
python lottery_monitor.py web --db chusennote.sqlite3 --port 8877
```

Then set the Base URL in the app's Settings screen to
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

Create and export a distribution-signed archive from a Mac whose Xcode account
has an Apple Distribution certificate and App Store provisioning access:

```bash
xcodebuild -project ios/Chusennote.xcodeproj \
  -scheme Chusennote -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath /tmp/Chusennote.xcarchive \
  -allowProvisioningUpdates archive

xcodebuild -exportArchive \
  -archivePath /tmp/Chusennote.xcarchive \
  -exportPath /tmp/Chusennote-export \
  -exportOptionsPlist ios/ExportOptions.plist \
  -allowProvisioningUpdates
```

The export configuration is non-secret and selects App Store Connect,
automatic signing, and team `D8H3TBWH7P`. A successful archive alone is not
distribution proof: verify that export produces an IPA signed by an Apple
Distribution identity and containing a production push entitlement.
