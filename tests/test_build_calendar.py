from __future__ import annotations

import io
import json
import urllib.error
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

import pytest
from icalendar import Calendar, Event

import scripts.build_calendar as build

NOW = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)
START = "2026-10-17T12:00:00Z"
OLD_START = "2026-09-01T08:00:00Z"


def match_detail(
    match_id, start=START, status="upcoming", teams=None, scores=(None, None), best_of=3
):
    teams = teams or [("624", "Paper Rex"), ("3", "DRX")]
    return {
        "id": str(match_id),
        "teams": [
            {
                "id": team_id,
                "name": name,
                "img": "https://owcdn.net/img/team.png",
                "score": score,
                "tag": None,
            }
            for (team_id, name), score in zip(teams, scores, strict=True)
        ],
        "event": {
            "id": "2775",
            "img": "https://owcdn.net/img/event.png",
            "series": "VCT 2026: Pacific Stage 1",
            "stage": "Playoffs: Grand Final",
            "date": start,
            "status": status,
            "patch": None,
        },
        "bans": [],
        "veto": [],
        "videos": {"streams": [], "vods": []},
        "map_count": 0,
        "total_maps": best_of or 1,
        "best_of": best_of,
        "data": [],
        "previous_encounters": [],
    }


def match_listing(match_id, start=START, status="upcoming"):
    return {
        "id": str(match_id),
        "team1": {"id": "624", "name": "Paper Rex", "score": None},
        "team2": {"id": "3", "name": "DRX", "score": None},
        "event": "VCT 2026: Pacific Stage 1",
        "event_id": "2775",
        "series": "Playoffs: Grand Final",
        "time": start,
        "status": status,
        "best_of": None,
    }


def team_profile(team_id="624", name="Paper Rex", upcoming=None):
    return {
        "name": name,
        "tag": "PRX" if team_id == "624" else "DRX",
        "img": "https://owcdn.net/img/team.png",
        "website": None,
        "twitter": None,
        "country": "Singapore",
        "rank": 1,
        "region": "Asia Pacific",
        "roster": [],
        "upcoming": upcoming or [],
        "completed": [],
    }


def team_match(match_id, start=START, status="upcoming"):
    return {
        "id": str(match_id),
        "event": "VCT 2026: Pacific Stage 1",
        "stage": "Playoffs: Grand Final",
        "opponent": "DRX",
        "opponent_tag": "DRX",
        "date": start,
        "eta": None,
        "score": "2:1" if status == "completed" else "",
        "status": status,
        "best_of": None,
    }


def player_profile(team_id="624", name="Paper Rex"):
    team = {"id": team_id, "name": name, "img": "https://owcdn.net/img/team.png"}
    return {
        "name": "Player One",
        "alias": "playerone",
        "country": "Singapore",
        "img": "https://owcdn.net/img/player.png",
        "agents": [],
        "current_team": team,
        "current_teams": [team],
        "past_teams": [
            {"id": "999", "name": "BOBA", "img": "https://owcdn.net/img/team.png"}
        ],
        "matches": [],
    }


def event_match(match_id, start=START, status="upcoming"):
    return {
        "id": str(match_id),
        "date": start[:10] if start else "2026-10-17",
        "time": start[11:19] if start else None,
        "status": status,
        "teams": [
            {"name": "Paper Rex", "region": "sg"},
            {"name": "DRX", "region": "kr"},
        ],
        "round": "Playoffs",
        "stage": "Grand Final",
        "eta": None,
        "best_of": None,
    }


def calendar_events(path):
    return {
        str(event["UID"]).removeprefix("vlr-match-").split("@", 1)[0]: event
        for event in Calendar.from_ical(path.read_bytes()).walk("VEVENT")
    }


@pytest.fixture
def api_responses(monkeypatch):
    monkeypatch.delenv("VLR_API_BASE_URL", raising=False)
    responses, requests = {}, []

    def urlopen(request, timeout=None):
        url = urlsplit(request.full_url)
        assert url.netloc == "api.example.test", (
            f"Unexpected network request: {request.full_url}"
        )
        key = (url.path.rstrip("/"), tuple(sorted(parse_qsl(url.query))))
        requests.append(key)
        assert key in responses, f"No API response registered for {key}"
        payload = responses[key]
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("scripts.vlr_api.urlopen", urlopen)
    return responses, requests


