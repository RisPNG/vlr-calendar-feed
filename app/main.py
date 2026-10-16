"""Serve calendar subscriptions and refresh them in one Render service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from scripts.build_calendar import CONFIG_PATH, ROOT_DIR

logger = logging.getLogger("uvicorn.error")


class PublishedCalendars(StaticFiles):
    """Pin each response to one generation before inspecting or opening its files."""

    async def get_response(self, path, scope):
        publication = StaticFiles(directory=Path(self.directory).resolve(), html=True)
        return await publication.get_response(path, scope)


class CalendarService:
    def __init__(
        self,
        config_path: Path,
        data_dir: Path,
        interval: float,
        retry_interval: float,
        timeout: float,
    ):
        self.config_path = config_path.resolve()
        self.data_dir = data_dir.resolve()
        self.current = self.data_dir / "current"
        self.interval, self.retry_interval, self.timeout = (
            interval,
            retry_interval,
            timeout,
        )
        self.process: asyncio.subprocess.Process | None = None
        self.last_success: datetime | None = None
        self.failed = False

    async def refresh(self) -> None:
        generation = Path(tempfile.mkdtemp(prefix="generation-", dir=self.data_dir))
        pointer = self.data_dir / "next"
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "scripts.build_calendar",
                "--config",
                str(self.config_path),
                "--output-dir",
                str(generation),
                "--previous-dir",
                str(self.current),
                "--state-file",
                str(self.data_dir / "state.json"),
                cwd=ROOT_DIR,
            )
            result = await asyncio.wait_for(self.process.wait(), timeout=self.timeout)
            if result:
                raise RuntimeError(f"Calendar build exited with status {result}")
            previous = self.current.resolve() if self.current.exists() else None
            unchanged = previous is not None and {
                path.name for path in previous.iterdir()
            } == {path.name for path in generation.iterdir()}
            for path in generation.iterdir():
                old = self.current / path.name
                if old.exists() and old.read_bytes() == path.read_bytes():
                    shutil.copystat(old, path)
                else:
                    unchanged = False
            if not unchanged:
                pointer.unlink(missing_ok=True)
                pointer.symlink_to(generation.name, target_is_directory=True)
                os.replace(pointer, self.current)
                for old in self.data_dir.glob("generation-*"):
                    if old not in {generation, previous}:
                        shutil.rmtree(old)
            self.last_success = datetime.now(timezone.utc)
            self.failed = False
            logger.info(
                "Calendar refresh succeeded; publication changed: %s", not unchanged
            )
        finally:
            if self.process is not None and self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                await self.process.wait()
            self.process = None
            pointer.unlink(missing_ok=True)
            if not self.current.exists() or generation != self.current.resolve():
                shutil.rmtree(generation, ignore_errors=True)

    async def run(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.refresh()
            except Exception:
                self.failed = True
                logger.exception(
                    "Calendar refresh failed; keeping the current publication"
                )
            delay = (
                self.retry_interval
                if self.failed
                else max(
                    0, self.interval - (asyncio.get_running_loop().time() - started)
                )
            )
            await asyncio.sleep(delay)


def create_app(
    config_path: Path = CONFIG_PATH,
    data_dir: Path | None = None,
    poll_interval_seconds: float | None = None,
    retry_interval_seconds: float | None = None,
) -> FastAPI:
    config_path = Path(config_path)
    settings = json.loads(config_path.read_text(encoding="utf-8")).get("settings", {})
    data_dir = Path(
        data_dir
        or os.environ.get("CALENDAR_DATA_DIR", ROOT_DIR / ".calendar-state" / "service")
    )
    interval = (
        poll_interval_seconds
        if poll_interval_seconds is not None
        else float(settings.get("poll_interval_seconds", 7200))
    )
    retry_interval = (
        retry_interval_seconds
        if retry_interval_seconds is not None
        else float(settings.get("retry_interval_seconds", 300))
    )
    service = CalendarService(
        config_path,
        data_dir,
        interval,
        retry_interval,
        float(settings.get("build_timeout_seconds", 1800)),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "next").unlink(missing_ok=True)
        for generation in data_dir.glob("generation-*"):
            if (
                not service.current.exists()
                or generation.resolve() != service.current.resolve()
            ):
                shutil.rmtree(generation)
        task = asyncio.create_task(service.run())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.get("/health")
    async def health():
        return {
            "status": "degraded"
            if service.failed
            else ("ok" if service.current.exists() else "starting"),
            "ready": service.current.exists(),
            "last_success": service.last_success,
        }

    @app.middleware("http")
    async def require_publication(request: Request, call_next):
        if request.url.path != "/health" and not service.current.exists():
            return JSONResponse(
                {"detail": "Calendars are being prepared. Please try again shortly."},
                status_code=503,
                headers={"Retry-After": "30"},
            )
        return await call_next(request)

    app.mount(
        "/",
        PublishedCalendars(
            directory=service.current, html=True, check_dir=False, follow_symlink=True
        ),
    )
    return app


app = create_app()
