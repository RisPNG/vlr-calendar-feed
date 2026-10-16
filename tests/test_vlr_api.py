import io
import json
import urllib.error
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import find_ids
from scripts.vlr_api import VlrApiClient


def test_api_requests_share_responses_and_preserve_transport_settings(monkeypatch):
    monkeypatch.delenv("VLR_API_BASE_URL", raising=False)
    monkeypatch.setenv("VLR_API_KEY", "calendar-test-key")
    payload = [{"id": "100", "status": "upcoming", "time": "2026-10-17T12:00:00Z"}]
    transport = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr("scripts.vlr_api.urlopen", transport)
    client = VlrApiClient("https://api.example.test/", timeout=120, interval=0)

    first = client.get("/api/v1/matches/", include_completed=False, completed_pages=1)
    second = client.get("api/v1/matches/", completed_pages=1, include_completed=False)

    assert first == second == payload
    transport.assert_called_once()
    request = transport.call_args.args[0]
    assert (
        request.full_url
        == "https://api.example.test/api/v1/matches/?completed_pages=1&include_completed=false"
    )
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Authorization") == "Bearer calendar-test-key"
    assert transport.call_args.kwargs["timeout"] == 120


def test_failed_api_response_is_retried_when_requested_again(monkeypatch):
    monkeypatch.delenv("VLR_API_BASE_URL", raising=False)
    payload = {"id": "86", "name": "VCT 2026", "events": []}
    transport = Mock(
        side_effect=[
            urllib.error.URLError("temporarily unavailable"),
            io.BytesIO(json.dumps(payload).encode()),
        ]
    )
    monkeypatch.setattr("scripts.vlr_api.urlopen", transport)
    client = VlrApiClient("https://api.example.test", interval=0)

    with pytest.raises(urllib.error.URLError, match="temporarily unavailable"):
        client.get("/api/v1/series/86")

    assert client.get("/api/v1/series/86") == payload
    assert transport.call_count == 2


@pytest.mark.parametrize("source_type", ["team", "player"])
def test_id_search_cli_encodes_query_and_maps_api_categories_to_vlr_urls(
    monkeypatch, tmp_path, capsys, source_type
):
    monkeypatch.delenv("VLR_API_BASE_URL", raising=False)
    config = tmp_path / "calendars.json"
    config.write_text(
        json.dumps(
            {
                "settings": {
                    "api_base_url": "https://api.example.test",
                    "request_interval_seconds": 0,
                }
            }
        )
    )
    query = "Xipto Esports GC & co?"
    monkeypatch.setattr(
        "sys.argv",
        [
            "find_ids.py",
            query,
            "--type",
            source_type,
            "--limit",
            "1",
            "--config",
            str(config),
        ],
    )
    payload = [
        {"id": "123", "name": "Xipto Esports GC"},
        {"id": "456", "name": "Another result"},
    ]
    transport = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr("scripts.vlr_api.urlopen", transport)

    assert find_ids.main() == 0

    request = urlsplit(transport.call_args.args[0].full_url)
    assert request.path == "/api/v1/search/"
    assert parse_qs(request.query) == {
        "search_category": [f"{source_type}s"],
        "search_term": [query],
    }
    assert (
        capsys.readouterr().out
        == f"123\tXipto Esports GC\thttps://www.vlr.gg/{source_type}/123\n"
    )