@pytest.fixture
def workspace(tmp_path):
    config = {
        "site": {
            "title": "VLR Calendar Feeds",
            "description": "Test feeds",
        },
        "settings": {
            "api_base_url": "https://api.example.test",
            "request_timeout_seconds": 120,
            "request_interval_seconds": 0,
            "history_pages_per_run": 1,
            "history_max_pages": 1,
            "historical_event_checks_per_run": 5,
            "detail_refresh_per_run": 24,
            "recent_history_days": 7,
            "timezone": "Asia/Kuala_Lumpur",
            "include_live": True,
            "include_completed": True,
            "published_ttl_hours": 2,
            "default_match_duration_minutes": 120,
            "best_of_duration_minutes": {"1": 60, "3": 120, "5": 180},
        },
        "calendars": [
            {"slug": "all", "name": "All Matches", "type": "global", "enabled": True}
        ],
    }
    return config, {
        "config_path": tmp_path / "calendars.json",
        "output_dir": tmp_path / "public",
        "state_file": tmp_path / "state.json",
    }


def test_all_source_types_share_requests_and_publish_canonical_matches(
    workspace, api_responses
):
    config, paths = workspace
    responses, requests = api_responses
    config["calendars"] += [
        {"slug": "team", "name": "Paper Rex", "type": "team", "team_id": 624},
        {"slug": "player", "name": "Player One", "type": "player", "player_id": 45},
        {"slug": "event", "name": "Pacific", "type": "event", "event_id": 2775},
        {"slug": "series", "name": "VCT 2026", "type": "series", "series_id": 86},
    ]
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100, "2026-10-17T11:00:00Z"),
                match_listing(400, None),
                match_listing(401, None, "live"),
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(200, OLD_START, "completed")
            ],
            ("/api/v1/team/624", (("include_completed", "false"),)): team_profile(
                upcoming=[team_match(100)]
            ),
            ("/api/v1/team/624/matches", (("page", "1"),)): [
                team_match(200, OLD_START, "completed")
            ],
            ("/api/v1/player/45", (("include_matches", "false"),)): player_profile(),
            ("/api/v1/player/45/matches", (("page", "1"),)): [
                {
                    "id": "300",
                    "date": "2024-07-26T10:00:00Z",
                    "event": "GC 24 SEA: Stage 2",
                    "stage": "Playoffs",
                    "team": "BOBA",
                    "opponent": "Xipto Esports GC",
                    "score": "0:2",
                    "status": "completed",
                    "best_of": None,
                }
            ],
            ("/api/v1/events/2775/matches", ()): [
                event_match(100),
                event_match(200, OLD_START, "completed"),
            ],
            ("/api/v1/series/86", ()): {
                "id": "86",
                "name": "VCT 2026",
                "events": [
                    {
                        "id": "2775",
                        "name": "VCT 2026: Pacific Stage 1",
                        "url": "https://www.vlr.gg/event/2775",
                    }
                ],
            },
            ("/api/v1/matches/100", ()): match_detail(100),
            ("/api/v1/matches/200", ()): match_detail(
                200, OLD_START, "final", scores=(2, 1)
            ),
            ("/api/v1/matches/300", ()): match_detail(
                300,
                "2024-07-26T10:00:00Z",
                "final",
                teams=[("999", "BOBA"), ("1000", "Xipto Esports GC")],
                scores=(0, 2),
            ),
            ("/api/v1/matches/400", ()): match_detail(400, None),
            ("/api/v1/matches/401", ()): match_detail(401, None, "live"),
        }
    )

    assert build.build(**paths, now=NOW) == 0

    expected = {
        "all": {"100", "200"},
        "team": {"100", "200"},
        "player": {"100", "300"},
        "event": {"100", "200"},
        "series": {"100", "200"},
    }
    for slug, match_ids in expected.items():
        events = calendar_events(paths["output_dir"] / f"{slug}.ics")
        assert set(events) == match_ids
        assert events["100"].decoded("DTSTART") == datetime(
            2026, 10, 17, 12, tzinfo=timezone.utc
        )
        assert events["100"].decoded("DTEND") == datetime(
            2026, 10, 17, 14, tzinfo=timezone.utc
        )
        assert "Paper Rex" in str(events["100"]["SUMMARY"])
        assert "DRX" in str(events["100"]["SUMMARY"])
        assert str(events["100"]["URL"]) == "https://www.vlr.gg/100"
    assert (
        "completed"
        in str(
            calendar_events(paths["output_dir"] / "all.ics")["200"]["DESCRIPTION"]
        ).lower()
    )
    history = calendar_events(paths["output_dir"] / "player.ics")["300"]
    assert "BOBA" in str(history["SUMMARY"])
    assert "Xipto Esports GC" in str(history["SUMMARY"])
    calendar = Calendar.from_ical((paths["output_dir"] / "all.ics").read_bytes())
    assert calendar.decoded("REFRESH-INTERVAL") == timedelta(hours=2)
    counts = Counter(requests)
    assert counts[("/api/v1/matches/100", ())] == 1
    assert counts[("/api/v1/team/624", (("include_completed", "false"),))] == 1
    assert counts[("/api/v1/events/2775/matches", ())] == 1


