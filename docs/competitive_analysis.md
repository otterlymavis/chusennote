# Competitive analysis: Songkick/Bandsintown patterns to adapt for Otterpia

This note translates the useful parts of concert-tracking apps into features that fit Otterpia's Japan-specific goal: tracking lottery applications, result dates, payment windows, and ticket-sale links for concerts and musicals.

## What Songkick appears to optimize for

Songkick presents itself as a fan-first concert tracker: users track artists, receive personalized concert alerts, and buy tickets from event pages. Its about page emphasizes three primitives: tracked artists, personalized concert alerts, and tickets without irrelevant spam or inflated-price focus. Source: <https://www.songkick.com/info/about>.

Songkick is also an aggregator. Its support docs say it indexes 100+ sources including major ticket vendors, smaller vendors, local listings, and artist-managed listings through Tourbox. Source: <https://support.songkick.com/hc/en-us/articles/360012565233-Where-does-Songkick-get-its-listings-from>.

Songkick exposes the same product shape through an event data model: artist/date/venue/location search, upcoming events, past events, user events, and tracked artists. Source: <https://www.songkick.com/developer/?q=&within=>.

### Adaptable Songkick ideas

- **Artist/event tracking graph**: store user-followed artists, musicals, production companies, venues, and regions.
- **Aggregator-first ingestion**: combine official sites, Japanese portals, RSS/news pages, and manually verified ticket links instead of relying on one search result.
- **Event identity resolution**: merge duplicate pages for the same show/tour by title, date, venue, city, and ticket URL.
- **Alert relevance controls**: alert only when a tracked entity changes, and avoid noisy repeated alerts unless lottery status changed.
- **Ticket confidence labels**: distinguish official/ticket-primary links from resale, social, or low-confidence pages.

## What Bandsintown appears to optimize for

Bandsintown emphasizes a two-sided platform: fans get discovery and alerts, while artists/promoters manage events and push updates. Its artist feature page lists presales, widgets/API, smart links, fan management, discovery, posts, email tooling, merch, and signup forms. Source: <https://www.artist.bandsintown.com/features>.

Its discovery docs describe automatic email and push alerts when artists publish new events in a fan's location, plus distribution to other platforms through widgets/API/smart links. Source: <https://www.artist.bandsintown.com/discovery-engine>.

Bandsintown's API docs show a concrete event payload worth copying conceptually: date/time, venue name/location, ticket links, lineup, description, title, and Bandsintown event page. Source: <https://help.artists.bandsintown.com/en/articles/9186477-api-documentation>.

Bandsintown also has a `Notify Me` concept for events with no ticket link or future on-sale date, letting fans opt in before ticket links exist. Source: <https://help.artists.bandsintown.com/en/articles/9186761-api-for-fan-opt-ins>.

### Adaptable Bandsintown ideas

- **Notify-me state before a lottery exists**: users can watch an event after the official announcement but before ticket pages publish lottery windows.
- **Smart-link style event page**: one Otterpia event page should show official site, Pia/eplus/Lawson links, FC links, and latest known lottery status.
- **Artist/venue/region fan preferences**: alerts should include location filters, but Japan-specific users may also want city/venue/performer-cast filters for musicals.
- **Lifecycle alerts**: announcement, lottery open, lottery closing soon, result date today, payment deadline soon, general sale soon, resale/official-trade open.
- **Embeddable/API-ready output**: keep `--json` output stable so a web UI, notification worker, or browser extension can consume the same data.

## Japan-specific gap Otterpia can own

Songkick/Bandsintown are strong at concert discovery, but Japanese ticketing has a different pain point: multiple lottery rounds, fan-club rounds, official presales, platform presales, result dates, payment deadlines, general sale dates, and sometimes official resale/trade windows.

Otterpia should therefore avoid being only a concert calendar. The core object should be a **ticket timeline**.

## Recommended implementation roadmap

### 1. Event watchlist and source registry

Implement a persistent watchlist with these entities:

- `watch_id`
- user keyword
- canonical event title
- official URL
- artist/company/cast tags
- preferred regions and venues
- source URLs discovered from the official page
- source confidence score

This mirrors Songkick's tracked-artist model but supports musicals, productions, casts, and ticket portals.

### 2. Ticket timeline model

Add a structured timeline table/object per event:

- `round_name` (e.g. `FC先行`, `オフィシャル先行`, `第1次抽選先行`, `一般発売`)
- `round_number`
- `platform` (official, Pia, eplus, Lawson, CN, Rakuten, Ticket Board)
- `application_start_at`
- `application_end_at`
- `result_at`
- `payment_start_at`
- `payment_end_at`
- `general_sale_at`
- `trade_start_at`
- `trade_end_at`
- `url`
- `evidence_text`
- `confidence`

This is the feature Songkick/Bandsintown do not specialize in, and it matches the original Otterpia use case.

### 3. Change detection and alert rules

Persist snapshots of each event/ticket page and emit alerts only when important fields change:

- new official page found
- new ticket link found
- new lottery round found
- application window opened
- application window closes in 24/48 hours
- result date is today
- payment deadline is within 24 hours
- general sale starts soon

### 4. Source-specific adapters

Keep the generic parser, but add adapters in this order:

1. official website parser: HTML/news pages, JSON-LD, Open Graph, sitemap/RSS if available
2. Pia adapter
3. eplus adapter
4. Lawson Ticket adapter
5. musical/stage production adapter for common Japanese production sites
6. fan-club/manual-private source notes for login-only information, without scraping private pages

### 5. User-facing app blocks

Keep the two-block UI, but make it richer:

- **General event info**: canonical title, official site, organizer, venue, date range, cast/lineup, region, source confidence.
- **Ticket info**: timeline grouped by platform and round, with deadline badges (`open`, `closing soon`, `results today`, `payment due`).

### 6. Discovery and deduplication

Borrow Songkick/Bandsintown discovery ideas carefully:

- recommend related events only after exact watched events are reliable
- de-duplicate by title/date/venue/platform URL
- show why an event matched a user keyword
- allow users to mute noisy sources or broad keywords

## What to implement next in this repository

The highest-value next patch is not another broad scraper. It is a persistence and alert core:

1. Add SQLite tables for watched keywords, events, sources, ticket rounds, and snapshots.
2. Save the output of `build_blocks()` into those tables.
3. Compare each run to the previous run.
4. Emit a small alert JSON list like:

```json
[
  {
    "type": "lottery_opened",
    "event": "Example Musical",
    "round": "第1次抽選先行",
    "deadline": "2026-06-18T23:59:00+09:00",
    "url": "https://t.pia.jp/example"
  }
]
```

That turns Otterpia from a one-off search command into a real monitoring app.


## Legal and data-use notes

If Otterpia later integrates official third-party APIs instead of only linking to public pages, keep each provider's terms visible in the implementation plan. For example, Songkick's API terms describe attribution and license requirements for applications using Songkick data. Source: <https://www.songkick.com/developer/api-terms-of-use>.

For the Japanese ticketing use case, the safest default is:

- store source URLs and short extracted facts, not copied full pages;
- link users back to official/ticket-provider pages for application and purchase;
- avoid scraping login-only fan-club pages unless the user manually enters their own notes;
- show evidence snippets and confidence scores so users can verify dates before applying.
