#!/usr/bin/env python3
"""Find VLR team/player IDs through the configured API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.vlr_api import VlrApiClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Team or player name")
    parser.add_argument("--type", choices=["team", "player"], required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "calendars.json",
    )
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding="utf-8"))["settings"]
    client = VlrApiClient(
        settings["api_base_url"],
        timeout=settings.get("request_timeout_seconds", 120),
        interval=settings.get("request_interval_seconds", 1),
    )
    results = client.get(
        "/api/v1/search/", search_category=f"{args.type}s", search_term=args.query
    )
    for item in results[: args.limit]:
        print(
            f"{item['id']}\t{item['name']}\thttps://www.vlr.gg/{args.type}/{item['id']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
