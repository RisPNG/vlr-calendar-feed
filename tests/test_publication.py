import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.main import PublishedCalendars


def test_publication_switch_keeps_response_headers_and_body_in_one_generation(
    tmp_path, monkeypatch
):
    previous = tmp_path / "generation-old"
    current_generation = tmp_path / "generation-new"
    previous.mkdir()
    current_generation.mkdir()
    (previous / "all.ics").write_bytes(b"old")
    (current_generation / "all.ics").write_bytes(b"newer payload")
    current = tmp_path / "current"
    current.symlink_to(previous.name, target_is_directory=True)
    app = FastAPI()
    app.mount("/", PublishedCalendars(directory=current, check_dir=False))
    original = FileResponse.__call__
    switched = False

    async def switch_after_stat(response, scope, receive, send):
        nonlocal switched
        if not switched:
            pointer = tmp_path / "next"
            pointer.symlink_to(current_generation.name, target_is_directory=True)
            os.replace(pointer, current)
            switched = True
        return await original(response, scope, receive, send)

    monkeypatch.setattr(FileResponse, "__call__", switch_after_stat)
    with TestClient(app) as client:
        before = client.get("/all.ics")
        assert before.status_code == 200
        assert before.content == b"old"
        assert int(before.headers["content-length"]) == len(before.content)
        assert current.resolve() == current_generation

        after = client.get(
            "/all.ics", headers={"If-None-Match": before.headers["etag"]}
        )
        assert after.status_code == 200
        assert after.content == b"newer payload"
        assert int(after.headers["content-length"]) == len(after.content)
        assert after.headers["etag"] != before.headers["etag"]
        assert (
            client.get(
                "/all.ics", headers={"If-None-Match": after.headers["etag"]}
            ).status_code
            == 304
        )
