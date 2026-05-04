from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.build_calendar import CalendarSource, build_ical_calendar, normalize_match


@dataclass
class Team:
    name: str
    id: int | None = None


@dataclass
class RawMatch:
    match_id: int
    team1: Team
    team2: Team
    event: str
    match_datetime: datetime
    status: str = "upcoming"


def test_normalize_match_generates_expected_summary():
    raw = RawMatch(
        match_id=123,
        team1=Team("PRX"),
        team2=Team("DRX"),
        event="VCT Pacific",
        match_datetime=datetime(2026, 5, 10, 20, 0),
    )

    match = normalize_match(raw, tz=ZoneInfo("Asia/Kuala_Lumpur"))

    assert match is not None
    assert match.summary == "VCT Pacific | PRX vs DRX"
    assert match.url == "https://www.vlr.gg/123"


def test_build_ical_contains_match_url_and_uid():
    raw = RawMatch(
        match_id=456,
        team1=Team("PRX"),
        team2=Team("GEN"),
        event="Masters",
        match_datetime=datetime(2026, 6, 1, 18, 0),
    )
    match = normalize_match(raw, tz=ZoneInfo("Asia/Kuala_Lumpur"))
    assert match is not None

    ics = build_ical_calendar(
        CalendarSource(
            slug="paper-rex",
            name="Paper Rex VLR Matches",
            description="Test calendar",
            source_type="team",
            enabled=True,
            team_id=624,
        ),
        [match],
        {"default_match_duration_minutes": 120},
    ).decode("utf-8")

    assert "UID:vlr-match-456@vlr-calendar-feed" in ics
    assert "SUMMARY:Masters | PRX vs GEN" in ics
    assert "https://www.vlr.gg/456" in ics
