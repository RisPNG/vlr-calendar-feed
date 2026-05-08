from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import scripts.build_calendar as build


@dataclass
class Team:
    name: str
    tag: str | None = None
    id: int | None = None


@dataclass
class RawMatch:
    match_id: int
    player_team: Team
    opponent_team: Team
    event: str
    date: date
    time: str
    status: str = "completed"
    url: str = "/123/test-match"


def test_player_match_team_names_are_not_tbd():
    raw = RawMatch(
        match_id=123,
        player_team=Team(name="BOBA"),
        opponent_team=Team(name="Xipto Esports GC", tag="XIP.GC"),
        event="GC 24 SEA: Stage 2",
        date=date(2024, 7, 26),
        time="18:00",
    )

    match = build.normalize_match(raw, tz=ZoneInfo("Asia/Kuala_Lumpur"))

    assert match is not None
    assert match.summary == "GC 24 SEA: Stage 2 | BOBA vs Xipto Esports GC (XIP.GC)"


def test_live_fallback_allows_live_match_without_time():
    raw = {
        "match_id": 456,
        "teams": [{"name": "Paper Rex", "id": 624}, {"name": "DRX", "id": 3}],
        "event": "VCT Pacific",
        "status": "live",
        "url": "/456/paper-rex-vs-drx",
    }
    fallback = datetime(2026, 5, 4, 20, 0, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))

    match = build.normalize_match(
        raw,
        tz=ZoneInfo("Asia/Kuala_Lumpur"),
        fallback_live_start=fallback,
    )

    assert match is not None
    assert match.starts_at == fallback
    assert match.summary == "VCT Pacific | Paper Rex vs DRX"


def test_extract_series_events_dedupes_links():
    html = """
    <a href="/event/2775/vct-2026-pacific-stage-1">VCT 2026: Pacific Stage 1</a>
    <a href="/event/2775/vct-2026-pacific-stage-1">Duplicate</a>
    <a href="/event/2790/valorant-masters-london-2026"><span>Valorant Masters London 2026</span></a>
    """

    events = build.extract_series_events(html)

    assert [event.event_id for event in events] == [2775, 2790]
    assert events[0].name == "VCT 2026: Pacific Stage 1"
    assert events[1].name == "Valorant Masters London 2026"


def test_series_event_filters_include_and_exclude():
    source = build.CalendarSource(
        slug="vct-pacific",
        name="VCT Pacific",
        description="",
        source_type="series",
        enabled=True,
        series_id=86,
        event_name_include=["Pacific"],
        event_name_exclude=["Kickoff"],
    )
    events = [
        build.SeriesEvent(1, "VCT 2026: Pacific Stage 1", "/event/1/a"),
        build.SeriesEvent(2, "VCT 2026: Pacific Kickoff", "/event/2/b"),
        build.SeriesEvent(3, "VCT 2026: Americas Stage 1", "/event/3/c"),
    ]

    filtered = build.filter_series_events(events, source)

    assert [event.event_id for event in filtered] == [1]


def test_build_ical_contains_summary_and_url():
    source = build.CalendarSource(
        slug="test",
        name="Test Calendar",
        description="Test Desc",
        source_type="event",
        enabled=True,
    )
    match = build.NormalizedMatch(
        match_id="123",
        event_name="VCT Pacific",
        team1_name="Paper Rex",
        team2_name="DRX",
        starts_at=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc),
        status="upcoming",
        url="https://www.vlr.gg/123/test",
    )

    ics = build.build_ical_calendar(source, [match], {"published_ttl_hours": 2}).decode("utf-8")

    assert "SUMMARY:VCT Pacific | Paper Rex vs DRX" in ics
    assert "URL:https://www.vlr.gg/123/test" in ics


def test_annotated_object_preserves_parent_event_name():
    raw = RawMatch(
        match_id=789,
        player_team=Team(name="Paper Rex", tag="PRX"),
        opponent_team=Team(name="DRX"),
        event="",
        date=date(2026, 5, 4),
        time="20:00",
    )

    annotated = build.annotate_match(raw, event_name="VCT 2026: Pacific Stage 1", event_id=2775)
    match = build.normalize_match(annotated, tz=ZoneInfo("Asia/Kuala_Lumpur"))

    assert match is not None
    assert match.summary == "VCT 2026: Pacific Stage 1 | Paper Rex (PRX) vs DRX"


