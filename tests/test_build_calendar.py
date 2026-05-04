from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_calendar import (  # noqa: E402
    CalendarSource,
    NormalizedMatch,
    build_ical_calendar,
    current_team_ids_from_profile,
    dedupe_matches,
    normalize_match,
)


@dataclass
class Obj:
    pass


def test_player_match_uses_player_team_and_opponent_team() -> None:
    tz = ZoneInfo("Asia/Kuala_Lumpur")

    player_team = Obj()
    player_team.name = "Shopify Rebellion"
    player_team.tag = "SR"

    opponent_team = Obj()
    opponent_team.name = "Xipto Esports"
    opponent_team.tag = "XIP"

    raw = Obj()
    raw.match_id = 123
    raw.url = "/123/example-match"
    raw.player_team = player_team
    raw.opponent_team = opponent_team
    raw.event = "Game Changers"
    raw.result = "win"
    raw.date = "2026-05-01"
    raw.time = "20:00"

    match = normalize_match(raw, tz=tz)

    assert match is not None
    assert match.summary == "Game Changers | Shopify Rebellion (SR) vs Xipto Esports (XIP)"
    assert match.status == "completed"


def test_live_match_fallback_start_time() -> None:
    tz = ZoneInfo("Asia/Kuala_Lumpur")
    fallback = datetime(2026, 5, 4, 20, 0, tzinfo=tz)

    raw = {
        "match_id": 456,
        "status": "live",
        "event": "VCT Pacific",
        "teams": [
            {"name": "Paper Rex", "id": 624},
            {"name": "DRX", "id": 8185},
        ],
        "url": "/456/live-match",
    }

    match = normalize_match(raw, tz=tz, fallback_live_start=fallback)

    assert match is not None
    assert match.starts_at == fallback
    assert match.summary == "VCT Pacific | Paper Rex vs DRX"


def test_current_team_ids_from_profile() -> None:
    profile = Obj()
    team = Obj()
    team.id = 624
    team.role = "player"
    team.left_date = None
    profile.current_teams = [team]

    assert current_team_ids_from_profile(profile) == [624]


def test_dedupe_prefers_non_tbd_names() -> None:
    dt = datetime(2026, 5, 4, 20, 0, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))
    bad = NormalizedMatch("1", "Event", "TBD", "TBD", dt, "upcoming", "https://www.vlr.gg/1")
    good = NormalizedMatch("1", "Event", "A", "B", dt, "completed", "https://www.vlr.gg/1")

    assert dedupe_matches([bad, good])[0].summary == "Event | A vs B"


def test_ics_contains_stable_uid_and_summary() -> None:
    dt = datetime(2026, 5, 4, 20, 0, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))
    source = CalendarSource("ayumiii", "Ayumiii", "Calendar", "player", True, player_id=8175)
    match = NormalizedMatch("123", "Event", "Team A", "Team B", dt, "completed", "https://www.vlr.gg/123")

    ics = build_ical_calendar(source, [match], {"published_ttl_hours": 2}).decode("utf-8")

    assert "UID:vlr-match-123@vlr-calendar-feed" in ics
    assert "SUMMARY:Event | Team A vs Team B" in ics