def test_unchanged_runs_preserve_publication_and_event_versions(
    workspace, api_responses, capsys, tmp_path
):
    config, paths = workspace
    responses, _ = api_responses
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    published = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in paths["output_dir"].iterdir()
    }
    before = calendar_events(paths["output_dir"] / "all.ics")["100"]

    assert build.build(**paths, now=NOW + timedelta(hours=2)) == 0

    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in paths["output_dir"].iterdir()
    } == published
    after = calendar_events(paths["output_dir"] / "all.ics")["100"]
    assert after.decoded("DTSTAMP") == before.decoded("DTSTAMP")
    assert int(after["SEQUENCE"]) == int(before["SEQUENCE"])
    assert capsys.readouterr().out.rstrip().endswith("Publication changed: false")


def test_existing_match_corrections_update_only_the_changed_event(
    workspace, api_responses
):
    config, paths = workspace
    responses, _ = api_responses
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100),
                match_listing(101),
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100),
            ("/api/v1/matches/101", ()): match_detail(101),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    before = calendar_events(paths["output_dir"] / "all.ics")
    correction_time = NOW + timedelta(hours=2)
    responses[("/api/v1/matches/100", ())]["event"].update(
        date="2026-10-17T15:00:00Z", status="final"
    )
    for team, score in zip(
        responses[("/api/v1/matches/100", ())]["teams"], (2, 1), strict=True
    ):
        team["score"] = score

    assert build.build(**paths, now=correction_time) == 0

    after = calendar_events(paths["output_dir"] / "all.ics")
    assert after["100"].decoded("DTSTART") == datetime(
        2026, 10, 17, 15, tzinfo=timezone.utc
    )
    assert int(after["100"]["SEQUENCE"]) == int(before["100"]["SEQUENCE"]) + 1
    assert after["100"].decoded("DTSTAMP") == correction_time
    assert "completed" in str(after["100"]["DESCRIPTION"]).lower()
    assert after["101"].to_ical() == before["101"].to_ical()


def test_player_transfer_replaces_future_team_matches_and_retains_history(
    workspace, api_responses
):
    config, paths = workspace
    responses, requests = api_responses
    config["calendars"] = [
        {"slug": "player", "name": "Player One", "type": "player", "player_id": 45}
    ]
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/player/45", (("include_matches", "false"),)): player_profile(),
            ("/api/v1/player/45/matches", (("page", "1"),)): [
                {
                    "id": "300",
                    "date": OLD_START,
                    "event": "VCT 2026: Pacific Stage 1",
                    "stage": "Group Stage",
                    "team": "BOBA",
                    "opponent": "Xipto Esports GC",
                    "score": "0:2",
                    "status": "completed",
                    "best_of": 3,
                }
            ],
            ("/api/v1/team/624", (("include_completed", "false"),)): team_profile(
                upcoming=[team_match(100)]
            ),
            ("/api/v1/team/3", (("include_completed", "false"),)): team_profile(
                "3", "DRX", [team_match(200)]
            ),
            ("/api/v1/matches/100", ()): match_detail(100),
            ("/api/v1/matches/200", ()): match_detail(200),
            ("/api/v1/matches/300", ()): match_detail(
                300, OLD_START, "final", scores=(0, 2)
            ),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    assert set(calendar_events(paths["output_dir"] / "player.ics")) == {"100", "300"}
    responses[("/api/v1/player/45", (("include_matches", "false"),))] = player_profile(
        "3", "DRX"
    )
    requests.clear()

    assert build.build(**paths, now=NOW + timedelta(hours=2)) == 0

    assert set(calendar_events(paths["output_dir"] / "player.ics")) == {"200", "300"}
    assert ("/api/v1/team/624", (("include_completed", "false"),)) not in requests


