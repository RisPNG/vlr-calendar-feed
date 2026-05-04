#!/usr/bin/env python3
"""Build static iCalendar feeds from VLR.gg data via vlrdevapi.

The generator intentionally keeps runtime dependencies tiny. Only `vlrdevapi`
is required for live builds; iCalendar serialization is implemented with the
Python standard library so GitHub Actions has fewer moving parts.
"""

from __future__ import annotations

import html
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    import vlrdevapi as vlr
except Exception:  # pragma: no cover - live dependency is not needed for unit tests
    vlr = None  # type: ignore[assignment]

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config" / "calendars.json"
PUBLIC_DIR = ROOT_DIR / "public"
VLR_BASE_URL = "https://www.vlr.gg"


@dataclass(frozen=True)
class CalendarSource:
    slug: str
    name: str
    description: str
    source_type: str
    enabled: bool
    team_id: int | None = None
    player_id: int | None = None
    team_aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedMatch:
    match_id: str
    event_name: str
    team1_name: str
    team2_name: str
    starts_at: datetime
    status: str
    url: str

    @property
    def summary(self) -> str:
        event = clean_text(self.event_name) or "VALORANT"
        team1 = clean_text(self.team1_name) or "TBD"
        team2 = clean_text(self.team2_name) or "TBD"
        return f"{event} | {team1} vs {team2}"


def clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return re.sub(r"\s+", " ", text) or fallback


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).lower())


def get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            value = obj[name]
            if value is not None:
                return value
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("config/calendars.json must contain a JSON object.")
    return data


