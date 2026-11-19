"""v5: Recording and Transcript are made by Media, not by hand, so a new install does not offer them in the New menu (Marvin #312, capabilities offer_in_new)."""

import json
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "docs" / "media.marvin-package.json"


def test_recording_and_transcript_are_not_offered_in_the_new_menu():
    types = {b["slug"]: b["payload"] for b in json.loads(PACKAGE.read_text(encoding="utf-8"))["blueprints"] if b["kind"] == "entry_type"}
    assert set(types) == {"recording", "transcript"}
    for slug, payload in types.items():
        assert payload["capabilities_json"]["offer_in_new"] is False, slug
