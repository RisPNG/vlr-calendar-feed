# VLR Calendar Feed

Follow Valorant teams, players, and competitions in your calendar app. Match schedules and results come from VLR.gg.

## Subscribe

1. Open the calendar website and choose a calendar.
2. Click **Copy subscription URL**.
3. In Google Calendar, select **Other calendars → + → From URL**, paste the link, and add the calendar. Other calendar apps usually have a similar subscription option.

A subscription URL looks like this:

```text
https://calendar.example.com/paper-rex.ics
```

Subscribed calendars update automatically. Your calendar app controls how often it checks for changes.

## Setup

To run your own calendar website:

1. Download or clone this repository.
2. Choose your calendars in [config/calendars.json](config/calendars.json).
3. Follow [Local use](#local-use) to start the website.

For a public website, use an always-on Python host with persistent storage. The [hosting guide](dev/README.md#deployment) explains the setup.

## Calendar configuration

Edit [config/calendars.json](config/calendars.json) to choose teams, players, events, or competition series. Set an entry's `enabled` value to `true` to show it or `false` to hide it.

For example, a team calendar looks like this:

```json
{
  "slug": "paper-rex",
  "name": "Paper Rex matches",
  "description": "Paper Rex match schedule.",
  "type": "team",
  "team_id": 624,
  "enabled": true
}
```

The `slug` becomes the calendar's filename, such as `paper-rex.ics`. Find the team ID in its VLR URL: `/team/624/paper-rex` uses `624`. See [more calendar options](dev/README.md#calendar-configuration) for players, events, and series.

## Local use

Use Python 3.14.7 with mise. From the project folder, run:

```bash
mise exec -- python -m venv .venv
mise exec -- .venv/bin/python -m pip install -r requirements.txt
mise exec -- .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open [localhost:8000](http://127.0.0.1:8000/) and allow a few minutes for the first calendars to appear.

## Subscription caching

Google Calendar may continue showing a cached subscription after the site updates. To create a new subscription URL, add a query parameter:

```text
https://calendar.example.com/vct-2026.ics?v=2
```

This creates a different subscription URL. It does not control Google's refresh schedule.
