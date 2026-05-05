#!/usr/bin/env python3
"""Build static iCalendar feeds from VLR.gg data via vlrdevapi.

Supported calendar source types:
- team: matches for one VLR team ID
- player: completed player history plus upcoming/live matches from current teams
- event: all matches for one VLR event ID
- series: all matches for every event linked from one VLR /series/<id>/ page
- global: global upcoming/live/completed match feeds where supported
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.error
import urllib.request
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
    event_id: int | None = None
    event_ids: list[int] = field(default_factory=list)
    series_id: int | None = None
    series_slug: str = ""
    series_url: str = ""
    stage: str | None = None
    team_aliases: list[str] = field(default_factory=list)
    event_name_include: list[str] = field(default_factory=list)
    event_name_exclude: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SeriesEvent:
    event_id: int
    name: str
    url: str


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

        aliases = as_clean_list(item.get("team_aliases", []))
        event_name_include = as_clean_list(
            item.get("event_name_include", item.get("include_event_names", []))
        )
        event_name_exclude = as_clean_list(
            item.get("event_name_exclude", item.get("exclude_event_names", []))
        )

        sources.append(
            CalendarSource(
                slug=slug,
                name=clean_text(item.get("name"), slug),
                description=clean_text(item.get("description"), "VLR.gg match calendar"),
                source_type=source_type,
                enabled=bool(item.get("enabled", True)),
                team_id=parse_optional_int(item.get("team_id")),
                player_id=parse_optional_int(item.get("player_id")),
                event_id=parse_optional_int(item.get("event_id")),
                event_ids=parse_int_list(item.get("event_ids", [])),
                series_id=parse_optional_int(item.get("series_id")),
                series_slug=clean_text(item.get("series_slug"), ""),
                series_url=clean_text(item.get("series_url"), ""),
                stage=clean_text(item.get("stage"), "") or None,
                team_aliases=aliases,
                event_name_include=event_name_include,
                event_name_exclude=event_name_exclude,
            )
        )

    return sources


def as_clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [clean_text(item) for item in value if clean_text(item)]


def parse_int_list(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    output: list[int] = []
    for item in value:
        parsed = parse_optional_int(item)
        if parsed is not None:
            output.append(parsed)
    return output


def parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    event_match_limit = int(settings.get("event_match_limit", max(upcoming_limit, completed_limit, 50)))
    include_completed = bool(settings.get("include_completed", False))
    include_live = bool(settings.get("include_live", False))

    matches: list[Any] = []

    if source.source_type == "team":
        if source.team_id is None:
            raise ValueError(f"Calendar {source.slug!r} is type=team but has no team_id.")

        matches.extend(
            vlr.teams.upcoming_matches(team_id=source.team_id, limit=upcoming_limit, timeout=timeout)
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
        live_matches_cache: list[Any] | None = None

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

            matches.extend(
                vlr.teams.upcoming_matches(team_id=team_id, limit=upcoming_limit, timeout=timeout)
            )

            if include_live:
                if live_matches_cache is None:
                    live_matches_cache = vlr.matches.live(limit=live_limit, timeout=timeout)
                matches.extend(filter_live_matches_for_team(live_matches_cache, team_source))

        if include_completed and completed_limit > 0:
            matches.extend(
                vlr.players.matches(player_id=source.player_id, limit=completed_limit, timeout=timeout)
            )
        return matches

    if source.source_type == "event":
        event_ids = source.event_ids or ([source.event_id] if source.event_id is not None else [])
        if not event_ids:
            raise ValueError(f"Calendar {source.slug!r} is type=event but has no event_id or event_ids.")
        for event_id in event_ids:
            matches.extend(fetch_event_matches(event_id, source, settings, limit=event_match_limit))
        return matches

    if source.source_type == "series":
        events = discover_series_events(source, settings)
        if not events:
            raise ValueError(f"Calendar {source.slug!r} found no events on its VLR series page.")

        for event in events:
            print(f"  Fetching event {event.event_id}: {event.name}")
            event_matches = fetch_event_matches(
                event.event_id,
                source,
                settings,
                limit=event_match_limit,
                fallback_event_name=event.name,
            )
            matches.extend(event_matches)
        return matches

    if source.source_type == "global":
        matches.extend(vlr.matches.upcoming(limit=upcoming_limit, timeout=timeout))
        if include_live:
            matches.extend(vlr.matches.live(limit=live_limit, timeout=timeout))
        if include_completed and completed_limit > 0 and hasattr(vlr.matches, "completed"):
            matches.extend(vlr.matches.completed(limit=completed_limit, timeout=timeout))
        return matches

    raise ValueError(f"Unsupported calendar type for {source.slug!r}: {source.source_type!r}")


def fetch_event_matches(
    event_id: int,
    source: CalendarSource,
    settings: dict[str, Any],
    limit: int | None,
    fallback_event_name: str | None = None,
) -> list[Any]:
    timeout = settings.get("request_timeout_seconds")
    stage = source.stage
    event_name = fallback_event_name or event_name_from_api(event_id, timeout=timeout)

    kwargs: dict[str, Any] = {"event_id": event_id, "limit": limit, "timeout": timeout}
    if stage:
        kwargs["stage"] = stage

    matches = vlr.events.matches(**kwargs)
    return [annotate_match(match, event_name=event_name, event_id=event_id) for match in matches]


def event_name_from_api(event_id: int, timeout: Any = None) -> str:
    try:
        info = vlr.events.info(event_id=event_id, timeout=timeout)
    except Exception:
        return ""
    return clean_text(get_attr(info, "name", default=""), "")


def annotate_match(raw: Any, event_name: str | None = None, event_id: int | None = None) -> Any:
    if isinstance(raw, dict):
        copy = dict(raw)
        if event_name:
            copy.setdefault("_calendar_event_name", event_name)
        if event_id is not None:
            copy.setdefault("_calendar_event_id", event_id)
        return copy

    try:
        if event_name:
            setattr(raw, "_calendar_event_name", event_name)
        if event_id is not None:
            setattr(raw, "_calendar_event_id", event_id)
    except Exception:
        pass
    return raw


def discover_series_events(source: CalendarSource, settings: dict[str, Any]) -> list[SeriesEvent]:
    timeout = settings.get("request_timeout_seconds", 20)
    limit = parse_optional_int(settings.get("series_event_limit"))
    url = series_url_for_source(source)
    html_text = fetch_text(url, timeout=timeout)
    events = extract_series_events(html_text)
    events = filter_series_events(events, source)

    if limit is not None:
        events = events[:limit]

    return events


def series_url_for_source(source: CalendarSource) -> str:
    if source.series_url:
        return source.series_url
    if source.series_id is None:
        raise ValueError(f"Calendar {source.slug!r} is type=series but has no series_id or series_url.")
    suffix = f"/{source.series_slug.strip('/')}" if source.series_slug else ""
    return f"{VLR_BASE_URL}/series/{source.series_id}{suffix}"


def fetch_text(url: str, timeout: Any = None) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "vlr-calendar-feed/1.0 (+https://github.com/RisPNG/vlr-calendar-feed)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or 20)) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not fetch VLR series page {url}: {exc}") from exc


def extract_series_events(page_html: str) -> list[SeriesEvent]:
    events: list[SeriesEvent] = []
    seen: set[int] = set()

    pattern = re.compile(
        r"<a\b[^>]*href=[\"'](?P<href>/event/(?P<id>\d+)/(?P<slug>[^\"'#?]+))[^\"']*[\"'][^>]*>(?P<body>.*?)</a>",
        re.I | re.S,
    )

    for match in pattern.finditer(page_html):
        event_id = parse_optional_int(match.group("id"))
        if event_id is None or event_id in seen:
            continue

        href = html.unescape(match.group("href"))
        body = match.group("body")
        text = clean_html_text(body)
        slug = html.unescape(match.group("slug"))
        name = text or slug_to_title(slug)

        seen.add(event_id)
        events.append(SeriesEvent(event_id=event_id, name=name, url=f"{VLR_BASE_URL}{href}"))

    return events


def clean_html_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(html.unescape(text), "")


def slug_to_title(slug: str) -> str:
    return clean_text(slug.replace("-", " ").title(), "VLR Event")


def filter_series_events(events: list[SeriesEvent], source: CalendarSource) -> list[SeriesEvent]:
    include = [normalize_name(item) for item in source.event_name_include]
    exclude = [normalize_name(item) for item in source.event_name_exclude]

    filtered: list[SeriesEvent] = []
    for event in events:
        name = normalize_name(event.name)
        if include and not any(token in name for token in include):
            continue
        if exclude and any(token in name for token in exclude):
            continue
        filtered.append(event)
    return filtered


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
    return [
        get_attr(match, "team1", "player_team", default=None),
        get_attr(match, "team2", "opponent_team", default=None),
    ]


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


def team_name_from_obj(team: Any, fallback: str = "TBD") -> str:
    if team is None:
        return fallback
    if isinstance(team, str):
        return clean_text(team, fallback)

    name = clean_text(
        get_attr(
            team,
            "name",
            "team_name",
            "team1_name",
            "team2_name",
            "tag",
            "core",
            default=None,
        ),
        "",
    )
    tag = clean_text(get_attr(team, "tag", default=""), "")

    if name and tag and name != tag:
        return f"{name} ({tag})"

    return name or tag or fallback


def match_id_from_raw(raw: Any) -> str | None:
    match_id = get_attr(raw, "match_id", "id", default=None)
    if match_id not in (None, ""):
        return str(match_id)

    url = clean_text(get_attr(raw, "url", "match_url", "link", default=""), "")
    found = re.search(r"/(\d+)(?:/|$)", url)
    if found:
        return found.group(1)

    return None


def normalize_match(
    raw: Any,
    tz: ZoneInfo,
    allow_date_only: bool = False,
    fallback_live_start: datetime | None = None,
) -> NormalizedMatch | None:
    match_id = match_id_from_raw(raw)
    if not match_id:
        return None

    team1 = get_attr(raw, "team1", "player_team", default=None)
    team2 = get_attr(raw, "team2", "opponent_team", default=None)

    teams = get_attr(raw, "teams", default=None)
    if teams and isinstance(teams, (list, tuple)):
        team1 = team1 or (teams[0] if len(teams) >= 1 else None)
        team2 = team2 or (teams[1] if len(teams) >= 2 else None)

    team1_name = team_name_from_obj(team1, fallback="TBD")
    team2_name = team_name_from_obj(team2, fallback="TBD")

    event_name = clean_text(
        get_attr(
            raw,
            "_calendar_event_name",
            "event",
            "event_name",
            "tournament_name",
            "tournament",
            "event_phase",
            default=None,
        ),
        "VALORANT",
    )

    status = clean_text(get_attr(raw, "status", default="upcoming"), "upcoming").lower()
    raw_time_text = clean_text(get_attr(raw, "time", "time_text", default="")).lower()
    if raw_time_text == "live":
        status = "live"

    result = clean_text(get_attr(raw, "result", default=""), "")
    if result and status == "upcoming":
        status = "completed"

    starts_at = parse_match_datetime(raw, tz=tz, allow_date_only=allow_date_only)
    if starts_at is None and status in {"live", "ongoing"} and fallback_live_start is not None:
        starts_at = with_timezone(fallback_live_start, tz)

    if starts_at is None:
        return None

    url = make_vlr_url(raw, match_id)

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
    raw_time = get_attr(raw, "time", "time_text", default=None)

    if isinstance(raw_date, datetime):
        return with_timezone(raw_date, tz)

    if isinstance(raw_date, date) and raw_time:
        parsed_time = parse_time_value(raw_time)
        if parsed_time is not None:
            return datetime.combine(raw_date, parsed_time, tzinfo=tz)

    candidates: list[str] = []
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

        has_explicit_time = bool(
            re.search(r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)", candidate, re.I)
        )
        if not allow_date_only and parsed.time() == time(0, 0) and not has_explicit_time:
            continue
        return parsed

    return None


def parse_time_value(value: Any) -> time | None:
    if isinstance(value, time):
        return value

    text = clean_text(value)
    if not text or text.lower() in {"tbd", "live", "completed", "ongoing"}:
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
    if not text or text.lower() in {"tbd", "live", "completed", "ongoing"}:
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
    url = clean_text(get_attr(raw, "url", "match_url", "link", default=""), "")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{VLR_BASE_URL}{url}"
    return f"{VLR_BASE_URL}/{match_id}"


def filter_normalized_matches(matches: Iterable[NormalizedMatch], settings: dict[str, Any]) -> list[NormalizedMatch]:
    include_completed = bool(settings.get("include_completed", False))
    include_live = bool(settings.get("include_live", False))

    output: list[NormalizedMatch] = []
    for match in matches:
        status = match.status.lower()
        if status == "completed" and not include_completed:
            continue
        if status in {"live", "ongoing"} and not include_live:
            continue
        output.append(match)
    return output


def dedupe_matches(matches: Iterable[NormalizedMatch]) -> list[NormalizedMatch]:
    seen: dict[str, NormalizedMatch] = {}
    status_priority = {"live": 4, "ongoing": 4, "completed": 3, "upcoming": 2, "scheduled": 1}

    for match in matches:
        existing = seen.get(match.match_id)
        if existing is None:
            seen[match.match_id] = match
            continue

        existing_tbd_count = int(existing.team1_name == "TBD") + int(existing.team2_name == "TBD")
        current_tbd_count = int(match.team1_name == "TBD") + int(match.team2_name == "TBD")
        if current_tbd_count < existing_tbd_count:
            seen[match.match_id] = match
            continue

        if current_tbd_count == existing_tbd_count and status_priority.get(match.status, 0) > status_priority.get(existing.status, 0):
            seen[match.match_id] = match

    return sorted(seen.values(), key=lambda match: (match.starts_at, match.match_id))


def build_ical_calendar(source: CalendarSource, matches: list[NormalizedMatch], settings: dict[str, Any]) -> bytes:
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
        f"REFRESH-INTERVAL;VALUE=DURATION:PT{ttl_hours}H",
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

    links: list[str] = []
    for calendar in built_calendars:
        href = f"{calendar['slug']}.ics"
        absolute = f"{base_url}/{href}" if base_url else href
        links.append(
            f"""
            <article class="card">
              <h2>{html.escape(calendar['name'])}</h2>
              <p>{html.escape(calendar['description'])}</p>
              <p><strong>{calendar['match_count']}</strong> events generated.</p>
              <div class="actions">
                <a href="{html.escape(href)}">Download ICS</a>
                <button data-url="{html.escape(absolute)}">Copy subscription URL</button>
              </div>
              <code>{html.escape(absolute)}</code>
            </article>
            """
        )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
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
    <p class="lede">{html.escape(description)}</p>
    <section class="grid">
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
        matches = dedupe_matches(filter_normalized_matches(normalized, settings))
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
