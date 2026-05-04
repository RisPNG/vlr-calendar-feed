# VLR Calendar Feed

Generate public `.ics` calendar feeds from VLR.gg match data using [`vlrdevapi`](https://vlrdevapi.readthedocs.io/), then deploy them to GitHub Pages with a scheduled GitHub Actions workflow.

This is designed for fixture calendars, not score tracking. Calendar titles look like:

```text
VCT Pacific | PRX vs DRX
```

Each event description contains the VLR.gg match link.

## What this repo gives you

- Scheduled GitHub Actions sync.
- Static GitHub Pages deployment.
- One `.ics` file per configured calendar.
- A simple `index.html` landing page with subscription links.
- Team calendars using `vlr.teams.upcoming_matches()`.
- Optional completed-match inclusion.
- Experimental player calendars by resolving the player's current VLR team(s).
- Stable iCalendar `UID`s based on VLR match IDs to reduce duplicate events.

## Quick deploy

1. Create a new GitHub repository.
2. Upload/push this repo.
3. Edit `config/calendars.json`.
4. Go to **Settings → Pages → Build and deployment → Source → GitHub Actions**.
5. Go to **Actions → Build and deploy calendar feeds → Run workflow**.
6. Your calendar feed will be available at:

```text
https://<your-github-username>.github.io/<repo-name>/<calendar-slug>.ics
```

For example:

```text
https://ris.github.io/vlr-calendar-feed/paper-rex.ics
```

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
    "completed_limit": 0,
    "include_completed": false,
    "request_timeout_seconds": 20,
    "allow_date_only": false
  },
  "calendars": [
    {
      "slug": "paper-rex",
      "name": "Paper Rex VLR Matches",
      "type": "team",
      "team_id": 624,
      "enabled": true
    }
  ]
}
```

### Team calendar

```json
{
  "slug": "sentinels",
  "name": "Sentinels VLR Matches",
  "type": "team",
  "team_id": 2,
  "enabled": true
}
```

### Player calendar

Player calendars are best-effort. `vlrdevapi.players.matches()` covers match history, while upcoming player fixtures are inferred from current team membership.

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

The workflow runs every 6 hours by default at minute 17 to avoid the top-of-hour load spike. Edit `.github/workflows/build-calendar.yml` to change it:

```yaml
on:
  schedule:
    - cron: "17 */6 * * *"
  workflow_dispatch:
```

GitHub scheduled workflows use cron and can also be triggered manually with `workflow_dispatch`.

## Notes

- `vlrdevapi` is a community VLR.gg library, not an official VLR.gg API.
- VLR match pages may occasionally lack exact times; this generator skips matches without parseable datetimes unless `allow_date_only` is enabled.
- Calendar event `SUMMARY` uses the format: `<event> | <team1> vs <team2>`.
- Calendar event `DESCRIPTION` includes the VLR match URL and basic fixture details.
