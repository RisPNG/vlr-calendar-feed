import io
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.build_calendar import CalendarPoller
from scripts.vlr_api import VlrApiClient

NOW = datetime(2026, 10, 16, 8, tzinfo=timezone.utc)
HISTORY = "/api/v1/team/624/matches"


@pytest.fixture
def history_api(monkeypatch):
    pages, requests = {}, []
    monkeypatch.delenv("VLR_API_BASE_URL", raising=False)

    def respond(request, timeout):
        url = urlsplit(request.full_url)
        assert url.netloc == "api.example.test" and url.path == HISTORY
        page = int(parse_qs(url.query)["page"][0])
        requests.append(page)
        return io.BytesIO(json.dumps(pages[page]).encode())

    monkeypatch.setattr("scripts.vlr_api.urlopen", respond)
    return pages, requests


def test_complete_rotating_sweep_confirms_membership_and_discards_obsolete_pages(
    history_api,
):
    pages, requests = history_api
    pages.update(
        {
            1: [{"id": str(match_id)} for match_id in range(1, 51)],
            2: [{"id": str(match_id)} for match_id in range(51, 101)],
            3: [{"id": "101"}],
        }
    )
    state = {
        "history": {
            HISTORY: {
                "pages": {
                    "4": {"checked_at": NOW.isoformat(), "matches": [{"id": "999"}]}
                },
                "cursor": 2,
            }
        }
    }
    settings = {"history_pages_per_run": 1, "history_max_pages": 5}

    for run in range(2):
        checked_at = NOW + timedelta(hours=2 * run)
        poller = CalendarPoller(
            VlrApiClient("https://api.example.test", interval=0),
            settings,
            state,
            checked_at,
        )
        records = poller.history(HISTORY)
        if run == 0:
            assert HISTORY not in poller.confirmed_history
            assert "999" in {record["id"] for record in records}

    assert poller.confirmed_history[HISTORY] == {
        "ids": {str(match_id) for match_id in range(1, 102)},
        "checked_at": checked_at.isoformat(),
    }
    assert {record["id"] for record in records} == {
        str(match_id) for match_id in range(1, 102)
    }
    assert set(state["history"][HISTORY]["pages"]) == {"1", "2", "3"}
    assert requests == [1, 2, 1, 3]


def test_full_page_cap_keeps_prior_confirmation_and_does_not_accumulate_sweep_metadata(
    history_api,
):
    pages, _ = history_api
    previous_check = (NOW - timedelta(days=1)).isoformat()
    state = {
        "history": {
            HISTORY: {
                "pages": {},
                "cursor": 2,
                "confirmed_ids": ["previous-known-match"],
                "confirmed_at": previous_check,
            }
        }
    }
    settings = {"history_pages_per_run": 1, "history_max_pages": 2}

    for run in range(6):
        pages.update(
            {
                1: [{"id": str(run * 100 + match_id)} for match_id in range(1, 51)],
                2: [{"id": str(run * 100 + match_id)} for match_id in range(51, 101)],
            }
        )
        poller = CalendarPoller(
            VlrApiClient("https://api.example.test", interval=0),
            settings,
            state,
            NOW + timedelta(hours=2 * run),
        )
        poller.history(HISTORY)
        assert poller.confirmed_history[HISTORY] == {
            "ids": {"previous-known-match"},
            "checked_at": previous_check,
        }
        assert "sweep_ids" not in state["history"][HISTORY]
        assert len(state["history"][HISTORY]["pages"]) == 2


def test_mid_rotation_cache_without_sweep_metadata_waits_for_a_complete_new_sweep(
    history_api,
):
    pages, requests = history_api
    pages.update(
        {
            1: [{"id": str(match_id)} for match_id in range(1, 51)],
            2: [{"id": str(match_id)} for match_id in range(51, 101)],
            3: [],
        }
    )
    state = {"history": {HISTORY: {"pages": {}, "cursor": 3}}}
    settings = {"history_pages_per_run": 1, "history_max_pages": 5}

    for run in range(3):
        poller = CalendarPoller(
            VlrApiClient("https://api.example.test", interval=0),
            settings,
            state,
            NOW + timedelta(hours=2 * run),
        )
        poller.history(HISTORY)
        if run < 2:
            assert HISTORY not in poller.confirmed_history

    assert poller.confirmed_history[HISTORY]["ids"] == {
        str(match_id) for match_id in range(1, 101)
    }
    assert requests == [1, 3, 1, 2, 1, 3]


@pytest.mark.parametrize(
    "terminal_page", [1, 2], ids=["empty-source", "short-page-at-cap"]
)
def test_actual_source_end_confirms_even_when_the_configured_cap_is_reached(
    history_api, terminal_page
):
    pages, _ = history_api
    pages[1] = (
        []
        if terminal_page == 1
        else [{"id": str(match_id)} for match_id in range(1, 51)]
    )
    pages[2] = [{"id": "51"}]
    poller = CalendarPoller(
        VlrApiClient("https://api.example.test", interval=0),
        {"history_pages_per_run": 1, "history_max_pages": terminal_page},
        {},
        NOW,
    )

    records = poller.history(HISTORY)

    assert poller.confirmed_history[HISTORY] == {
        "ids": {record["id"] for record in records},
        "checked_at": NOW.isoformat(),
    }
