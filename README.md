# otterpia

A keyword-first assistant for tracking Japanese concert and musical ticket lotteries.

Instead of asking you to hand-maintain every ticket URL first, otterpia starts from the workflow you described:

1. You enter an artist/event/musical keyword.
2. The app searches for likely official pages.
3. It reads the official page for event details and ticket links.
4. It generates two blocks:
   - **General event info**: title, official page, date clues, venue clues, summary.
   - **Ticket / lottery info**: ticket links plus detected lottery rounds, start/end dates, result dates, general sale dates, and payment deadlines.

## Quick start

```bash
python3 lottery_monitor.py "your event keyword"
```

JSON output for a future web/app UI:

```bash
python3 lottery_monitor.py "your event keyword" --json
```

## How the current pipeline works

### 1. Search for the official page

`lottery_monitor.py` searches the web with a Japanese ticket-oriented query:

```text
<keyword> 公式 チケット 抽選 先行
```

It scores results higher when they look official (`公式`, `official`, `オフィシャル`, `公演`) and lower when they are social/noisy pages or ticket portal pages. The top official-looking pages are fetched first.

### 2. Build the General event info block

From the official pages, the app extracts:

- page title / Open Graph title
- official page URL
- nearby text around event-date labels like `公演日`, `日程`, `開催日時`
- nearby text around venue labels like `会場`, `劇場`, `ホール`, `アリーナ`
- ticket links found on the official page

If no ticket links are found, the app adds fallback search links for Pia, eplus, and Lawson Ticket.

### 3. Build the Ticket / lottery info block

From each ticket link, the app looks for Japanese ticketing phrases such as:

- `第1次抽選先行`
- `先行`
- `プレオーダー`
- `受付期間`
- `申込期間`
- `抽選結果`, `結果発表`, `当落`
- `一般発売`
- `入金`, `支払`, `払込`

It then extracts nearby dates in formats like:

- `2026/06/10`
- `2026-06-10`
- `2026年6月10日`
- `6月10日` (year inferred from the current date)

## Example output shape

```markdown
# General event info

- Keyword: Example Musical
- Title: Example Musical Official
- Official page: https://official.example/stage
- Event date clues:
  - 公演日 2026年7月10日 会場 Example Hall

# Ticket / lottery info

- Ticket links found:
  - チケット抽選先行はこちら: https://eplus.jp/example-musical/
- Lottery / sales rounds:
  - 第1次抽選先行 (eplus)
    - Lottery start: 2026-06-10
    - Lottery end: 2026-06-18
    - Results date: 2026-06-22
```


## Product research

See [`docs/competitive_analysis.md`](docs/competitive_analysis.md) for an analysis of Songkick/Bandsintown patterns and a Japan-specific implementation roadmap for Otterpia.

## Notes and limitations

- Japanese ticket sites often change HTML, use dynamic rendering, and hide details behind JavaScript or login gates.
- This version uses only the Python standard library, so it is easy to run anywhere, but site-specific parsers may be needed for high precision.
- The best long-term approach is to keep this keyword-first pipeline and add dedicated adapters for Pia, eplus, Lawson Ticket, official fan-club pages, and musical production sites.

## Future upgrades

- Add a small web UI that displays the two blocks as cards.
- Persist event history in SQLite and alert only on changes.
- Add Discord/LINE/Slack notifications for newly opened or closing lotteries.
- Add source-specific parsers for Pia/eplus/Lawson Ticket date fields.
- Support multiple rounds explicitly (`1st lottery`, `2nd lottery`, `official presale`, `general sale`).
