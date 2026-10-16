# VLR Calendar Feed

Generate public `.ics` feeds from VLR.gg through [vlrggapi.rispeng.com](https://vlrggapi.rispeng.com) and publish them with GitHub Pages.

Very wonky [video tutorial](https://f005.backblazeb2.com/file/RisPNG/vlr-calendar-feed-tut.mp4).

The repository supports these calendar sources:

- `team`: a team's upcoming, live, and completed matches.
- `player`: a player's completed match history plus upcoming and live matches from their current team.
- `event`: matches from one VLR event ID.
- `series`: matches from events linked from a VLR series page, such as VCT 2026 or Game Changers 2026.
- `global`: global upcoming, live, and completed match listings.

## Subscribe or deploy

The published feeds are available at [rispng.github.io/vlr-calendar-feed](https://rispng.github.io/vlr-calendar-feed/). Copy a subscription URL into Google Calendar using **Other calendars → + → From URL**. For example:

```text
https://rispng.github.io/vlr-calendar-feed/paper-rex.ics
```

To publish your own feeds:

1. Push this repository to GitHub.
2. Open **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Edit `config/calendars.json`, including `site.base_url` for your Pages address and `settings.api_base_url` for the API you use.
4. Run **Actions → Build and deploy calendar feeds → Run workflow**. Re-enable the workflow if GitHub has disabled it after repository inactivity.
5. Subscribe to the generated `.ics` URL.

No API key or repository secret is required for the configured public API. If you use an authenticated API, set the optional `VLR_API_KEY` repository secret for GitHub Actions, or the environment variable of the same name for local commands. The client sends it as a bearer token. `VLR_API_BASE_URL` can override the configured API address locally.

## Refresh and publication

The workflow polls every two hours at minute 17 UTC. GitHub may delay scheduled runs, and calendar applications refresh subscriptions on their own schedules. `published_ttl_hours` is a refresh hint for those applications.

Each run fetches current listings for the configured sources and shares API responses across calendars. Recent history is checked more frequently. Older history pages, event listings, and match details are rechecked in rotating batches, including completed matches. An unchanged listing does not prevent a later detail refresh, and records are not permanently excluded because they are old.

Every previously unseen event listing is fetched when discovered, including during the first build. The historical event budget controls subsequent rereads of completed events within each series. Current events and explicitly configured single-event calendars are checked each run. Match-detail enrichment uses its separate shared budget from the first build onwards. Historical pagination is incremental, so a fresh build can accumulate older matches over subsequent runs until it reaches the source's end or the configured depth.

The configured `detail_refresh_per_run` budget is `24`. Half is reserved for older completed matches, so at least `12` older details can be revisited per successful run while recent details use the remaining slots. Unused recent slots can also refresh older details. For a stable cohort of `N` older matches, one detail sweep takes roughly `2 × ceil(N / 12)` hours at the scheduled cadence. For example, 1,200 older matches can take about 200 hours, or eight days and eight hours. Failed or delayed runs and newly discovered records can extend that time. A detail-only correction is therefore not guaranteed to appear within two hours. Increasing the batch sizes reduces the delay and increases API and VLR requests.

The generator compares the resulting calendars and index with the published site. It preserves event UIDs, timestamps, and revision numbers when event content is unchanged, and publishes only when the feed or index content changes. A correction to an existing match updates that event. Matches without a known start time are omitted until a usable time is available.

Polling state is stored separately in the GitHub Actions cache, including after runs that publish nothing. The previous published site remains the comparison baseline, so a failed deployment can be retried. Event revisions are reconciled with both the published feed and the cached candidate, preserving the newer revision after a failed deployment or a lost cache save. If the cache is missing, completed entries are recovered from the published feeds while history pages are rediscovered. Those recovered records remain eligible for detail revalidation. If the API or baseline download fails, the workflow fails and the existing site remains published.

A completed entry missing from a partial history window is retained. It can be removed after a complete sweep reaches the source's actual end and confirms its absence with a check at least as recent as the stored event revision. Reaching the configured pagination cap alone does not confirm absence. Corrected match details can also remove an entry from a team calendar when neither team matches the configured team. The build job has a 30-minute timeout, and a failed or timed-out build leaves the published site intact.

## Calendar configuration

Edit the `calendars` array in `config/calendars.json`. Each entry has a unique `slug`, a `name`, a `description`, a `type`, and an `enabled` flag. The slug becomes the feed filename.

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
| `published_ttl_hours` | Suggested subscription refresh interval, currently `2` hours. The workflow schedule is configured separately. |
| `default_match_duration_minutes` | Fallback event duration, currently `120` minutes. |
| `infer_match_duration_from_best_of` | Use a known match format to estimate duration. |
| `best_of_duration_minutes` | Format-to-duration mapping, currently BO1 = 60, BO3 = 120, and BO5 = 180 minutes. |

Durations are estimates because scheduled match listings do not provide an end time.

## Local use

Use Python 3.11 or later. HTTP requests use the Python standard library, and `icalendar` reads and serialises calendar feeds. The requirements file also installs pytest for the tests.

```bash
mise exec -- python -m pip install -r requirements.txt
mise exec -- python -m pytest -q
mise exec -- python scripts/build_calendar.py --state-file .calendar-state/state.json --output-dir public
```

To compare with an existing site snapshot, pass `--previous-dir /path/to/previous`. Keep the state file between runs to continue historical revalidation. The workflow restores the published site automatically.

## Subscription caching

Google Calendar may continue showing a cached subscription after the site updates. To create a new subscription URL, add a query parameter:

```text
https://rispng.github.io/vlr-calendar-feed/vct-2026.ics?v=2
```

This creates a different subscription URL. It does not control Google's refresh schedule.
