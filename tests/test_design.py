import json
from datetime import datetime, timezone
from html.parser import HTMLParser

import scripts.build_calendar as build


class Page(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.text = []
        self.captured = {}
        self.capture = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        if tag in {"title", "h1", "h2", "code"}:
            self.capture = (tag, [])

    def handle_endtag(self, tag):
        if self.capture and self.capture[0] == tag:
            self.captured.setdefault(tag, []).append("".join(self.capture[1]))
            self.capture = None

    def handle_data(self, data):
        self.text.append(data)
        if self.capture:
            self.capture[1].append(data)


def test_calendar_index_groups_all_source_types_and_preserves_subscription_actions():
    manifest = [
        {
            "slug": source_type,
            "name": name,
            "description": f"Follow {name}.",
            "type": source_type,
            "match_count": count,
            "file": f"{source_type}.ics",
        }
        for count, (source_type, name) in enumerate(
            [
                ("player", "Player One"),
                ("team", "Paper Rex"),
                ("event", "Pacific Stage 1"),
                ("series", "Champions Tour"),
                ("global", "All Valorant Matches"),
            ],
            start=1,
        )
    ]
    page = Page(
        build.render_index(
            manifest,
            {"title": "Match calendars", "description": "Choose a subscription."},
        )
    )

    assert page.captured["title"] == ["Match calendars"]
    assert len(page.captured["h1"]) == 1
    assert sum(tag == "main" for tag, _ in page.elements) == 1
    assert sum(tag == "article" for tag, _ in page.elements) == len(manifest)
    section_headings = page.captured["h2"]
    group_positions = [
        next(
            index
            for index, heading in enumerate(section_headings)
            if heading.strip().startswith(label)
        )
        for label in ["All matches", "Competitions", "Events", "Teams", "Players"]
    ]
    assert group_positions == sorted(group_positions)
    assert any(tag == "nav" and attrs.get("aria-label") for tag, attrs in page.elements)

    ids = [attrs["id"] for _, attrs in page.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    fragments = {
        attrs["href"][1:]
        for tag, attrs in page.elements
        if tag == "a" and attrs.get("href", "").startswith("#")
    }
    assert fragments
    assert fragments <= set(ids)

    filenames = {feed["file"] for feed in manifest}
    assert {
        attrs["data-feed"]
        for tag, attrs in page.elements
        if tag == "button" and "data-feed" in attrs
    } == filenames
    assert filenames <= {
        attrs.get("href") for tag, attrs in page.elements if tag == "a"
    }
    assert filenames <= set(page.captured["code"])
    assert not any("data-url" in attrs for _, attrs in page.elements)


def test_calendar_index_escapes_authored_content_and_handles_no_enabled_sources():
    title = 'Calendars <img src="x" onerror="alert(1)"> & friends'
    description = 'Matches <script>alert("bad")</script> & schedules'
    manifest = [
        {
            "slug": "paper-rex",
            "name": title,
            "description": description,
            "type": "team",
            "match_count": 1,
            "file": "paper-rex.ics",
        }
    ]
    page = Page(build.render_index(manifest, {"title": title, "description": description}))
    assert page.captured["title"] == [title]
    assert title in "".join(page.text)
    assert description in "".join(page.text)
    assert not any(tag == "img" for tag, _ in page.elements)
    assert all(attrs.get("src") == "enhance.js" for tag, attrs in page.elements if tag == "script")
    assert not any(
        attribute.startswith("on") for _, attrs in page.elements for attribute in attrs
    )

    empty = Page(build.render_index([], {"title": "Calendars", "description": "Subscriptions"}))
    assert len(empty.captured["h1"]) == 1
    assert not any(tag in {"article", "button"} for tag, _ in empty.elements)
    assert "No calendars are enabled." in "".join(empty.text)
    ids = {attrs["id"] for _, attrs in empty.elements if "id" in attrs}
    assert all(
        attrs["href"][1:] in ids
        for tag, attrs in empty.elements
        if tag == "a" and attrs.get("href", "").startswith("#")
    )


def test_build_publishes_referenced_design_assets_with_stable_publication(tmp_path, monkeypatch):
    monkeypatch.delenv("VLR_API_BASE_URL", raising=False)
    config = {
        "site": {"title": "Match calendars", "description": "Calendar subscriptions"},
        "settings": {"api_base_url": "https://api.example.test"},
        "calendars": [
            {
                "slug": "disabled-team",
                "name": "Hidden team",
                "type": "team",
                "team_id": 624,
                "enabled": False,
            }
        ],
    }
    config_path = tmp_path / "calendars.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_dir = tmp_path / "public"
    paths = {
        "config_path": config_path,
        "output_dir": output_dir,
        "state_file": tmp_path / "state.json",
        "now": datetime(2026, 10, 19, tzinfo=timezone.utc),
    }

    assert build.build(**paths) == 0
    page = Page((output_dir / "index.html").read_text(encoding="utf-8"))
    assets = {
        attrs["href"]
        for tag, attrs in page.elements
        if tag == "link" and attrs.get("rel") == "stylesheet"
    } | {
        attrs["src"]
        for tag, attrs in page.elements
        if tag == "script" and attrs.get("src")
    }
    assert assets == {"style.css", "enhance.js"}
    for filename in assets:
        assert (output_dir / filename).read_bytes() == (build.ROOT_DIR / "scripts" / filename).read_bytes()
    assert json.loads((output_dir / "feeds.json").read_text()) == []
    assert "Hidden team" not in "".join(page.text)
    assert not (output_dir / "disabled-team.ics").exists()
    assert not (output_dir / "state.json").exists()

    previous = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output_dir.iterdir()}
    assert build.build(**paths) == 0
    assert previous == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output_dir.iterdir()}