def test_series_discovers_new_events_on_later_runs(workspace, api_responses):
    config, paths = workspace
    responses, requests = api_responses
    config["calendars"] = [
        {"slug": "series", "name": "VCT 2026", "type": "series", "series_id": 86}
    ]
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/series/86", ()): {
                "id": "86",
                "name": "VCT 2026",
                "events": [
                    {
                        "id": "2775",
                        "name": "VCT 2026: Pacific Stage 1",
                        "url": "https://www.vlr.gg/event/2775",
                    }
                ],
            },
            ("/api/v1/events/2775/matches", ()): [event_match(100)],
            ("/api/v1/events/2790/matches", ()): [event_match(200)],
            ("/api/v1/matches/100", ()): match_detail(100),
            ("/api/v1/matches/200", ()): match_detail(200),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    responses[("/api/v1/series/86", ())]["events"].append(
        {
            "id": "2790",
            "name": "Valorant Masters London 2026",
            "url": "https://www.vlr.gg/event/2790",
        }
    )
    requests.clear()

    assert build.build(**paths, now=NOW + timedelta(hours=2)) == 0

    assert set(calendar_events(paths["output_dir"] / "series.ics")) == {"100", "200"}
    assert ("/api/v1/series/86", ()) in requests
    assert ("/api/v1/events/2790/matches", ()) in requests


def test_old_details_are_revisited_after_leaving_recent_results(
    workspace, api_responses
):
    config, paths = workspace
    responses, requests = api_responses
    config["settings"]["detail_refresh_per_run"] = 64
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(match_id, OLD_START, "completed")
                for match_id in range(100, 150)
            ],
            **{
                (f"/api/v1/matches/{match_id}", ()): match_detail(
                    match_id, OLD_START, "final", scores=(0, 2)
                )
                for match_id in range(100, 150)
            },
            **{
                (f"/api/v1/matches/{match_id}", ()): match_detail(
                    match_id, "2026-10-16T06:00:00Z", "final", scores=(2, 1)
                )
                for match_id in range(200, 250)
            },
        }
    )
    assert build.build(**paths, now=NOW) == 0
    original = calendar_events(paths["output_dir"] / "all.ics")
    config["settings"]["detail_refresh_per_run"] = 2
    paths["config_path"].write_text(json.dumps(config))
    responses[("/api/v1/matches/completed", (("page", "1"),))] = [
        match_listing(match_id, "2026-10-16T06:00:00Z", "completed")
        for match_id in range(200, 250)
    ]
    responses[("/api/v1/matches/101", ())]["teams"][0]["score"] = 1
    responses[("/api/v1/matches/101", ())]["event"]["date"] = "2026-09-01T09:00:00Z"

    for run in range(1, 4):
        assert build.build(**paths, now=NOW + timedelta(hours=2 * run)) == 0

    refreshed = calendar_events(paths["output_dir"] / "all.ics")
    assert set(refreshed) == {
        str(match_id) for match_id in [*range(100, 150), *range(200, 250)]
    }
    assert Counter(requests)[("/api/v1/matches/101", ())] >= 2
    assert refreshed["101"].decoded("DTSTART") == datetime(
        2026, 9, 1, 9, tzinfo=timezone.utc
    )
    assert int(refreshed["101"]["SEQUENCE"]) == int(original["101"]["SEQUENCE"]) + 1


@pytest.mark.parametrize("failure", ["source", "detail"])
def test_failed_fetch_preserves_published_files_and_refresh_state(
    workspace, api_responses, failure
):
    config, paths = workspace
    responses, _ = api_responses
    config["calendars"].append(
        {"slug": "team", "name": "Paper Rex", "type": "team", "team_id": 624}
    )
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/team/624", (("include_completed", "false"),)): team_profile(
                upcoming=[team_match(100)]
            ),
            ("/api/v1/team/624/matches", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    published = {path.name: path.read_bytes() for path in paths["output_dir"].iterdir()}
    state = paths["state_file"].read_bytes()
    responses[("/api/v1/matches/100", ())]["event"]["date"] = "2026-10-17T15:00:00Z"
    failed_request = (
        ("/api/v1/team/624", (("include_completed", "false"),))
        if failure == "source"
        else ("/api/v1/matches/100", ())
    )
    responses[failed_request] = urllib.error.URLError("VLR unavailable")

    with pytest.raises((RuntimeError, urllib.error.URLError), match="VLR unavailable"):
        build.build(**paths, now=NOW + timedelta(hours=2))

    assert {
        path.name: path.read_bytes() for path in paths["output_dir"].iterdir()
    } == published
    assert paths["state_file"].read_bytes() == state


def test_history_pages_are_revisited_with_a_bounded_rotation(workspace, api_responses):
    config, paths = workspace
    responses, requests = api_responses
    config["settings"]["history_max_pages"] = 3
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(match_id, OLD_START, "completed")
                for match_id in range(100, 150)
            ],
            ("/api/v1/matches/completed", (("page", "2"),)): [
                match_listing(match_id, OLD_START, "completed")
                for match_id in range(200, 250)
            ],
            ("/api/v1/matches/completed", (("page", "3"),)): [
                match_listing(300, OLD_START, "completed")
            ],
            **{
                (f"/api/v1/matches/{match_id}", ()): match_detail(
                    match_id, OLD_START, "final", scores=(2, 1)
                )
                for match_id in [*range(100, 150), *range(200, 250), 300]
            },
        }
    )

    for run in range(3):
        assert build.build(**paths, now=NOW + timedelta(hours=2 * run)) == 0

    assert set(calendar_events(paths["output_dir"] / "all.ics")) == {
        str(match_id) for match_id in [*range(100, 150), *range(200, 250), 300]
    }
    counts = Counter(requests)
    assert counts[("/api/v1/matches/completed", (("page", "1"),))] == 3
    assert counts[("/api/v1/matches/completed", (("page", "2"),))] >= 1
    assert counts[("/api/v1/matches/completed", (("page", "3"),))] >= 1
    requests.clear()

    for run in range(3, 6):
        assert build.build(**paths, now=NOW + timedelta(hours=2 * run)) == 0

    counts = Counter(requests)
    assert counts[("/api/v1/matches/completed", (("page", "1"),))] == 3
    assert counts[("/api/v1/matches/completed", (("page", "2"),))] >= 1
    assert counts[("/api/v1/matches/completed", (("page", "3"),))] >= 1
    assert (
        sum(
            count
            for (path, query), count in counts.items()
            if path == "/api/v1/matches/completed"
        )
        <= 6
    )


