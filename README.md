# VLR Calendar Feed

Generate public `.ics` calendar feeds from VLR.gg match data using `vlrdevapi`, then deploy them to GitHub Pages with scheduled GitHub Actions.

This repo is designed for fixture calendars. Event titles look like:

```text
VCT Pacific | PRX vs DRX
```

Each event description contains the VLR.gg match link.

## Current config

The included `config/calendars.json` builds:

- `paper-rex.ics`
- `thedoctorr.ics`
- `ayumiii.ics`

It includes upcoming, live, and completed matches. Player completed matches are normalized from `player_team` and `opponent_team`, so they should not render as `TBD vs TBD` when the data has team names.

## Deploy

1. Push this repo to GitHub on `master`.
2. Go to **Settings -> Pages -> Build and deployment -> Source**.
3. Select **GitHub Actions**.
4. Go to **Actions -> Build and deploy calendar feeds -> Run workflow**.

The workflow runs every 2 hours:

```yaml
- cron: "17 */2 * * *"
```

GitHub cron uses UTC.

## Subscribe

Use the GitHub Pages URL, not the GitHub repo URL:

```text
https://rispng.github.io/vlr-calendar-feed/ayumiii.ics
```

In Google Calendar desktop:

```text
Other calendars -> + -> From URL -> paste the .ics URL -> Add calendar
```

Google Calendar decides when to refresh subscribed feeds, so changes may not appear instantly even after GitHub Pages is updated.

## Configure calendars

Edit `config/calendars.json`.

Team calendar:

```json
{
  "slug": "paper-rex",
  "name": "Paper Rex VLR Matches",
  "description": "All Paper Rex Valorant matches from VLR.gg.",
  "type": "team",
  "team_id": 624,
  "team_aliases": ["Paper Rex", "PRX"],
  "enabled": true
}
```

Player calendar:

```json
{
  "slug": "ayumiii",
  "name": "Ayumiii VLR Matches",
  "description": "All Ayumiii Valorant matches from VLR.gg.",
  "type": "player",
  "player_id": 8175,
  "enabled": true
}
```

## Find IDs

```bash
python -m pip install -r requirements.txt
python scripts/find_ids.py "paper rex" --type team
python scripts/find_ids.py "ayumiii" --type player
```

You can also get IDs directly from VLR.gg URLs, for example `/team/624/paper-rex` or `/player/8175/...`.

## Local development

```bash
python -m pip install -r requirements.txt
python scripts/build_calendar.py
python -m pytest
```

## Notes

- `slug` controls the public file name and URL.
- Keep a slug stable after people subscribe.
- The generator uses stable `UID`s based on VLR match IDs so updated titles replace old events when calendar clients refresh.
- `vlrdevapi` is a community library, so occasional VLR markup changes may require parser updates.
