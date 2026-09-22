# Chusennote App Store metadata

This is the source-of-truth draft for the first App Store Connect record. Keep
the submitted listing synchronized with this file.

## App record

- Platform: iOS
- Name: Chusennote
- Primary language: English (U.S.)
- Bundle ID: `com.chusennote.mobile`
- SKU: `chusennote-ios-001`
- Primary category: Entertainment
- Secondary category: Utilities

Creating the App Store Connect record is an external account action. Confirm
the values above immediately before selecting **Create**.

## Version 0.1.0

Subtitle:

> Ticket deadlines, organized

Promotional text:

> Track artists and exact events, collect official ticket dates in one place, and get reminders before applications, results, payments, sales, and official resale windows.

Description:

> Chusennote keeps event and ticket deadlines together so you can act before a window closes.
>
> Track an artist to discover related public event pages, or follow an exact event when you already know what you want. Each event can show official links, dates, venues, organizers, lineup details, ticket rounds, application windows, result dates, payment deadlines, general sales, and official resale periods when the source publishes them.
>
> Choose reminder timing for the events and rounds that matter. Delivered reminders remain visible in the app, and calendar feeds help you keep important dates alongside the rest of your schedule.
>
> Chusennote links back to public source pages and keeps confidence and provenance visible. It does not claim that an inferred date is official when the source does not say so.
>
> An account is optional. Signing in keeps watches, sources, reminder preferences, calendar access, and push registrations scoped to you. Signed-in users can permanently delete their account and associated account data from Settings.

Keywords:

> concert,tickets,lottery,events,reminders,artist,venue,resale,calendar

URLs:

- Support URL: `https://chusennote.onrender.com/support`
- Privacy Policy URL: `https://chusennote.onrender.com/privacy`
- User Privacy Choices URL: `https://chusennote.onrender.com/privacy`
- Marketing URL: `https://chusennote.onrender.com/`

## Review notes

- The production API is `https://chusennote.onrender.com`.
- Account creation is optional; the anonymous workspace remains usable.
- Account deletion is in **Settings → Server → Delete Account** and requires
  the current password plus a destructive confirmation.
- Push delivery uses Firebase Cloud Messaging backed by production APNs.
- The app does not sell tickets or unlock paid digital content.
- Public source links open the publisher or ticket provider in the browser.
- Provide a temporary review account only if App Review requests authenticated
  coverage; do not commit review credentials here.

## App privacy answers

The listing should describe data used for app functionality, not tracking:

- Contact Info → Email Address: collected, linked to identity, app functionality.
- User Content → Other User Content: watches, tags, regions, venues, manual
  public source links, and reminder preferences; linked to identity when signed
  in; app functionality.
- Identifiers → Device ID: Firebase device token; linked to identity when
  signed in; app functionality.
- Diagnostics: disclose only if production logging is changed to retain crash,
  performance, or other diagnostic data beyond the hosting provider's ordinary
  infrastructure logs.
- Tracking: no.

## Compliance

- `ITSAppUsesNonExemptEncryption` is `false`; the app relies on exempt
  operating-system HTTPS/TLS rather than proprietary cryptography.
- The app supports account creation and in-app permanent account deletion.
- Privacy and support pages are public and included in the deployment smoke
  suite.
- Age rating draft: select the lowest rating supported by the questionnaire;
  the app has no user-generated public feed, gambling, contests, or mature
  content, but it opens external event and ticket pages in the browser.