@pytest.mark.parametrize(
    "retain_state", [True, False], ids=["cached-state", "cache-evicted"]
)
def test_clean_output_can_reuse_previous_publication(
    workspace, api_responses, capsys, tmp_path, retain_state
):
    config, paths = workspace
    responses, _ = api_responses
    config["settings"]["history_max_pages"] = 2
    config["settings"]["detail_refresh_per_run"] = 64
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(match_id, OLD_START, "completed")
                for match_id in range(200, 250)
            ],
            ("/api/v1/matches/completed", (("page", "2"),)): [
                match_listing(300, OLD_START, "completed")
            ],
            ("/api/v1/matches/100", ()): match_detail(100),
            **{
                (f"/api/v1/matches/{match_id}", ()): match_detail(
                    match_id, OLD_START, "final", scores=(2, 1)
                )
                for match_id in [*range(200, 250), 300]
            },
        }
    )
    assert build.build(**paths, now=NOW) == 0
    published = {path.name: path.read_bytes() for path in paths["output_dir"].iterdir()}
    previous = paths["output_dir"]
    paths["output_dir"] = tmp_path / "next-public"
    if not retain_state:
        paths["state_file"].unlink()
        config["settings"]["history_pages_per_run"] = 0
        paths["config_path"].write_text(json.dumps(config))

    assert (
        build.build(**paths, previous_dir=previous, now=NOW + timedelta(hours=2)) == 0
    )

    assert {
        path.name: path.read_bytes() for path in paths["output_dir"].iterdir()
    } == published
    assert {path.name: path.read_bytes() for path in previous.iterdir()} == published
    assert capsys.readouterr().out.rstrip().endswith("Publication changed: false")


def test_historical_score_correction_advances_event_version(workspace, api_responses):
    config, paths = workspace
    responses, _ = api_responses
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(200, OLD_START, "completed")
            ],
            ("/api/v1/matches/200", ()): match_detail(
                200, OLD_START, "final", scores=(2, 1)
            ),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    before = calendar_events(paths["output_dir"] / "all.ics")["200"]
    for team, score in zip(
        responses[("/api/v1/matches/200", ())]["teams"], (1, 2), strict=True
    ):
        team["score"] = score
    correction_time = NOW + timedelta(hours=2)

    assert build.build(**paths, now=correction_time) == 0

    after = calendar_events(paths["output_dir"] / "all.ics")["200"]
    assert after["UID"] == before["UID"]
    assert after.decoded("DTSTART") == before.decoded("DTSTART")
    assert after["SUMMARY"] == before["SUMMARY"]
    assert "Score: 2:1" in str(before["DESCRIPTION"])
    assert "Score: 1:2" in str(after["DESCRIPTION"])
    assert int(after["SEQUENCE"]) == int(before["SEQUENCE"]) + 1
    assert after.decoded("DTSTAMP") == correction_time


