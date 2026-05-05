#!/usr/bin/env python3
"""Find VLR team/player IDs using vlrdevapi search helpers."""

from __future__ import annotations

import argparse
import sys
from typing import Any

try:
    import vlrdevapi as vlr
except Exception as exc:  # pragma: no cover
    raise SystemExit("vlrdevapi is not installed. Run: python -m pip install -r requirements.txt") from exc


def get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def print_result(item: Any) -> None:
    item_id = get_attr(item, "id", "team_id", "player_id", default="?")
    name = get_attr(item, "name", "team_name", "player_name", default="?")
    url = get_attr(item, "url", "link", default="")
    print(f"{item_id}\t{name}\t{url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find VLR team/player IDs.")
    parser.add_argument("query", help="Search query, such as 'paper rex' or 'ayumiii'")
    parser.add_argument("--type", choices=["team", "player"], required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if args.type == "team":
        results = vlr.search.search_teams(args.query, limit=args.limit)
    else:
        results = vlr.search.search_players(args.query, limit=args.limit)

    for item in results:
        print_result(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
