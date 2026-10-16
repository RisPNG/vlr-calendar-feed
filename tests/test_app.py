import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar

from app.main import create_app


@pytest.fixture
def backend(monkeypatch):
    for variable in (
        "VLR_API_BASE_URL",
        "VLR_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    state = SimpleNamespace(
        fail=False,
        block_next=False,
        blocked=threading.Event(),
        release=threading.Event(),
        active=0,
        maximum_active=0,
        lock=threading.Lock(),
        start="2026-10-17T12:00:00Z",
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            with state.lock:
                state.active += 1
                state.maximum_active = max(state.maximum_active, state.active)
            try:
                if state.block_next:
                    state.block_next = False
                    state.blocked.set()
                    if not state.release.wait(10):
                        self.send_error(504)
                        return
                url = urlsplit(self.path)
                route = (url.path.rstrip("/"), tuple(sorted(parse_qsl(url.query))))
                pages = {
                    ("/api/v1/matches", (("include_completed", "false"),)): [
                        {
                            "id": "100",
                            "time": state.start,
                            "status": "upcoming",
                            "event": "VCT Pacific",
                            "series": "Playoffs: Grand Final",
                            "team1": {"id": "624", "name": "Paper Rex"},
                            "team2": {"id": "3", "name": "DRX"},
                            "best_of": None,
                        }
                    ],
                    ("/api/v1/matches/completed", (("page", "1"),)): [],
                    ("/api/v1/matches/100", ()): {
                        "id": "100",
                        "best_of": 3,
                        "total_maps": 3,
                        "teams": [
                            {"id": "624", "name": "Paper Rex", "score": None},
                            {"id": "3", "name": "DRX", "score": None},
                        ],
                        "event": {
                            "id": "2775",
                            "series": "VCT Pacific",
                            "stage": "Playoffs: Grand Final",
                            "date": state.start,
                            "status": "upcoming",
                        },
                        "map_count": 0,
                        "data": [],
                        "videos": {"streams": [], "vods": []},
                    },
                }
                status = 503 if state.fail else (200 if route in pages else 404)
                payload = pages.get(route, {"detail": "Unknown test API route"})
                content = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                try:
                    self.wfile.write(content)
                except BrokenPipeError:
                    pass
            finally:
                with state.lock:
                    state.active -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.fixture
def service_config(tmp_path, backend):
    config = {
        "site": {
            "title": "Calendar Service",
            "description": "Match subscriptions",
        },
        "settings": {
            "api_base_url": backend.url,
            "request_timeout_seconds": 5,
            "request_interval_seconds": 0,
            "history_max_pages": 1,
            "history_pages_per_run": 1,
            "detail_refresh_per_run": 2,
            "include_live": True,
            "include_completed": True,
            "published_ttl_hours": 2,
            "build_timeout_seconds": 30,
        },
        "calendars": [{"slug": "all", "name": "All Matches", "type": "global"}],
    }
    path = tmp_path / "calendars.json"
    path.write_text(json.dumps(config))
    return path


def wait_for_health(client, predicate):
    deadline = time.monotonic() + 10
    health = None
    while time.monotonic() < deadline:
        response = client.get("/health")
        assert response.status_code == 200
        health = response.json()
        if predicate(health):
            return health
        time.sleep(0.02)
    raise AssertionError(
        f"Calendar service health did not reach the expected state: {health}"
    )


@pytest.mark.parametrize(
    "page_origin",
    ["https://vlrggics.rispeng.com", "https://vlr-calendar-feed.onrender.com"],
)
def test_initial_background_build_keeps_health_responsive_and_serves_real_feeds(
    service_config, backend, tmp_path, page_origin
):
    backend.block_next = True
    app = create_app(
        service_config,
        tmp_path / "data",
        poll_interval_seconds=60,
        retry_interval_seconds=60,
    )

    with TestClient(app, base_url=page_origin) as client:
        assert backend.blocked.wait(5)
        assert client.get("/health").json() == {
            "status": "starting",
            "ready": False,
            "last_success": None,
        }
        response = client.get("/")
        assert response.status_code == 503
        assert response.headers["retry-after"] == "30"
        backend.release.set()
        health = wait_for_health(
            client, lambda value: value["ready"] and value["status"] == "ok"
        )
        assert health["last_success"]
        response = client.get("/all.ics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/calendar")
        event = Calendar.from_ical(response.content).walk("VEVENT")[0]
        assert str(event["UID"]) == "vlr-match-100@vlr-calendar-feed"
        assert event.decoded("DTSTART") == datetime(
            2026, 10, 17, 12, tzinfo=timezone.utc
        )
        assert "Paper Rex" in str(event["SUMMARY"])
        assert client.get("/").status_code == 200
        assert "All Matches" in client.get("/").text
        assert 'data-feed="all.ics"' in client.get("/").text
        assert "<code>all.ics</code>" in client.get("/").text
        assert "data-url=" not in client.get("/").text
        assert client.get("/state.json").status_code == 404
        manifest = client.get("/feeds.json")
        assert manifest.status_code == 200
        assert manifest.json()[0]["file"] == "all.ics"
        assert (
            client.get(
                "/all.ics", headers={"If-None-Match": response.headers["etag"]}
            ).status_code
            == 304
        )


def test_polling_preserves_current_feeds_during_builds_failures_and_retries(
    service_config, backend, tmp_path
):
    app = create_app(
        service_config,
        tmp_path / "data",
        poll_interval_seconds=0.2,
        retry_interval_seconds=0.1,
    )

    with TestClient(app) as client:
        initial = wait_for_health(client, lambda value: value["status"] == "ok")
        response = client.get("/all.ics")
        original, etag = response.content, response.headers["etag"]
        backend.block_next = True
        assert backend.blocked.wait(5)
        assert client.get("/health").json()["ready"]
        assert client.get("/all.ics").content == original
        time.sleep(0.25)
        assert backend.maximum_active == 1
        backend.release.set()
        wait_for_health(
            client, lambda value: value["last_success"] != initial["last_success"]
        )
        unchanged = client.get("/all.ics")
        assert unchanged.content == original
        assert unchanged.headers["etag"] == etag
        backend.blocked.clear()
        backend.release.clear()
        backend.block_next = True
        assert backend.blocked.wait(5)
        successful = client.get("/health").json()
        backend.fail = True
        backend.release.set()
        degraded = wait_for_health(client, lambda value: value["status"] == "degraded")
        assert degraded["ready"]
        assert degraded["last_success"] == successful["last_success"]
        assert client.get("/all.ics").content == original
        assert client.get("/").status_code == 200
        backend.start = "2026-10-17T15:00:00Z"
        backend.fail = False
        recovered = wait_for_health(
            client,
            lambda value: (
                value["status"] == "ok"
                and value["last_success"] != degraded["last_success"]
            ),
        )
        assert recovered["ready"]
        updated = Calendar.from_ical(client.get("/all.ics").content).walk("VEVENT")[0]
        before = Calendar.from_ical(original).walk("VEVENT")[0]
        assert updated.decoded("DTSTART") == datetime(
            2026, 10, 17, 15, tzinfo=timezone.utc
        )
        assert int(updated["SEQUENCE"]) == int(before["SEQUENCE"]) + 1


def test_restart_serves_persisted_publication_before_the_initial_refresh_finishes(
    service_config, backend, tmp_path
):
    data_dir = tmp_path / "data"
    with TestClient(
        create_app(service_config, data_dir, poll_interval_seconds=60)
    ) as client:
        wait_for_health(client, lambda value: value["status"] == "ok")
        published = client.get("/all.ics")

    orphan = data_dir / "generation-interrupted"
    orphan.mkdir()
    (orphan / "index.html").write_text("Incomplete build")
    (data_dir / "next").symlink_to(orphan.name)
    backend.block_next = True
    backend.fail = True
    with TestClient(
        create_app(service_config, data_dir, poll_interval_seconds=60)
    ) as client:
        assert backend.blocked.wait(5)
        assert not orphan.exists()
        assert not (data_dir / "next").is_symlink()
        assert client.get("/health").json() == {
            "status": "ok",
            "ready": True,
            "last_success": None,
        }
        response = client.get("/all.ics")
        assert response.content == published.content
        assert response.headers["etag"] == published.headers["etag"]
        backend.release.set()
        health = wait_for_health(client, lambda value: value["status"] == "degraded")
        assert health["ready"]
        assert client.get("/all.ics").content == published.content


@pytest.mark.parametrize("termination", ["shutdown", "timeout"])
def test_inflight_build_process_is_terminated_and_reaped(
    service_config, backend, tmp_path, monkeypatch, termination
):
    original = asyncio.create_subprocess_exec
    processes = []

    async def record_subprocess(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", record_subprocess)
    if termination == "timeout":
        config = json.loads(service_config.read_text())
        config["settings"]["build_timeout_seconds"] = 0.8
        service_config.write_text(json.dumps(config))
    backend.block_next = True
    app = create_app(
        service_config,
        tmp_path / "data",
        poll_interval_seconds=60,
        retry_interval_seconds=60,
    )

    with TestClient(app) as client:
        assert backend.blocked.wait(5)
        assert len(processes) == 1
        if termination == "timeout":
            health = wait_for_health(
                client, lambda value: value["status"] == "degraded"
            )
            assert not health["ready"]
            assert health["last_success"] is None
            assert client.get("/").status_code == 503

    assert processes[0].returncode is not None