@pytest.mark.parametrize(
    "start,best_of,expected_hour,duration",
    [
        ("2026-10-17T00:00:00Z", 1, 0, 60),
        ("2026-10-17T12:00:00Z", 5, 12, 180),
        ("2026-10-17T20:00:00+08:00", None, 12, 120),
    ],
)
def test_api_start_and_announced_format_control_calendar_time(
    workspace, api_responses, start, best_of, expected_hour, duration
):
    config, paths = workspace
    responses, _ = api_responses
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100, start)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100, start, best_of=best_of),
        }
    )

    assert build.build(**paths, now=NOW) == 0

    event = calendar_events(paths["output_dir"] / "all.ics")["100"]
    assert event.decoded("DTSTART") == datetime(
        2026, 10, 17, expected_hour, tzinfo=timezone.utc
    )
    assert event.decoded("DTEND") - event.decoded("DTSTART") == timedelta(
        minutes=duration
    )
    if best_of is None:
        assert "Format:" not in str(event["DESCRIPTION"])
    else:
        assert f"Format: BO{best_of}" in str(event["DESCRIPTION"])


def test_completed_series_events_are_revisited_to_find_added_matches(
    workspace, api_responses
):
    config, paths = workspace
    responses, requests = api_responses
    config["settings"]["historical_event_checks_per_run"] = 1
    config["calendars"] = [
        {"slug": "series", "name": "VCT 2026", "type": "series", "series_id": 86}
    ]
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/series/86", ()): {
                "id": "86",
                "name": "VCT 2026",
                "events": [
                    {
                        "id": "2775",
                        "name": "Pacific Stage 1",
                        "url": "https://www.vlr.gg/event/2775",
                        "status": "completed",
                    },
                    {
                        "id": "2790",
                        "name": "Pacific Stage 2",
                        "url": "https://www.vlr.gg/event/2790",
                        "status": "completed",
                    },
                ],
            },
            ("/api/v1/events/2775/matches", ()): [
                event_match(100, OLD_START, "completed")
            ],
            ("/api/v1/events/2790/matches", ()): [
                event_match(200, OLD_START, "completed")
            ],
            ("/api/v1/matches/100", ()): match_detail(
                100, OLD_START, "final", scores=(2, 1)
            ),
            ("/api/v1/matches/200", ()): match_detail(
                200, OLD_START, "final", scores=(2, 1)
            ),
            ("/api/v1/matches/300", ()): match_detail(
                300, OLD_START, "final", scores=(2, 1)
            ),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    responses[("/api/v1/events/2790/matches", ())].append(
        event_match(300, OLD_START, "completed")
    )
    requests.clear()
    for run in range(1, 4):
        start = len(requests)
        assert build.build(**paths, now=NOW + timedelta(hours=2 * run)) == 0
        assert (
            sum(path.startswith("/api/v1/events/") for path, query in requests[start:])
            <= 1
        )

    assert set(calendar_events(paths["output_dir"] / "series.ics")) == {
        "100",
        "200",
        "300",
    }
    assert ("/api/v1/events/2775/matches", ()) in requests
    assert ("/api/v1/events/2790/matches", ()) in requests