def load_sources(config: dict[str, Any]) -> list[CalendarSource]:
    sources: list[CalendarSource] = []
    for item in config.get("calendars", []):
        if not isinstance(item, dict):
            continue
        slug = clean_slug(item.get("slug") or item.get("name") or "calendar")
        source_type = clean_text(item.get("type"), "team").lower()
        aliases = item.get("team_aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        if not isinstance(aliases, list):
            aliases = []
        sources.append(
            CalendarSource(
                slug=slug,
                name=clean_text(item.get("name"), slug),
                description=clean_text(item.get("description"), "VLR.gg match calendar"),
                source_type=source_type,
                enabled=bool(item.get("enabled", True)),
                team_id=parse_optional_int(item.get("team_id")),
                player_id=parse_optional_int(item.get("player_id")),
                team_aliases=[clean_text(alias) for alias in aliases if clean_text(alias)],
            )
        )
    return sources


def parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def clean_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", str(value).strip().lower()).strip("-")
    return slug or "calendar"


def ensure_vlr_available() -> None:
    if vlr is None:
        raise RuntimeError("vlrdevapi is not installed. Run: python -m pip install -r requirements.txt")


def fetch_matches_for_source(source: CalendarSource, settings: dict[str, Any]) -> list[Any]:
    ensure_vlr_available()
    timeout = settings.get("request_timeout_seconds")
    upcoming_limit = int(settings.get("upcoming_limit", 50))
    completed_limit = int(settings.get("completed_limit", 0))
    live_limit = int(settings.get("live_limit", 50))
    include_completed = bool(settings.get("include_completed", False))
    include_live = bool(settings.get("include_live", False))

    matches: list[Any] = []

    if source.source_type == "team":
        if source.team_id is None:
            raise ValueError(f"Calendar {source.slug!r} is type=team but has no team_id.")

        matches.extend(
            vlr.teams.upcoming_matches(
                team_id=source.team_id,
                limit=upcoming_limit,
                timeout=timeout,
            )
        )

        if include_live:
            live_matches = vlr.matches.live(limit=live_limit, timeout=timeout)
            matches.extend(filter_live_matches_for_team(live_matches, source))

        if include_completed and completed_limit > 0:
            matches.extend(
                vlr.teams.completed_matches(
                    team_id=source.team_id,
                    limit=completed_limit,
                    timeout=timeout,
                )
            )
        return matches

    if source.source_type == "player":
        if source.player_id is None:
            raise ValueError(f"Calendar {source.slug!r} is type=player but has no player_id.")

        profile = vlr.players.profile(player_id=source.player_id, timeout=timeout)
        team_ids = current_team_ids_from_profile(profile)
        seen_team_ids: set[int] = set()

        for team_id in team_ids:
            if team_id in seen_team_ids:
                continue
            seen_team_ids.add(team_id)
            team_source = CalendarSource(
                slug=source.slug,
                name=source.name,
                description=source.description,
                source_type="team",
                enabled=True,
                team_id=team_id,
                team_aliases=source.team_aliases,
            )
            matches.extend(vlr.teams.upcoming_matches(team_id=team_id, limit=upcoming_limit, timeout=timeout))
            if include_live:
                live_matches = vlr.matches.live(limit=live_limit, timeout=timeout)
                matches.extend(filter_live_matches_for_team(live_matches, team_source))

        if include_completed and completed_limit > 0:
            matches.extend(vlr.players.matches(player_id=source.player_id, limit=completed_limit, timeout=timeout))
        return matches

    if source.source_type == "global":
        matches.extend(vlr.matches.upcoming(limit=upcoming_limit, timeout=timeout))
        if include_live:
            matches.extend(vlr.matches.live(limit=live_limit, timeout=timeout))
        return matches

    raise ValueError(f"Unsupported calendar type for {source.slug!r}: {source.source_type!r}")


def filter_live_matches_for_team(live_matches: Iterable[Any], source: CalendarSource) -> list[Any]:
    return [match for match in live_matches if match_involves_source_team(match, source)]


def match_involves_source_team(match: Any, source: CalendarSource) -> bool:
    if source.team_id is None and not source.team_aliases:
        return False

    for team in iter_match_teams(match):
        team_id = parse_optional_int(get_attr(team, "id", "team_id", default=None))
        if source.team_id is not None and team_id == source.team_id:
            return True

        team_name = get_attr(team, "name", "tag", "team_name", default="")
        if team_name_matches_source(team_name, source):
            return True

    return False


def iter_match_teams(match: Any) -> list[Any]:
    teams = get_attr(match, "teams", default=None)
    if teams and isinstance(teams, (list, tuple, set)):
        return list(teams)
    return [get_attr(match, "team1", default=None), get_attr(match, "team2", default=None)]


def team_name_matches_source(team_name: Any, source: CalendarSource) -> bool:
    name = normalize_name(team_name)
    if not name:
        return False
    aliases = [*source.team_aliases]
    if source.name:
        aliases.append(source.name.replace(" VLR Matches", ""))
    return any(name == normalize_name(alias) for alias in aliases if normalize_name(alias))


def current_team_ids_from_profile(profile: Any) -> list[int]:
    ids: list[int] = []
    for attr_name in ("current_teams", "teams", "team"):
        value = get_attr(profile, attr_name)
        if not value:
            continue
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        for team in value:
            role = clean_text(get_attr(team, "role", default="")).lower()
            left_date = get_attr(team, "left_date", "end_date", default=None)
            team_id = parse_optional_int(get_attr(team, "id", "team_id", default=None))
            if team_id is None:
                continue
            if left_date is None and (not role or role in {"player", "active", "current"}):
                ids.append(team_id)
    return ids


def normalize_match(
    raw: Any,
    tz: ZoneInfo,
    allow_date_only: bool = False,
    fallback_live_start: datetime | None = None,
) -> NormalizedMatch | None:
    match_id = get_attr(raw, "match_id", "id", "series_id")
    if match_id in (None, ""):
        return None

    team1 = get_attr(raw, "team1", default=None)
    team2 = get_attr(raw, "team2", default=None)
    teams = get_attr(raw, "teams", default=None)
    if teams and isinstance(teams, (list, tuple)):
        team1 = team1 or (teams[0] if len(teams) >= 1 else None)
        team2 = team2 or (teams[1] if len(teams) >= 2 else None)

    team1_name = clean_text(get_attr(team1, "name", "team1_name", "tag", default=None), "TBD")
    team2_name = clean_text(get_attr(team2, "name", "team2_name", "tag", default=None), "TBD")

    event_name = clean_text(
        get_attr(raw, "event", "event_name", "tournament_name", "tournament", "event_phase", default=None),
        "VALORANT",
    )

    status = clean_text(get_attr(raw, "status", default="upcoming"), "upcoming").lower()
    starts_at = parse_match_datetime(raw, tz=tz, allow_date_only=allow_date_only)
    if starts_at is None and status == "live" and fallback_live_start is not None:
        starts_at = with_timezone(fallback_live_start, tz)
    if starts_at is None:
        return None

    url = make_vlr_url(raw, str(match_id))

    return NormalizedMatch(
        match_id=str(match_id),
        event_name=event_name,
        team1_name=team1_name,
        team2_name=team2_name,
        starts_at=starts_at,
        status=status,
        url=url,
    )


def parse_match_datetime(raw: Any, tz: ZoneInfo, allow_date_only: bool = False) -> datetime | None:
    value = get_attr(raw, "match_datetime", "datetime", "start_time", "starts_at", default=None)
    if isinstance(value, datetime):
        return with_timezone(value, tz)

    raw_date = get_attr(raw, "date", default=None)
    raw_time = get_attr(raw, "time", default=None)

    if isinstance(raw_date, datetime):
        return with_timezone(raw_date, tz)

    if isinstance(raw_date, date) and raw_time:
        parsed_time = parse_time_value(raw_time)
        if parsed_time is not None:
            return datetime.combine(raw_date, parsed_time, tzinfo=tz)

    candidates = []
    if value:
        candidates.append(str(value))
    if raw_date and raw_time:
        candidates.append(f"{raw_date} {raw_time}")
    if raw_date:
        candidates.append(str(raw_date))

    for candidate in candidates:
        parsed = parse_datetime_string(candidate, tz=tz)
        if parsed is None:
            continue
        if not allow_date_only and parsed.time() == time(0, 0) and not re.search(
            r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)", candidate, re.I
        ):
            continue
        return parsed

    return None


