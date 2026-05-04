#!/usr/bin/env python3
"""Find VLR team/player IDs using vlrdevapi search."""

from __future__ import annotations

import argparse
from typing import Any

import vlrdevapi as vlr


def get_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def print_rows(kind: str, rows: list[Any]) -> None:
    if not rows:
        print(f"No {kind} results found.")
        return
    for item in rows:
        item_id = get_attr(item, "id", "team_id", "player_id", default="?")
        name = get_attr(item, "name", "ign", "handle", default="?")
        country = get_attr(item, "country", default="")
        print(f"{kind[:-1].title()} ID: {item_id} | {name} {f'({country})' if country else ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search VLR IDs for config/calendars.json")
    parser.add_argument("query", help="Team or player name, for example 'paper rex' or 'tenz'")
    parser.add_argument("--type", choices=["team", "player", "all"], default="all")
    args = parser.parse_args()

    if args.type in {"team", "all"}:
        teams = vlr.search.search_teams(args.query)
        print_rows("teams", list(teams)[:10])

    if args.type in {"player", "all"}:
        players = vlr.search.search_players(args.query)
        print_rows("players", list(players)[:10])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