@pytest.mark.parametrize(
    "team_start,expected_team_hour", [(START, 12), ("2026-10-17T16:00:00Z", 16)]
)
def test_shared_detail_cache_does_not_hide_one_sources_rescheduled_start(
    workspace, api_responses, team_start, expected_team_hour
):
    config, paths = workspace
    responses, requests = api_responses
    config["calendars"].append(
        {"slug": "team", "name": "Paper Rex", "type": "team", "team_id": 624}
    )
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100, "2026-10-17T11:00:00Z")
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/team/624", (("include_completed", "false"),)): team_profile(
                upcoming=[team_match(100)]
            ),
            ("/api/v1/team/624/matches", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    config["settings"]["detail_refresh_per_run"] = 0
    paths["config_path"].write_text(json.dumps(config))
    responses[("/api/v1/matches", (("include_completed", "false"),))][0]["time"] = (
        "2026-10-17T15:00:00Z"
    )
    responses[("/api/v1/team/624", (("include_completed", "false"),))]["upcoming"][0][
        "date"
    ] = team_start
    requests.clear()

    assert build.build(**paths, now=NOW + timedelta(hours=2)) == 0

    global_event = calendar_events(paths["output_dir"] / "all.ics")["100"]
    team_event = calendar_events(paths["output_dir"] / "team.ics")["100"]
    assert global_event.decoded("DTSTART") == datetime(
        2026, 10, 17, 15, tzinfo=timezone.utc
    )
    assert team_event.decoded("DTSTART") == datetime(
        2026, 10, 17, expected_team_hour, tzinfo=timezone.utc
    )
    assert ("/api/v1/matches/100", ()) not in requests


def test_failed_publication_retry_reuses_the_generated_event_version(
    workspace, api_responses, capsys, tmp_path
):
    config, paths = workspace
    responses, _ = api_responses
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    previous = paths["output_dir"]
    published = (previous / "all.ics").read_bytes()
    responses[("/api/v1/matches/100", ())]["event"]["date"] = "2026-10-17T15:00:00Z"
    paths["output_dir"] = tmp_path / "failed-publication"
    assert (
        build.build(**paths, previous_dir=previous, now=NOW + timedelta(hours=2)) == 0
    )
    candidate = (paths["output_dir"] / "all.ics").read_bytes()
    assert candidate != published
    paths["output_dir"] = tmp_path / "retry-publication"

    assert (
        build.build(**paths, previous_dir=previous, now=NOW + timedelta(hours=4)) == 0
    )

    assert (paths["output_dir"] / "all.ics").read_bytes() == candidate
    assert (previous / "all.ics").read_bytes() == published
    assert capsys.readouterr().out.rstrip().endswith("Publication changed: true")


def test_detail_budget_reserves_capacity_for_current_matches_with_large_history(
    workspace, api_responses
):
    config, paths = workspace
    responses, requests = api_responses
    config["settings"].update(history_max_pages=2, detail_refresh_per_run=2)
    paths["config_path"].write_text(json.dumps(config))
    old_ids = [*range(100, 150), *range(200, 210)]
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(999)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(match_id, OLD_START, "completed")
                for match_id in old_ids[:50]
            ],
            ("/api/v1/matches/completed", (("page", "2"),)): [
                match_listing(match_id, OLD_START, "completed")
                for match_id in old_ids[50:]
            ],
            **{
                (f"/api/v1/matches/{match_id}", ()): match_detail(
                    match_id, OLD_START, "final"
                )
                for match_id in old_ids
            },
            ("/api/v1/matches/999", ()): match_detail(999, "2026-10-17T15:00:00Z"),
        }
    )

    assert build.build(**paths, now=NOW) == 0

    assert ("/api/v1/matches/999", ()) in requests
    assert (
        sum(
            path.startswith("/api/v1/matches/") and path.rsplit("/", 1)[1].isdigit()
            for path, query in requests
        )
        == 2
    )
    assert calendar_events(paths["output_dir"] / "all.ics")["999"].decoded(
        "DTSTART"
    ) == datetime(2026, 10, 17, 15, tzinfo=timezone.utc)


def test_global_stage_filter_uses_listing_series_when_details_are_deferred(
    workspace, api_responses
):
    config, paths = workspace
    responses, _ = api_responses
    config["settings"]["detail_refresh_per_run"] = 0
    config["calendars"][0]["stage"] = "Grand Final"
    paths["config_path"].write_text(json.dumps(config))
    group_match = match_listing(101)
    group_match["series"] = "Group Stage: Week 1"
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100),
                group_match,
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
        }
    )

    assert build.build(**paths, now=NOW) == 0

    assert set(calendar_events(paths["output_dir"] / "all.ics")) == {"100"}


def test_complete_source_history_removes_matches_no_longer_in_that_source(
    workspace, api_responses
):
    config, paths = workspace
    responses, _ = api_responses
    config["settings"]["detail_refresh_per_run"] = 0
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(100, OLD_START, "completed"),
                match_listing(101, OLD_START, "completed"),
            ],
        }
    )
    assert build.build(**paths, now=NOW) == 0
    original = calendar_events(paths["output_dir"] / "all.ics")
    responses[("/api/v1/matches/completed", (("page", "1"),))] = [
        match_listing(101, OLD_START, "completed")
    ]

    assert build.build(**paths, now=NOW + timedelta(hours=2)) == 0

    remaining = calendar_events(paths["output_dir"] / "all.ics")
    assert set(remaining) == {"101"}
    assert remaining["101"].to_ical() == original["101"].to_ical()


def test_corrected_match_participants_remove_an_incorrect_team_membership(
    workspace, api_responses
):
    config, paths = workspace
    responses, _ = api_responses
    config["calendars"] = [
        {"slug": "team", "name": "Paper Rex", "type": "team", "team_id": 624}
    ]
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/team/624", (("include_completed", "false"),)): team_profile(),
            ("/api/v1/team/624/matches", (("page", "1"),)): [
                team_match(100, OLD_START, "completed")
            ],
            ("/api/v1/matches/100", ()): match_detail(
                100, OLD_START, "final", scores=(2, 1)
            ),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    assert set(calendar_events(paths["output_dir"] / "team.ics")) == {"100"}
    responses[("/api/v1/matches/100", ())] = match_detail(
        100,
        OLD_START,
        "final",
        teams=[("999", "BOBA"), ("1000", "Xipto Esports GC")],
        scores=(2, 1),
    )

    assert build.build(**paths, now=NOW + timedelta(hours=2)) == 0

    assert calendar_events(paths["output_dir"] / "team.ics") == {}