def parse_time_value(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    text = clean_text(value)
    if not text or text.lower() in {"tbd", "live", "completed"}:
        return None

    formats = ("%H:%M", "%I:%M %p", "%I%p", "%H%M")
    for fmt in formats:
        try:
            return datetime.strptime(text.upper(), fmt).time()
        except ValueError:
            continue
    return None


def parse_datetime_string(value: str, tz: ZoneInfo) -> datetime | None:
    text = clean_text(value)
    if not text or text.lower() in {"tbd", "live", "completed"}:
        return None

    try:
        return with_timezone(datetime.fromisoformat(text.replace("Z", "+00:00")), tz)
    except ValueError:
        pass

    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d",
        "%b %d %Y %H:%M",
        "%b %d %Y %I:%M %p",
        "%B %d %Y %H:%M",
        "%B %d %Y %I:%M %p",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def with_timezone(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def make_vlr_url(raw: Any, match_id: str) -> str:
    url = clean_text(get_attr(raw, "url", "match_url", "link", default=""))
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{VLR_BASE_URL}{url}"
    return f"{VLR_BASE_URL}/{match_id}"


def dedupe_matches(matches: Iterable[NormalizedMatch]) -> list[NormalizedMatch]:
    seen: dict[str, NormalizedMatch] = {}
    for match in matches:
        seen[match.match_id] = match
    return sorted(seen.values(), key=lambda match: (match.starts_at, match.match_id))


def build_ical_calendar(
    source: CalendarSource,
    matches: list[NormalizedMatch],
    settings: dict[str, Any],
) -> bytes:
    duration = timedelta(minutes=int(settings.get("default_match_duration_minutes", 120)))
    ttl_hours = int(settings.get("published_ttl_hours", 2))
    generated_at = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//vlr-calendar-feed//github-pages//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_text(source.name)}",
        f"X-WR-CALDESC:{ics_text(source.description)}",
        f"X-PUBLISHED-TTL:PT{ttl_hours}H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT2H",
    ]

    for match in matches:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{ics_text(f'vlr-match-{match.match_id}@vlr-calendar-feed')}",
                f"DTSTAMP:{format_ics_datetime(generated_at)}",
                f"DTSTART:{format_ics_datetime(match.starts_at.astimezone(timezone.utc))}",
                f"DTEND:{format_ics_datetime((match.starts_at + duration).astimezone(timezone.utc))}",
                f"SUMMARY:{ics_text(match.summary)}",
                f"DESCRIPTION:{ics_text(build_description(match))}",
                f"URL:{ics_text(match.url)}",
                f"CATEGORIES:{ics_text('VALORANT,VLR.gg,' + match.status)}",
                f"STATUS:{'CONFIRMED' if match.status != 'cancelled' else 'CANCELLED'}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold_ics_line(line) for line in lines) + "\r\n").encode("utf-8")


def format_ics_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ics_text(value: Any) -> str:
    text = clean_text(value)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", r"\;")
    text = text.replace(",", r"\,")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
    return text


def fold_ics_line(line: str, limit: int = 75) -> str:
    if len(line.encode("utf-8")) <= limit:
        return line
    output: list[str] = []
    current = ""
    for char in line:
        test = current + char
        allowed = limit if not output else limit - 1
        if len(test.encode("utf-8")) > allowed:
            output.append(current if not output else " " + current)
            current = char
        else:
            current = test
    if current:
        output.append(current if not output else " " + current)
    return "\r\n".join(output)


def build_description(match: NormalizedMatch) -> str:
    return "\n".join(
        [
            match.url,
            "",
            f"Event: {match.event_name}",
            f"Match: {match.team1_name} vs {match.team2_name}",
            f"Status: {match.status}",
            "Source: VLR.gg",
        ]
    )


def write_index(config: dict[str, Any], built_calendars: list[dict[str, Any]]) -> None:
    site = config.get("site", {}) if isinstance(config.get("site"), dict) else {}
    title = clean_text(site.get("title"), "VLR Calendar Feeds")
    description = clean_text(site.get("description"), "Public Valorant match calendars generated from VLR.gg.")
    base_url = clean_text(site.get("base_url"), "").rstrip("/")
    generated_at = datetime.now(timezone.utc).isoformat()

    links = []
    for calendar in built_calendars:
        href = f"{calendar['slug']}.ics"
        absolute = f"{base_url}/{href}" if base_url else href
        links.append(
            f"""
            <article class=\"card\">
              <h2>{html.escape(calendar['name'])}</h2>
              <p>{html.escape(calendar['description'])}</p>
              <p><strong>{calendar['match_count']}</strong> events generated.</p>
              <div class=\"actions\">
                <a href=\"{html.escape(href)}\">Download ICS</a>
                <button data-url=\"{html.escape(absolute)}\">Copy subscription URL</button>
              </div>
              <code>{html.escape(absolute)}</code>
            </article>
            """
        )

    html_doc = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; }}
    body {{ margin: 0; min-height: 100vh; background: #101114; color: #f5f5f5; }}
    main {{ width: min(920px, calc(100% - 32px)); margin: 0 auto; padding: 64px 0; }}
    h1 {{ font-size: clamp(2rem, 5vw, 4rem); line-height: 1; margin: 0 0 16px; }}
    .lede {{ color: #c7c7c7; font-size: 1.1rem; margin-bottom: 32px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
    .card {{ border: 1px solid #2c2e36; background: #181a20; border-radius: 20px; padding: 20px; box-shadow: 0 20px 60px rgba(0,0,0,.22); }}
    .card h2 {{ margin: 0 0 8px; }}
    .card p {{ color: #d0d0d0; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }}
    a, button {{ border: 0; border-radius: 999px; background: #77dd77; color: #051405; padding: 10px 14px; font-weight: 700; text-decoration: none; cursor: pointer; }}
    button {{ font: inherit; }}
    code {{ display: block; overflow-x: auto; color: #b7ffb7; background: #0b0c0f; border-radius: 12px; padding: 10px; }}
    footer {{ margin-top: 32px; color: #9c9c9c; font-size: .9rem; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p class=\"lede\">{html.escape(description)}</p>
    <section class=\"grid\">
      {''.join(links) if links else '<p>No enabled calendars were generated.</p>'}
    </section>
    <footer>Generated at {html.escape(generated_at)}.</footer>
  </main>
  <script>
    for (const button of document.querySelectorAll('button[data-url]')) {{
      button.addEventListener('click', async () => {{
        await navigator.clipboard.writeText(button.dataset.url);
        button.textContent = 'Copied';
        setTimeout(() => button.textContent = 'Copy subscription URL', 1500);
      }});
    }}
  </script>
</body>
</html>
"""

    (PUBLIC_DIR / "index.html").write_text(html_doc, encoding="utf-8")
    (PUBLIC_DIR / "feeds.json").write_text(json.dumps(built_calendars, indent=2), encoding="utf-8")


def write_nojekyll() -> None:
    (PUBLIC_DIR / ".nojekyll").write_text("", encoding="utf-8")


def build() -> int:
    config = load_config()
    settings = config.get("settings", {}) if isinstance(config.get("settings"), dict) else {}
    tz = ZoneInfo(clean_text(settings.get("timezone"), "UTC"))
    allow_date_only = bool(settings.get("allow_date_only", False))
    live_start_fallback = clean_text(settings.get("live_match_start_fallback"), "now").lower()
    fallback_live_start = datetime.now(tz) if live_start_fallback == "now" else None
    sources = [source for source in load_sources(config) if source.enabled]

    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    built_calendars: list[dict[str, Any]] = []

    for source in sources:
        print(f"Building {source.slug} ({source.source_type})...")
        raw_matches = fetch_matches_for_source(source, settings)
        normalized = [
            match
            for raw in raw_matches
            if (
                match := normalize_match(
                    raw,
                    tz=tz,
                    allow_date_only=allow_date_only,
                    fallback_live_start=fallback_live_start,
                )
            )
            is not None
        ]
        matches = dedupe_matches(normalized)
        ics_bytes = build_ical_calendar(source, matches, settings)
        output_path = PUBLIC_DIR / f"{source.slug}.ics"
        output_path.write_bytes(ics_bytes)
        built_calendars.append(
            {
                "slug": source.slug,
                "name": source.name,
                "description": source.description,
                "type": source.source_type,
                "match_count": len(matches),
                "file": f"{source.slug}.ics",
            }
        )
        print(f"  Wrote {output_path.relative_to(ROOT_DIR)} with {len(matches)} events.")

    write_index(config, built_calendars)
    write_nojekyll()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(build())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
