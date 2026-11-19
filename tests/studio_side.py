"""What Screenplay Studio's own jobs write for a file, run from a Studio checkout: the other half of the parity check. Its describe-video (on a
file alone: the asset's `video` or `audio` metadata), describe-audio, measure-level and make-waveform (on an entry holding the file), each with
Marvin replaced by a stand-in that hands over the one file and keeps what the job writes.

Needs `storyrender` importable: STUDIO_RENDER=<ScreenplayStudio>/render (scripts/studio_golden.py and tests/test_parity.py add it to the path)."""

import json
import shutil
from pathlib import Path

from marvin_worker import marvin_api
from marvin_worker.jobs import Context

ASSET = "0b9d4c2e-77aa-4c55-9a51-3f0c2d1e8a90"
ENTRY = "the-entry"


def run(file: Path, mime: str, work: Path, has_audio: bool) -> dict:
    """{asset: {key, facts}, audio: describe-audio's fields, level: measure-level's, waveform: make-waveform's}; the last three only for a file
    with sound (Studio's jobs refuse one without)."""
    from storyrender import jobs  # from the Studio checkout on the path

    record = {"id": ASSET, "slug": "the-file", "name": file.name, "mimeType": mime, "originalFilename": file.name, "metadataJson": {}}
    kept: dict = {}
    saved = {name: getattr(marvin_api, name) for name in ("asset", "download_asset", "request", "query", "update_entry")}

    def request(creds, path, method="GET", data=None, content_type=None, **_):
        assert method == "PATCH" and path == f"/api/platform/assets/{ASSET}", (method, path)
        kept["metadata"] = json.loads(data)["metadata"]
        return b"{}"

    marvin_api.asset = lambda creds, ref: record
    marvin_api.download_asset = lambda creds, ref, dest: shutil.copy(file, dest)
    marvin_api.request = request
    marvin_api.query = lambda creds, spec, limit=100: [{"id": "e1", "slug": ENTRY, "title": "t", "dataJson": {"audio": ASSET}}]
    marvin_api.update_entry = lambda creds, slug, changes: kept.setdefault("entry", {}).update(changes) or {}
    try:
        out: dict = {}
        loose = jobs.describe_video({"job": "j", "args": {"asset": ASSET}}, Context(creds=("http://m", "t"), work=_fresh(work, "asset"), name="j"))["result"]
        key = loose["kind"]
        out["asset"] = {"key": key, "facts": kept["metadata"][key]}
        if has_audio:
            for name, fn in (("audio", jobs.describe_audio), ("level", jobs.measure_level), ("waveform", jobs.make_waveform)):
                kept["entry"] = {}
                fn({"job": "j", "args": {"entry": ENTRY}}, Context(creds=("http://m", "t"), work=_fresh(work, name), name="j"))
                out[name] = dict(kept["entry"])
        return out
    finally:
        for name, fn in saved.items():
            setattr(marvin_api, name, fn)


def _fresh(work: Path, name: str) -> Path:
    folder = work / f"studio-{name}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
