# VLR Calendar Feed

Generate public `.ics` feeds from VLR.gg and publish them with GitHub Pages.

Very wonky video tutorial: https://f005.backblazeb2.com/file/RisPNG/vlr-calendar-feed-tut.mp4

The repo supports these calendar source types:

- `team` - a team's upcoming, live, and optionally completed matches.
- `player` - a player's completed match history plus upcoming/live matches from their current team.
- `event` - all matches from one VLR event ID.
- `series` - all matches from every event linked from a VLR series page, such as VCT 2026 or Game Changers 2026.
- `global` - global upcoming/live/completed feeds where supported by vlrdevapi.

## Quick deploy

1. Push this repo to GitHub.
2. In GitHub, open **Settings -> Pages** and set **Source** to **GitHub Actions**.
3. Edit `config/calendars.json`.
4. Run **Actions -> Build and deploy calendar feeds -> Run workflow**.
5. Subscribe to the generated `.ics` URL from Google Calendar using **Other calendars -> + -> From URL**.

Example feed URL:

```text
https://<username>.github.io/<repo>/paper-rex.ics
```

## Series calendars

VLR series pages have URLs like:

```text
https://www.vlr.gg/series/86/valorant-champions-tour-2026
https://www.vlr.gg/series/87/valorant-game-changers-2026
```

Use the number after `/series/` as `series_id`.

```json
{
  "slug": "vct-2026",
  "name": "Valorant Champions Tour 2026",
  "description": "All VCT 2026 matches from VLR.gg.",
  "type": "series",
  "series_id": 86,
  "series_slug": "valorant-champions-tour-2026",
  "enabled": true
}
```

For Game Changers:

```json
{
  "slug": "game-changers-2026",
  "name": "Valorant Game Changers 2026",
  "description": "All Game Changers 2026 matches from VLR.gg.",
  "type": "series",
  "series_id": 87,
  "series_slug": "valorant-game-changers-2026",
  "enabled": true
}
```

The generator fetches the series page, extracts every `/event/<id>/...` link, then calls `vlr.events.matches(event_id=...)` for each event.

## Single event calendars

Use the event ID from a VLR event URL:

```text
https://www.vlr.gg/event/2775/vct-2026-pacific-stage-1
```

Config:

```json
{
  "slug": "vct-2026-pacific-stage-1",
  "name": "VCT 2026: Pacific Stage 1",
  "description": "All matches from VCT 2026: Pacific Stage 1.",
  "type": "event",
  "event_id": 2775,
  "enabled": true
}
```

## Optional series filters

You can filter which events from a series are used by event name substring.

Only Pacific events:

```json
{
  "slug": "vct-2026-pacific",
  "name": "VCT 2026 Pacific",
  "description": "Pacific VCT 2026 matches.",
  "type": "series",
  "series_id": 86,
  "series_slug": "valorant-champions-tour-2026",
  "event_name_include": ["Pacific"],
  "enabled": true
}
```

Exclude Kickoff:

```json
{
  "slug": "vct-2026-no-kickoff",
  "name": "VCT 2026 without Kickoff",
  "description": "All VCT 2026 matches except Kickoff events.",
  "type": "series",
  "series_id": 86,
  "series_slug": "valorant-champions-tour-2026",
  "event_name_exclude": ["Kickoff"],
  "enabled": true
}
```

## Settings

Important settings in `config/calendars.json`:

- `published_ttl_hours`: hint for calendar clients, default `2`.
- `event_match_limit`: maximum matches fetched per event.
- `series_event_limit`: maximum events read from a series page.
- `include_completed`: include completed matches.
- `include_live`: include live matches.
- `live_match_start_fallback`: use `now` for live matches that do not expose a start time.
- `default_match_duration_minutes`: fallback calendar event duration when match format cannot be detected.
- `infer_match_duration_from_best_of`: when `true`, use BO format before the fallback duration.
- `best_of_duration_minutes`: map BO values to event durations, for example BO1 = 60, BO3 = 120, BO5 = 180.
- `fetch_best_of_from_match_detail`: when BO is not present in the match list response, fetch `vlr.series.info(match_id=...)` to try reading `best_of`.
- `best_of_detail_fetch_max_matches`: caps extra match-detail requests per calendar run to avoid overly slow global/series feeds.

Example duration settings:

```json
{
  "default_match_duration_minutes": 120,
  "infer_match_duration_from_best_of": true,
  "fetch_best_of_from_match_detail": true,
  "best_of_detail_fetch_max_matches": 250,
  "best_of_duration_minutes": {
    "1": 60,
    "3": 120,
    "5": 180
  }
}
```

If BO format cannot be read, the generator still falls back to `default_match_duration_minutes`.

## Google Calendar cache note

Google Calendar may cache subscribed `.ics` feeds. If you need to force a fresh subscription for your own calendar, add a cache buster when subscribing:

```text
https://<username>.github.io/<repo>/vct-2026.ics?v=2
```

## Local test

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/build_calendar.py
```
