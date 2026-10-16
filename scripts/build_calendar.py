#!/usr/bin/env python3
"""Poll the VLR API and publish stable iCalendar subscriptions."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory
from typing import Any

from icalendar import Calendar, Event

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.vlr_api import VlrApiClient

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config" / "calendars.json"
PUBLIC_DIR = ROOT_DIR / "public"
STATE_PATH = ROOT_DIR / ".calendar-state" / "state.json"


@dataclass(frozen=True)
class CalendarSource:
    slug: str
    name: str
    description: str
    source_type: str
    enabled: bool = True
    team_id: int | None = None
    player_id: int | None = None
    event_id: int | None = None
    event_ids: list[int] = field(default_factory=list)
    series_id: int | None = None
    stage: str | None = None
    event_name_include: list[str] = field(default_factory=list)
    event_name_exclude: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedMatch:
    match_id: str
    event_name: str
    team1_name: str
    team2_name: str
    starts_at: datetime
    status: str
    url: str
    duration_minutes: int | None = None
    best_of: str = ""
    score: str = ""
    stage: str = ""

    @property
    def summary(self) -> str:
        return (
            f"{self.event_name or 'VALORANT'} | {self.team1_name} vs {self.team2_name}"
        )


def load_sources(config: dict[str, Any]) -> list[CalendarSource]:
    sources = []
    for item in config.get("calendars", []):
        slug = item["slug"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise ValueError(f"Invalid calendar slug: {slug}")
        sources.append(
            CalendarSource(
                slug=slug,
                name=item["name"],
                description=item.get("description", "VLR.gg match calendar"),
                source_type=item["type"],
                enabled=item.get("enabled", True),
                team_id=item.get("team_id"),
                player_id=item.get("player_id"),
                event_id=item.get("event_id"),
                event_ids=item.get("event_ids", []),
                series_id=item.get("series_id"),
                stage=item.get("stage"),
                event_name_include=item.get("event_name_include", []),
                event_name_exclude=item.get("event_name_exclude", []),
            )
        )
    if len({source.slug for source in sources}) != len(sources):
        raise ValueError("Calendar slugs must be unique")
    return [source for source in sources if source.enabled]


def normalize_match(
    raw: dict[str, Any], settings: dict[str, Any] | None = None
) -> NormalizedMatch | None:
    settings = settings or {}
    value = raw.get("time")
    if not value:
        return None
    if len(value) == 10:
        if not settings.get("allow_date_only", False):
            return None
        value += "T00:00:00+00:00"
    starts_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if starts_at.tzinfo is None:
        raise ValueError(f"API returned a timezone-less start for match {raw['id']}")
    status = {"final": "completed", "ongoing": "live", "scheduled": "upcoming"}.get(
        raw["status"], raw["status"]
    )
    best_of = str(raw.get("best_of") or "")
    duration = None
    if best_of and settings.get("infer_match_duration_from_best_of", True):
        duration = int(
            settings.get("best_of_duration_minutes", {"1": 60, "3": 120, "5": 180}).get(
                best_of, settings.get("default_match_duration_minutes", 120)
            )
        )
    first, second = raw["team1"], raw["team2"]
    scores = [first.get("score"), second.get("score")]
    return NormalizedMatch(
        match_id=str(raw["id"]),
        event_name=raw.get("event", ""),
        team1_name=first.get("name") or "TBD",
        team2_name=second.get("name") or "TBD",
        starts_at=starts_at.astimezone(timezone.utc),
        status=status,
        url=f"https://www.vlr.gg/{raw['id']}",
        best_of=f"BO{best_of}" if best_of else "",
        duration_minutes=duration,
        score=":".join(str(score) for score in scores)
        if all(score is not None for score in scores)
        else "",
        stage=raw.get("stage") or raw.get("series") or "",
    )


def reconcile_feed_versions(published: bytes, candidate: bytes) -> bytes:
    if not candidate:
        return published
    if not published:
        return candidate
    events = {}
    for contents in (published, candidate):
        for event in Calendar.from_ical(contents).walk("VEVENT"):
            uid = str(event["uid"])
            current = events.get(uid)
            if current is None or (
                int(event.get("sequence", 0)),
                event.decoded("dtstamp"),
            ) > (int(current.get("sequence", 0)), current.decoded("dtstamp")):
                events[uid] = event
    calendar = Calendar()
    calendar.add("version", "2.0")
    calendar.add("prodid", "-//vlr-calendar-feed//github-pages//EN")
    for event in events.values():
        calendar.add_component(event)
    return calendar.to_ical()


def published_history(
    contents: bytes, confirmed: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    records = []
    for event in Calendar.from_ical(contents).walk("VEVENT"):
        fields = dict(
            re.findall(
                r"(?:^|\s)(Event|Match|Status|Stage|Score|Format|Calendar duration|Source):\s*(.*?)(?=\s(?:Event|Match|Status|Stage|Score|Format|Calendar duration|Source):|$)",
                str(event.get("description", "")),
                re.DOTALL,
            )
        )
        if fields.get("Status") != "completed":
            continue
        match_id = (
            str(event["uid"])
            .removeprefix("vlr-match-")
            .removesuffix("@vlr-calendar-feed")
        )
        if (
            confirmed
            and match_id not in confirmed["ids"]
            and datetime.fromisoformat(confirmed["checked_at"])
            >= event.decoded("dtstamp")
        ):
            continue
        teams = fields.get("Match", "").split(" vs ", 1)
        if len(teams) != 2:
            continue
        scores = fields.get("Score", "").split(":")
        records.append(
            {
                "id": match_id,
                "time": event.decoded("dtstart").isoformat(),
                "status": "completed",
                "event": fields.get("Event", ""),
                "stage": fields.get("Stage", ""),
                "best_of": (
                    format_match.group(1)
                    if (
                        format_match := re.fullmatch(
                            r"bo\s*([1357])", fields.get("Format", ""), re.IGNORECASE
                        )
                    )
                    else None
                ),
                "team1": {
                    "name": teams[0],
                    "score": scores[0] if len(scores) == 2 else None,
                },
                "team2": {
                    "name": teams[1],
                    "score": scores[1] if len(scores) == 2 else None,
                },
            }
        )
    return records


class CalendarPoller:
    def __init__(
        self,
        client: VlrApiClient,
        settings: dict[str, Any],
        state: dict[str, Any],
        now: datetime,
    ):
        self.client, self.settings, self.state, self.now = client, settings, state, now
        self.histories: dict[str, list[dict[str, Any]]] = {}
        self.confirmed_history: dict[str, dict[str, Any]] = {}
        self.event_ids: set[str] = set()

    def history(self, path: str) -> list[dict[str, Any]]:
        if path in self.histories:
            return self.histories[path]
        saved = self.state.setdefault("history", {}).setdefault(
            path, {"pages": {}, "cursor": 2}
        )
        pages = saved["pages"]
        first = self.client.get(path, page=1)
        pages["1"] = {"checked_at": self.now.isoformat(), "matches": first}
        first_ids = {str(match["id"]) for match in first}
        maximum = min(50, max(1, int(self.settings.get("history_max_pages", 50))))
        if len(first) < 50:
            saved["pages"] = pages = {"1": pages["1"]}
            saved["cursor"] = 2
            saved["confirmed_ids"] = sorted(first_ids)
            saved["confirmed_at"] = self.now.isoformat()
            saved.pop("sweep_ids", None)
        else:
            if saved["cursor"] > maximum:
                saved.pop("sweep_ids", None)
            for _ in range(
                min(maximum - 1, int(self.settings.get("history_pages_per_run", 1)))
            ):
                page = max(2, min(saved["cursor"], maximum))
                if page == 2:
                    saved["sweep_ids"] = sorted(first_ids)
                records = self.client.get(path, page=page)
                pages[str(page)] = {
                    "checked_at": self.now.isoformat(),
                    "matches": records,
                }
                if "sweep_ids" in saved:
                    saved["sweep_ids"] = sorted(
                        set(saved["sweep_ids"])
                        | first_ids
                        | {str(match["id"]) for match in records}
                    )
                if len(records) < 50:
                    saved["pages"] = pages = {
                        key: value for key, value in pages.items() if int(key) <= page
                    }
                    saved["cursor"] = 2
                    if "sweep_ids" in saved:
                        saved["confirmed_ids"] = saved.pop("sweep_ids")
                        saved["confirmed_at"] = self.now.isoformat()
                    break
                saved["cursor"] = page + 1 if page < maximum else 2
                if saved["cursor"] == 2:
                    saved.pop("sweep_ids", None)
        if "confirmed_ids" in saved:
            self.confirmed_history[path] = {
                "ids": set(saved["confirmed_ids"]),
                "checked_at": saved["confirmed_at"],
            }
        records_by_id = {}
        for page in sorted(pages.values(), key=lambda page: page["checked_at"]):
            records_by_id.update((str(match["id"]), match) for match in page["matches"])
        records = list(records_by_id.values())
        self.histories[path] = records
        return records

    def event_matches(
        self, event: dict[str, Any], refresh: bool
    ) -> list[dict[str, Any]]:
        key = str(event["id"])
        self.event_ids.add(key)
        events = self.state.setdefault("events", {})
        if refresh or key not in events:
            records = self.client.get(f"/api/v1/events/{key}/matches")
            events[key] = {"checked_at": self.now.isoformat(), "matches": records}
        output = []
        for match in events[key]["matches"][
            : int(self.settings.get("event_match_limit", 25565))
        ]:
            if len(match["teams"]) < 2:
                continue
            clock = match.get("time")
            value = (
                f"{match['date']}T{clock}+00:00"
                if match.get("date") and clock
                else None
            )
            if value is None and self.settings.get("allow_date_only"):
                value = match.get("date")
            output.append(
                {
                    "id": str(match["id"]),
                    "team1": match["teams"][0],
                    "team2": match["teams"][1],
                    "time": value,
                    "status": match["status"],
                    "event": event["name"],
                    "stage": match.get("stage") or match.get("round") or "",
                    "best_of": match.get("best_of"),
                }
            )
        return output

    def collect_source(self, source: CalendarSource) -> list[dict[str, Any]]:
        if source.source_type == "global":
            records = list(self.client.get("/api/v1/matches/", include_completed=False))
            if self.settings.get("include_completed", False):
                records += self.history("/api/v1/matches/completed")
            return records
        if source.source_type in {"team", "player"}:
            records = []
            if source.source_type == "player":
                profile = self.client.get(
                    f"/api/v1/player/{source.player_id}", include_matches=False
                )
                teams = profile.get("current_teams") or (
                    [profile["current_team"]] if profile.get("current_team") else []
                )
                if self.settings.get("include_completed", False):
                    for match in self.history(
                        f"/api/v1/player/{source.player_id}/matches"
                    ):
                        scores = match.get("score", "").split(":")
                        records.append(
                            {
                                "id": match["id"],
                                "team1": {
                                    "name": match["team"],
                                    "score": scores[0] if len(scores) == 2 else None,
                                },
                                "team2": {
                                    "name": match["opponent"],
                                    "score": scores[1] if len(scores) == 2 else None,
                                },
                                "time": match.get("date"),
                                "status": match["status"],
                                "event": match["event"],
                                "stage": match.get("stage", ""),
                                "best_of": match.get("best_of"),
                            }
                        )
            else:
                teams = [{"id": str(source.team_id)}]
            for team in teams:
                profile = self.client.get(
                    f"/api/v1/team/{team['id']}", include_completed=False
                )
                matches = list(profile["upcoming"])
                if source.source_type == "team" and self.settings.get(
                    "include_completed", False
                ):
                    matches += self.history(f"/api/v1/team/{team['id']}/matches")
                for match in matches:
                    scores = match.get("score", "").split(":")
                    records.append(
                        {
                            "id": match["id"],
                            "team1": {
                                "id": str(team["id"]),
                                "name": profile["name"],
                                "score": scores[0] if len(scores) == 2 else None,
                            },
                            "team2": {
                                "name": match["opponent"],
                                "score": scores[1] if len(scores) == 2 else None,
                            },
                            "time": match.get("date"),
                            "status": match["status"],
                            "event": match["event"],
                            "stage": match.get("stage", ""),
                            "best_of": match.get("best_of"),
                        }
                    )
            return records
        if source.source_type == "event":
            events = [
                {"id": event_id, "name": source.name}
                for event_id in (source.event_ids or [source.event_id])
            ]
            return [
                match
                for event in events
                for match in self.event_matches(event, refresh=True)
            ]
        if source.source_type == "series":
            series = self.client.get(f"/api/v1/series/{source.series_id}")
            events = [
                event
                for event in series["events"]
                if (
                    not source.event_name_include
                    or any(
                        name.casefold() in event["name"].casefold()
                        for name in source.event_name_include
                    )
                )
                and not any(
                    name.casefold() in event["name"].casefold()
                    for name in source.event_name_exclude
                )
            ]
            events = events[: int(self.settings.get("series_event_limit", 25565))]
            saved = self.state.setdefault("events", {})
            historical = sorted(
                (event for event in events if event.get("status") == "completed"),
                key=lambda event: (
                    saved.get(str(event["id"]), {}).get("checked_at", ""),
                    str(event["id"]),
                ),
            )
            selected = {
                str(event["id"])
                for event in historical[
                    : int(self.settings.get("historical_event_checks_per_run", 5))
                ]
            }
            records = []
            for event in events:
                records += self.event_matches(
                    event,
                    refresh=event.get("status") != "completed"
                    or str(event["id"]) in selected,
                )
            return records
        raise ValueError(f"Unknown calendar type: {source.source_type}")

    def refresh_details(
        self,
        matches: dict[str, dict[str, Any]],
        listings: dict[str, list[dict[str, Any]]],
    ) -> None:
        details = self.state.setdefault("details", {})
        limit = max(0, int(self.settings.get("detail_refresh_per_run", 24)))
        cutoff = (
            self.now - timedelta(days=int(self.settings.get("recent_history_days", 7)))
        ).isoformat()
        ordered = sorted(
            matches, key=lambda key: (details.get(key, {}).get("checked_at", ""), key)
        )
        older = [
            key
            for key in ordered
            if matches[key]["status"] in {"completed", "final"}
            and (matches[key].get("time") or "9999") < cutoff
        ]
        selected = older[: max(1, limit // 2)] if limit else []
        selected += [key for key in ordered if key not in older][
            : limit - len(selected)
        ]
        selected += [key for key in ordered if key not in selected][
            : limit - len(selected)
        ]
        for key in selected:
            detail = self.client.get(f"/api/v1/matches/{key}")
            event = detail["event"]
            teams = detail["teams"]
            if len(teams) < 2:
                raise ValueError(f"Match detail {key} does not contain both teams")
            details[key] = {
                "checked_at": self.now.isoformat(),
                "listings": listings[key],
                "participants": sorted(
                    {
                        str(member["id"])
                        for map_data in detail.get("data", [])
                        for member in map_data.get("members", [])
                    }
                ),
                "match": {
                    "id": key,
                    "time": event.get("date"),
                    "event": event.get("series") or matches[key].get("event", ""),
                    "stage": event.get("stage", ""),
                    "status": event["status"],
                    "best_of": detail.get("best_of"),
                    "team1": {
                        name: teams[0].get(name) for name in ("id", "name", "score")
                    },
                    "team2": {
                        name: teams[1].get(name) for name in ("id", "name", "score")
                    },
                },
            }
        self.state["details"] = {
            key: value for key, value in details.items() if key in matches
        }


def build_ical_calendar(
    source: CalendarSource,
    matches: list[NormalizedMatch],
    settings: dict[str, Any],
    previous: bytes | None = None,
    now: datetime | None = None,
) -> bytes:
    now = now or datetime.now(timezone.utc)
    old_events = {}
    if previous:
        old_events = {
            str(event["UID"]): event
            for event in Calendar.from_ical(previous).walk("VEVENT")
        }
    calendar = Calendar()
    calendar.add("prodid", "-//vlr-calendar-feed//github-pages//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", source.name)
    calendar.add("x-wr-caldesc", source.description)
    calendar.add("x-wr-timezone", settings.get("timezone", "UTC"))
    interval = timedelta(hours=int(settings.get("published_ttl_hours", 2)))
    calendar.add("x-published-ttl", interval)
    calendar.add("refresh-interval", interval)
    for match in matches:
        event = Event()
        uid = f"vlr-match-{match.match_id}@vlr-calendar-feed"
        event.add("uid", uid)
        event.add("dtstart", match.starts_at)
        event.add(
            "dtend",
            match.starts_at
            + timedelta(
                minutes=match.duration_minutes
                or int(settings.get("default_match_duration_minutes", 120))
            ),
        )
        event.add("summary", match.summary)
        description = [
            match.url,
            "",
            f"Event: {match.event_name}",
            f"Match: {match.team1_name} vs {match.team2_name}",
            f"Status: {match.status}",
        ]
        if match.stage:
            description.append(f"Stage: {match.stage}")
        if match.score:
            description.append(f"Score: {match.score}")
        if match.best_of:
            description.append(f"Format: {match.best_of}")
        description.append("Source: VLR.gg")
        event.add("description", "\n".join(description))
        event.add("url", match.url)
        event.add("categories", ["VALORANT", "VLR.gg", match.status])
        event.add("status", "CANCELLED" if match.status == "cancelled" else "CONFIRMED")
        previous_event = old_events.get(uid)
        stamp, sequence = now, 0
        if previous_event:
            stamp = previous_event.decoded("dtstamp")
            sequence = int(previous_event.get("sequence", 0))
            del previous_event["DTSTAMP"]
            previous_event.pop("SEQUENCE", None)
            if previous_event.to_ical() != event.to_ical():
                stamp, sequence = now, sequence + 1
        event.add("dtstamp", stamp)
        event.add("sequence", sequence)
        calendar.add_component(event)
    return calendar.to_ical()


def build(
    config_path: Path = CONFIG_PATH,
    output_dir: Path = PUBLIC_DIR,
    previous_dir: Path | None = None,
    state_file: Path | None = None,
    now: datetime | None = None,
) -> int:
    config_path, output_dir = Path(config_path), Path(output_dir)
    previous_dir = Path(previous_dir) if previous_dir is not None else output_dir
    state_file = Path(state_file) if state_file is not None else STATE_PATH
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings = config.get("settings", {})
    state = (
        json.loads(state_file.read_text(encoding="utf-8"))
        if state_file.exists()
        else {}
    )
    client = VlrApiClient(
        settings["api_base_url"],
        timeout=float(settings.get("request_timeout_seconds", 120)),
        interval=float(settings.get("request_interval_seconds", 1)),
    )
    poller = CalendarPoller(client, settings, state, now)
    sources = load_sources(config)
    collected = {}
    current_memberships: dict[str, set[str]] = {}
    all_matches = {}
    listings: dict[str, list[dict[str, Any]]] = {}
    versions = state.setdefault("feeds", {})
    prior_feeds = {}
    for source in sources:
        print(f"Fetching {source.slug} ({source.source_type})...", flush=True)
        prior = previous_dir / f"{source.slug}.ics"
        previous = reconcile_feed_versions(
            prior.read_bytes() if prior.exists() else b"",
            versions.get(source.slug, "").encode("utf-8"),
        )
        prior_feeds[source.slug] = previous
        records = poller.collect_source(source)
        current_memberships[source.slug] = {str(record["id"]) for record in records}
        if (
            settings.get("include_completed", False)
            and previous
            and source.source_type in {"global", "team", "player"}
        ):
            current_ids = {str(record["id"]) for record in records}
            history_path = {
                "global": "/api/v1/matches/completed",
                "team": f"/api/v1/team/{source.team_id}/matches",
                "player": f"/api/v1/player/{source.player_id}/matches",
            }[source.source_type]
            records = [
                record
                for record in published_history(
                    previous, poller.confirmed_history.get(history_path)
                )
                if record["id"] not in current_ids
            ] + records
        collected[source.slug] = records
        all_matches.update((str(record["id"]), record) for record in records)
        for record in records:
            listings.setdefault(str(record["id"]), []).append(record)
    poller.refresh_details(all_matches, listings)
    state["history"] = {
        key: value
        for key, value in state.get("history", {}).items()
        if key in poller.histories
    }
    state["events"] = {
        key: value
        for key, value in state.get("events", {}).items()
        if key in poller.event_ids
    }
    files: dict[str, bytes] = {}
    manifest = []
    for source in sources:
        matches = {}
        counts = {"upcoming": 0, "live": 0, "completed": 0}
        for record in collected[source.slug]:
            key = str(record["id"])
            cached = state["details"].get(key)
            raw = dict(record)
            if cached:
                raw["best_of"] = cached["match"].get("best_of")
                if record in cached["listings"]:
                    raw = cached["match"]
                    team_ids = {
                        str(raw[team]["id"])
                        for team in ("team1", "team2")
                        if raw[team].get("id")
                    }
                    if (
                        source.source_type == "team"
                        and len(team_ids) == 2
                        and str(source.team_id) not in team_ids
                    ):
                        continue
                    if (
                        source.source_type == "player"
                        and key not in current_memberships[source.slug]
                        and cached["participants"]
                        and str(source.player_id) not in cached["participants"]
                    ):
                        continue
            match = normalize_match(raw, settings)
            if (
                match is None
                or (
                    match.status == "completed"
                    and not settings.get("include_completed", False)
                )
                or (match.status == "live" and not settings.get("include_live", False))
            ):
                continue
            if source.stage and source.stage.casefold() not in match.stage.casefold():
                continue
            if match.status in counts:
                if counts[match.status] >= int(
                    settings.get(f"{match.status}_limit", 25565)
                ):
                    continue
                counts[match.status] += 1
            matches[key] = match
        filename = f"{source.slug}.ics"
        contents = build_ical_calendar(
            source,
            sorted(
                matches.values(), key=lambda match: (match.starts_at, match.match_id)
            ),
            settings,
            prior_feeds[source.slug] or None,
            now,
        )
        files[filename] = contents
        versions[source.slug] = contents.decode("utf-8")
        manifest.append(
            {
                "slug": source.slug,
                "name": source.name,
                "description": source.description,
                "type": source.source_type,
                "match_count": len(matches),
                "file": filename,
            }
        )
        print(f"Built {filename}: {len(matches)} matches", flush=True)
    state["feeds"] = {source.slug: versions[source.slug] for source in sources}
    site = config.get("site", {})
    cards = []
    for feed in manifest:
        cards.append(
            f'<article class="card"><h2>{html.escape(feed["name"])}</h2><p>{html.escape(feed["description"])}</p><p><strong>{feed["match_count"]}</strong> events generated.</p><div class="actions"><a href="{feed["file"]}">Download ICS</a><button data-feed="{feed["file"]}">Copy subscription URL</button></div><code>{feed["file"]}</code></article>'
        )
    template = Template(
        (ROOT_DIR / "scripts" / "index.html").read_text(encoding="utf-8")
    )
    files["index.html"] = template.substitute(
        title=html.escape(site.get("title", "VLR Calendar Feeds")),
        description=html.escape(
            site.get(
                "description", "Public Valorant match calendars generated from VLR.gg."
            )
        ),
        cards="\n".join(cards),
    ).encode("utf-8")
    files["feeds.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    previous_files = (
        {path.name for path in previous_dir.iterdir() if path.is_file()}
        if previous_dir.exists()
        else set()
    )
    changed = previous_files != set(files) or any(
        not (previous_dir / name).exists()
        or (previous_dir / name).read_bytes() != content
        for name, content in files.items()
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".calendar-build-", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary)
        for name, content in files.items():
            (staging / name).write_bytes(content)
        (staging / "state.json").write_text(
            json.dumps(state, separators=(",", ":")), encoding="utf-8"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            target = output_dir / name
            if not target.exists() or target.read_bytes() != content:
                os.replace(staging / name, target)
        for target in output_dir.iterdir():
            if target.is_file() and target.name not in files:
                target.unlink()
        os.replace(staging / "state.json", state_file)
    print(f"Publication changed: {str(changed).lower()}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=PUBLIC_DIR)
    parser.add_argument("--previous-dir", type=Path)
    parser.add_argument("--state-file", type=Path, default=STATE_PATH)
    args = parser.parse_args()
    raise SystemExit(
        build(args.config, args.output_dir, args.previous_dir, args.state_file)
    )
