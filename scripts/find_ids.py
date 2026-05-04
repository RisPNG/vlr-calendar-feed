#!/usr/bin/env python3
"""Find VLR team/player IDs using vlrdevapi search helpers."""

from __future__ import annotations

import argparse
import sys

try:
    import vlrdevapi as vlr
except Exception as exc:  # pragma: no cover
    raise SystemExit("vlrdevapi is not installed. Run: python -m pip install -r requirements.txt") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Search VLR IDs for teams or players.")
    parser.add_argument("query", help="Search query, e.g. 'paper rex' or 'something'")
    parser.add_argument("--type", choices=("team", "player"), default="team")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if args.type == "team":
        results = vlr.search.search_teams(args.query, limit=args.limit)
        for item in results:
            print(f"team_id={getattr(item, 'team_id', '')}\tname={getattr(item, 'name', '')}\turl={getattr(item, 'url', '')}")
    else:
        results = vlr.search.search_players(args.query, limit=args.limit)
        for item in results:
            print(f"player_id={getattr(item, 'player_id', '')}\tign={getattr(item, 'ign', '')}\treal_name={getattr(item, 'real_name', '')}\turl={getattr(item, 'url', '')}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