def test_best_of_duration_mapping():
    settings = {
        "best_of_duration_minutes": {
            "1": 60,
            "3": 120,
            "5": 180,
        }
    }

    assert build.best_of_to_duration_minutes("BO1", settings) == 60
    assert build.best_of_to_duration_minutes("Best of 3", settings) == 120
    assert build.best_of_to_duration_minutes("bo5", settings) == 180
    assert build.best_of_to_duration_minutes("unknown", settings) is None


def test_ical_uses_match_specific_duration_with_default_fallback():
    source = build.CalendarSource(
        slug="test",
        name="Test Calendar",
        description="Test Desc",
        source_type="event",
        enabled=True,
    )
    start = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    bo1 = build.NormalizedMatch(
        match_id="bo1",
        event_name="Test Event",
        team1_name="A",
        team2_name="B",
        starts_at=start,
        status="upcoming",
        url="https://www.vlr.gg/1/test",
        duration_minutes=60,
        best_of="BO1",
    )
    fallback = build.NormalizedMatch(
        match_id="fallback",
        event_name="Test Event",
        team1_name="C",
        team2_name="D",
        starts_at=start,
        status="upcoming",
        url="https://www.vlr.gg/2/test",
    )

    ics = build.build_ical_calendar(
        source,
        [bo1, fallback],
        {"published_ttl_hours": 2, "default_match_duration_minutes": 120},
    ).decode("utf-8")

    assert "UID:vlr-match-bo1@vlr-calendar-feed" in ics
    assert "DTEND:20260504T130000Z" in ics
    assert "UID:vlr-match-fallback@vlr-calendar-feed" in ics
    assert "DTEND:20260504T140000Z" in ics


def test_detect_timezone_from_visible_edT_time():
    detected = build.timezone_from_text("Friday, May 8 4:00 AM EDT")

    assert detected is not None
    assert detected.label == "America/New_York (EDT)"

    start = datetime.combine(date(2026, 5, 8), build.parse_time_value("4:00 AM"), tzinfo=detected.tz)
    assert start.astimezone(timezone.utc) == datetime(2026, 5, 8, 8, 0, tzinfo=timezone.utc)


def test_detect_timezone_from_visible_pdt_time():
    detected = build.timezone_from_text("Friday, May 8 1:00 AM PDT")

    assert detected is not None
    assert detected.label == "America/Los_Angeles (PDT)"

    start = datetime.combine(date(2026, 5, 8), build.parse_time_value("1:00 AM"), tzinfo=detected.tz)
    assert start.astimezone(timezone.utc) == datetime(2026, 5, 8, 8, 0, tzinfo=timezone.utc)


def test_detect_timezone_from_match_detail_html():
    html = """
    <div class="match-header-date">
      <div class="moment-tz-convert">Fri, May 8</div>
      <div class="moment-tz-convert">1:00 AM PDT</div>
    </div>
    """

    detected = build.detect_timezone_from_match_detail_html(html)

    assert detected is not None
    assert detected.label == "America/Los_Angeles (PDT)"


def test_detect_source_timezone_from_raw_match_time_without_network():
    raw = RawMatch(
        match_id=666490,
        player_team=Team(name="Paper Rex", tag="PRX"),
        opponent_team=Team(name="Global Esports", tag="GE"),
        event="VCT 26: PAC Stage 1",
        date=date(2026, 5, 8),
        time="1:00 AM PDT",
        status="upcoming",
    )

    detected = build.detect_source_timezone(
        [raw],
        {"detect_source_timezone": True},
        fallback_tz=ZoneInfo("Asia/Kuala_Lumpur"),
    )
    match = build.normalize_match(raw, tz=detected.tz)

    assert match is not None
    assert match.starts_at.astimezone(timezone.utc) == datetime(2026, 5, 8, 8, 0, tzinfo=timezone.utc)
