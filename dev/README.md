# Development and hosting

See the [main README](../README.md) for subscriptions and a quick local start. Run the commands below from the repository root. Python 3.14.7 is pinned in [.python-version](../.python-version).

## Deployment

Use an always-on Python host with a writable directory that survives restarts and deployments.

1. Install the production dependencies from [requirements.txt](../requirements.txt) with `python -m pip install -r requirements.txt`.
2. Set `CALENDAR_DATA_DIR` to the persistent storage directory, for example `/var/data`.
3. Set `PORT` to the port assigned by your host and start the service:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
```

Keep one instance and one Uvicorn worker because the application owns its background poller and local state. Generated feeds and polling state live under `CALENDAR_DATA_DIR`. Without an explicit value, local runs use `.calendar-state/service` inside the repository. No PostgreSQL, Redis, or separate worker service is required.

The page builds subscription URLs from the address opened in the browser, so custom domains do not require an additional application environment variable.

No API key is required for the configured public API. If you use an authenticated API, set the optional `VLR_API_KEY` environment variable. The client sends it as a bearer token. `VLR_API_BASE_URL` can override the configured API address.

The first calendar build runs after startup. `/health` returns process liveness, snapshot readiness, and the last successful refresh. It returns HTTP 200 even when no snapshot is ready, so inspect the response body for readiness. The calendar index returns `503` until the first successful snapshot is available. After that, the index, `/feeds.json`, and each `/<slug>.ics` URL serve the most recent successful snapshot while later builds run or fail.

## Render

Create a Python web service from this repository, or review and apply [render.yaml](../render.yaml) as a Render Blueprint. The supplied configuration uses one always-on instance and a 1 GB persistent disk so polling continues between subscription requests and historical state survives restarts.

| Setting | Value |
| --- | --- |
| Service name | `vlr-gg-ics` |
| Region | Singapore |
| Branch | `master` |
| Runtime | Python |
| Python version | `PYTHON_VERSION=3.14.7` |
| Compute plan | `0.5c-512mb` |
| Instances | `1` |
| Build command | `python -m pip install -r requirements.txt` |
| Start command | `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/health` |
| Auto-deploy | On commit |
| Persistent disk | 1 GB mounted at `/var/data` |
| Data directory | `CALENDAR_DATA_DIR=/var/data` |

Persistent disks are available only on paid Render services, and only files under the mount path survive restarts. Render also stops the existing disk-backed instance before starting its replacement during a deploy. See [Render's persistent disk documentation](https://render.com/docs/disks) for these hosting constraints.

## Refresh and publication

The service starts a poll on startup and refreshes every two hours while it is running. A failed refresh is retried after five minutes, with the previous successful snapshot still available. Calendar applications refresh subscriptions on their own schedules. `published_ttl_hours` is a refresh hint for those applications.

Each run fetches current listings for the configured sources and shares API responses across calendars. Recent history is checked more frequently. Older history pages, event listings, and match details are rechecked in rotating batches, including completed matches. An unchanged listing does not prevent a later detail refresh, and records are not permanently excluded because they are old.

Every previously unseen event listing is fetched when discovered, including during the first build. The historical event budget controls subsequent rereads of completed events within each series. Current events and explicitly configured single-event calendars are checked each run. Match-detail enrichment uses its separate shared budget from the first build onwards. Historical pagination is incremental, so a fresh build can accumulate older matches over subsequent runs until it reaches the source's end or the configured depth.

The configured `detail_refresh_per_run` budget is `24`. Half is reserved for older completed matches, so at least `12` older details can be revisited per successful run while recent details use the remaining slots. Unused recent slots can also refresh older details. For a stable cohort of `N` older matches, one detail sweep takes roughly `2 × ceil(N / 12)` hours at the scheduled cadence. For example, 1,200 older matches can take about 200 hours, or eight days and eight hours. Failed or delayed runs and newly discovered records can extend that time. A detail-only correction is therefore not guaranteed to appear within two hours. Increasing the batch sizes reduces the delay and increases API and VLR requests.

The generator compares the resulting calendars and index with the current local snapshot. It preserves event UIDs, timestamps, and revision numbers when event content is unchanged, and replaces the public snapshot only when the feed or index content changes. A correction to an existing match updates that event. Matches without a known start time are omitted until a usable time is available.

Polling state is stored separately from public files under the data directory and is updated even when the snapshot does not change. If state is missing but a snapshot remains, completed entries are recovered from those feeds while history pages are rediscovered. Those recovered records remain eligible for detail revalidation. The service switches snapshots after a successful build and retains the current and previous generations. An API or build failure leaves the last successful snapshot available.

A completed entry missing from a partial history window is retained. It can be removed after a complete sweep reaches the source's actual end and confirms its absence with a check at least as recent as the stored event revision. Reaching the configured pagination cap alone does not confirm absence. Corrected match details can also remove an entry from a team calendar when neither team matches the configured team.

## Calendar configuration

Edit the `calendars` array in [config/calendars.json](../config/calendars.json). Each entry has a unique `slug`, a `name`, a `description`, a `type`, and an `enabled` flag. The slug becomes the feed filename.

| Type | Matches included | Identifier |
| --- | --- | --- |
| `team` | A team's upcoming, live, and completed matches. | `team_id` |
| `player` | A player's history and upcoming or live matches from their current teams. | `player_id` |
| `event` | Matches from one or more events. | `event_id` or `event_ids` |
| `series` | Matches from every event in a competition series. | `series_id` |
| `global` | Global upcoming, live, and completed listings. | None |

Use the numeric ID from the corresponding VLR URL. For example, `/team/624/paper-rex` gives `team_id: 624`, and `/player/8175/ayumiii` gives `player_id: 8175`. You can also search through the configured API:

```bash
mise exec -- python scripts/find_ids.py "paper rex" --type team
mise exec -- python scripts/find_ids.py "ayumiii" --type player
```

For a series such as [VCT 2026](https://www.vlr.gg/series/86/valorant-champions-tour-2026), use:

```json
{
  "slug": "vct-2026",
  "name": "Valorant Champions Tour 2026",
  "description": "All VCT 2026 matches from VLR.gg.",
  "type": "series",
  "series_id": 86,
  "enabled": true
}
```

The API discovers events linked from the series page, and the generator fetches their matches through the API. Optional `event_name_include` and `event_name_exclude` arrays filter those events by name substring. For example, `"event_name_include": ["Pacific"]` selects Pacific events, while `"event_name_exclude": ["Kickoff"]` excludes Kickoff events.

For a single [VCT 2026 Pacific Stage 1 event](https://www.vlr.gg/event/2775/vct-2026-pacific-stage-1), use:

```json
{
  "slug": "vct-2026-pacific-stage-1",
  "name": "VCT 2026: Pacific Stage 1",
  "description": "All VCT 2026: Pacific Stage 1 matches.",
  "type": "event",
  "event_id": 2775,
  "enabled": true
}
```

## Settings

| Setting | Purpose |
| --- | --- |
| `api_base_url` | API origin, currently `https://vlrggapi.rispeng.com`. |
| `poll_interval_seconds` | Time between successful poll starts, currently `7200` seconds. |
| `retry_interval_seconds` | Wait after a failed build before retrying, currently `300` seconds. |
| `build_timeout_seconds` | Maximum build runtime, currently `1800` seconds. A timed-out build leaves the current snapshot available. |
| `timezone` | Calendar display timezone, currently `Asia/Kuala_Lumpur`. API timestamps identify the actual instant independently of this setting. |
| `request_timeout_seconds` | Per-request timeout. The configured `120` seconds allows for an API cold start. |
| `request_interval_seconds` | Minimum spacing between requests. The configured one-second interval is a courtesy setting, not a verified VLR quota. |
| `recent_history_days` | History window checked more frequently, currently `7` days. Older records remain in the rotation. |
| `detail_refresh_per_run` | Maximum shared match-detail requests per run, currently `24`, covering initial enrichment and later revalidation. |
| `historical_event_checks_per_run` | Completed event listings rechecked per series per run, currently `5`. Previously unseen event listings are always fetched. |
| `history_pages_per_run` | Additional historical pages per source per run, currently `1`. Page 1 is always checked. |
| `history_max_pages` | Maximum pagination depth, currently `50` pages. This bounds discovery depth rather than excluding matches by age. |
| `upcoming_limit`, `live_limit`, `completed_limit` | Maximum matches accepted from the corresponding listings. |
| `event_match_limit`, `series_event_limit` | Maximum matches per event and events per series. |
| `include_completed`, `include_live` | Include completed and live matches. Both are enabled in the supplied configuration. |
| `allow_date_only` | Allow a date without a known time to become a midnight event. Disabled by default. |
| `published_ttl_hours` | Suggested subscription refresh interval, currently `2` hours. The service's polling interval is configured separately. |
| `default_match_duration_minutes` | Fallback event duration, currently `120` minutes. |
| `infer_match_duration_from_best_of` | Use a known match format to estimate duration. |
| `best_of_duration_minutes` | Format-to-duration mapping, currently BO1 = 60, BO3 = 120, and BO5 = 180 minutes. |

Durations are estimates because scheduled match listings do not provide an end time.

## Development

Install the development dependencies and run the tests:

```bash
mise exec -- python -m venv .venv
mise exec -- .venv/bin/python -m pip install -r requirements-dev.txt
mise exec -- .venv/bin/python -m pytest -q
```

FastAPI and Uvicorn run the service, HTTP requests use the Python standard library, and `icalendar` reads and serialises calendar feeds. Development dependencies are separate from the production installation.

For a one-off build:

```bash
mise exec -- .venv/bin/python scripts/build_calendar.py --output-dir public --state-file .calendar-state/state.json
```

Pass `--previous-dir /path/to/previous` to compare with another snapshot. Keep the state file and published files between runs to continue historical revalidation and preserve event revisions.
