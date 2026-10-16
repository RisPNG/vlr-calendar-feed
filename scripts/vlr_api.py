"""HTTP access to the configured VLR API, shared by every calendar in a run."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class VlrApiClient:
    def __init__(self, base_url: str, timeout: float = 120, interval: float = 1):
        self.base_url = os.environ.get("VLR_API_BASE_URL", base_url).rstrip("/")
        self.timeout = timeout
        self.interval = interval
        self.responses: dict[str, Any] = {}
        self.next_request = 0.0

    def get(self, path: str, **query: Any) -> Any:
        parameters = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in query.items()
        }
        url = f"{self.base_url}/{path.lstrip('/')}"
        if parameters:
            url += "?" + urlencode(sorted(parameters.items()))
        if url in self.responses:
            return self.responses[url]
        delay = self.next_request - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        headers = {"User-Agent": "vlr-calendar-feed", "Accept": "application/json"}
        if token := os.environ.get("VLR_API_KEY"):
            headers["Authorization"] = f"Bearer {token}"
        self.next_request = time.monotonic() + self.interval
        with urlopen(Request(url, headers=headers), timeout=self.timeout) as response:
            data = json.load(response)
        self.responses[url] = data
        return data
