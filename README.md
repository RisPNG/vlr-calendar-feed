# VLR Calendar Feed

Generate public `.ics` calendar feeds from VLR.gg match data using [`vlrdevapi`](https://vlrdevapi.readthedocs.io/), then deploy them to GitHub Pages with a scheduled GitHub Actions workflow.

This is designed for fixture calendars, not score tracking. Calendar titles look like:

```text
VCT Pacific | Paper Rex vs DRX
```

Each event description contains the VLR.gg match link.

## What this repo gives you

- Scheduled GitHub Actions sync.
- Static GitHub Pages deployment.
- One `.ics` file per configured calendar.
- A simple `index.html` landing page with subscription links.
- Team calendars using `vlr.teams.upcoming_matches()`.
- Optional live-match capture using `vlr.matches.live()`.
- Optional completed-match inclusion.
- Experimental player calendars by resolving the player's current VLR team(s).
- Stable iCalendar `UID`s based on VLR match IDs to reduce duplicate events.

## Quick deploy

1. Push this repo to GitHub.
2. Edit `config/calendars.json`.
3. Go to **Settings -> Pages -> Build and deployment -> Source -> GitHub Actions**.
4. Go to **Actions -> Build and deploy calendar feeds -> Run workflow**.
5. Your calendar feed will be available at:

```text
https://<your-github-username>.github.io/<repo-name>/<calendar-slug>.ics
```

For your repo, Paper Rex should eventually be:

```text
https://rispng.github.io/vlr-calendar-feed/paper-rex.ics
```

Use the GitHub Pages URL in Google Calendar's **From URL** subscription flow. Do not download/import the `.ics` if you want auto-updates.

## Configure calendars

Edit `config/calendars.json`:

```json
{
  "site": {
    "title": "VLR Calendar Feeds",
    "description": "Public Valorant match calendars generated from VLR.gg.",
    "base_url": ""
  },
  "settings": {
    "timezone": "Asia/Kuala_Lumpur",
    "default_match_duration_minutes": 120,
    "upcoming_limit": 50,
    "include_live": true,
    "live_limit": 50,
    "live_match_start_fallback": "now",
    "completed_limit": 0,
    "include_completed": false,
    "request_timeout_seconds": 20,
    "allow_date_only": false,
    "published_ttl_hours": 2
  },
  "calendars": [
    {
      "slug": "paper-rex",
      "name": "Paper Rex VLR Matches",
      "description": "Upcoming and live Paper Rex Valorant fixtures from VLR.gg.",
      "type": "team",
      "team_id": 624,
      "team_aliases": ["Paper Rex", "PRX"],
      "enabled": true
    }
  ]
}
```

### Important settings

| Setting | Meaning |
|---|---|
| `include_live` | When `true`, the generator also checks `vlr.matches.live()` and includes matching live games. |
| `live_match_start_fallback` | If a live match has no parseable start time, use `now` so it still appears in the feed. |
| `team_aliases` | Fallback names/tags used when a live match does not expose team IDs. |
| `default_match_duration_minutes` | Calendar event duration. Useful because VLR fixtures often have a start time but no end time. |
| `include_completed` | Keep `false` for a clean fixture-only calendar. |

### Add another team

```json
{
  "slug": "sentinels",
  "name": "Sentinels VLR Matches",
  "description": "Upcoming and live Sentinels Valorant fixtures from VLR.gg.",
  "type": "team",
  "team_id": 2,
  "team_aliases": ["Sentinels", "SEN"],
  "enabled": true
}
```

### Player calendar

Player calendars are best-effort. `vlrdevapi.players.matches()` covers match history, while upcoming and live player fixtures are inferred from current team membership.

```json
{
  "slug": "something-player",
  "name": "Player VLR Matches",
  "type": "player",
  "player_id": 1234,
  "enabled": true
}
```

## Find VLR IDs

Use the helper script after installing dependencies:

```bash
python scripts/find_ids.py "paper rex" --type team
python scripts/find_ids.py "tenz" --type player
```

Copy the returned `team_id` or `player_id` into `config/calendars.json`.

## Add to Google Calendar

1. Open Google Calendar on desktop.
2. Next to **Other calendars**, click **+**.
3. Choose **From URL**.
4. Paste your `.ics` URL.
5. Click **Add calendar**.

Google Calendar controls how often subscribed feeds refresh. This repo can update the source feed frequently, but subscribed users may not see changes immediately.

## Local development

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Build the public files:

```bash
python scripts/build_calendar.py
```

Run tests:

```bash
python -m pytest
```

## GitHub Actions schedule

The workflow runs every 2 hours by default at minute 17 to avoid the top-of-hour load spike. Edit `.github/workflows/build-calendar.yml` to change it:

```yaml
on:
  schedule:
    - cron: "17 */2 * * *"
  workflow_dispatch:
```

This ZIP is configured for the `master` branch. If you later rename your default branch to `main`, change the workflow branch filter.

## Notes

- `vlrdevapi` is a community VLR.gg library, not an official VLR.gg API.
- VLR match pages may occasionally lack exact times; this generator skips non-live matches without parseable datetimes unless `allow_date_only` is enabled.
- Calendar event `SUMMARY` uses the format: `<event> | <team1> vs <team2>`.
- Calendar event `DESCRIPTION` includes the VLR match URL and basic fixture details.