def test_flattened_published_descriptions_retain_completed_history_on_cache_loss(
    workspace, api_responses, tmp_path
):
    config, paths = workspace
    responses, _ = api_responses
    config["settings"]["detail_refresh_per_run"] = 0
    paths["config_path"].write_text(json.dumps(config))
    previous = tmp_path / "published-before"
    previous.mkdir()
    calendar = Calendar()
    calendar.add("version", "2.0")
    calendar.add("prodid", "-//vlr-calendar-feed//github-pages//EN")
    start = datetime(2024, 7, 26, 10, tzinfo=timezone.utc)
    for match_id, status in [(300, "completed"), (301, "upcoming")]:
        event = Event()
        event.add("uid", f"vlr-match-{match_id}@vlr-calendar-feed")
        event.add("dtstamp", datetime(2026, 5, 4, 12, tzinfo=timezone.utc))
        event.add("dtstart", start)
        event.add("dtend", start + timedelta(minutes=120))
        event.add("summary", "GC 24 SEA: Stage 2 | BOBA vs Xipto Esports GC (XIP.GC)")
        event.add(
            "description",
            (
                f"https://www.vlr.gg/{match_id} Event: GC 24 SEA: Stage 2 "
                "Match: BOBA vs Xipto Esports GC (XIP.GC) "
                f"Status: {status} Format: Bo3 Calendar duration: 120 minutes Source: VLR.gg"
            ),
        )
        event.add("categories", [f"VALORANT,VLR.gg,{status}"])
        event.add("status", "CONFIRMED")
        calendar.add_component(event)
    (previous / "all.ics").write_bytes(calendar.to_ical())
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [],
            ("/api/v1/matches/completed", (("page", "1"),)): [
                match_listing(match_id, "2026-10-16T06:00:00Z", "completed")
                for match_id in range(200, 250)
            ],
        }
    )

    assert build.build(**paths, previous_dir=previous, now=NOW) == 0

    events = calendar_events(paths["output_dir"] / "all.ics")
    assert set(events) == {"300"} | {str(match_id) for match_id in range(200, 250)}
    retained = events["300"]
    assert retained.decoded("DTSTART") == start
    assert retained.decoded("DTEND") == start + timedelta(minutes=120)
    assert (
        str(retained["SUMMARY"])
        == "GC 24 SEA: Stage 2 | BOBA vs Xipto Esports GC (XIP.GC)"
    )
    assert "Status: completed\n" in str(retained["DESCRIPTION"])
    assert "Format: BO3\n" in str(retained["DESCRIPTION"])


def test_newer_published_revision_takes_precedence_over_restored_stale_state(
    workspace, api_responses, capsys, tmp_path
):
    config, paths = workspace
    responses, _ = api_responses
    paths["config_path"].write_text(json.dumps(config))
    responses.update(
        {
            ("/api/v1/matches", (("include_completed", "false"),)): [
                match_listing(100)
            ],
            ("/api/v1/matches/completed", (("page", "1"),)): [],
            ("/api/v1/matches/100", ()): match_detail(100),
        }
    )
    assert build.build(**paths, now=NOW) == 0
    stale_state = paths["state_file"].read_bytes()
    assert int(calendar_events(paths["output_dir"] / "all.ics")["100"]["SEQUENCE"]) == 0
    for revision, hour in [(1, 15), (2, 16)]:
        start = f"2026-10-17T{hour}:00:00Z"
        responses[("/api/v1/matches", (("include_completed", "false"),))][0]["time"] = (
            start
        )
        responses[("/api/v1/matches/100", ())]["event"]["date"] = start
        assert build.build(**paths, now=NOW + timedelta(hours=2 * revision)) == 0

    previous = paths["output_dir"]
    published = {path.name: path.read_bytes() for path in previous.iterdir()}
    latest = calendar_events(previous / "all.ics")["100"]
    assert int(latest["SEQUENCE"]) == 2
    assert latest.decoded("DTSTAMP") == NOW + timedelta(hours=4)
    paths["state_file"].write_bytes(stale_state)
    paths["output_dir"] = tmp_path / "restored-cache-run"

    assert (
        build.build(**paths, previous_dir=previous, now=NOW + timedelta(hours=6)) == 0
    )

    assert {
        path.name: path.read_bytes() for path in paths["output_dir"].iterdir()
    } == published
    assert (
        calendar_events(paths["output_dir"] / "all.ics")["100"].to_ical()
        == latest.to_ical()
    )
    assert capsys.readouterr().out.rstrip().endswith("Publication changed: false")
