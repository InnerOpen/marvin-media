"""Describe parity (design §8.3): the same file gives the same numbers from media-describe as from Screenplay Studio's own jobs, so a Recording
holds what Studio's fields hold today and a later mirror (P7) changes nothing. Compared on the file's own metadata (`video` / `audio`, Studio's
describe-video on a file alone) and on the Recording's fields (describe-audio, measure-level, make-waveform, and describe-video's clip facts).

Against Studio itself when STUDIO_RENDER names a ScreenplayStudio checkout's render folder (exact: the same file on the same machine); else
against tests/data/studio_golden.json, written by scripts/studio_golden.py from Studio 09bef82 (a little room for another ffmpeg build)."""

import json
import os
import sys
from pathlib import Path

import pytest

from tests import media_files
from tests.conftest import describe, needs_ffmpeg

GOLDEN = json.loads((Path(__file__).parent / "data" / "studio_golden.json").read_text(encoding="utf-8"))
ID = "0b9d4c2e-77aa-4c55-9a51-3f0c2d1e8a90"
LIVE = os.environ.get("STUDIO_RENDER")


def _studio(name: str, file: Path, mime: str, work: Path, has_audio: bool) -> dict:
    if not LIVE:
        return GOLDEN["files"][name]
    if LIVE not in sys.path:
        sys.path.insert(0, LIVE)
    from tests import studio_side

    return studio_side.run(file, mime, work, has_audio)


def _same(mine, theirs, where: str) -> None:
    if LIVE:
        assert mine == theirs, where
    elif isinstance(theirs, dict):
        assert set(mine) == set(theirs), where
        for key in theirs:
            _same(mine[key], theirs[key], f"{where}.{key}")
    elif isinstance(theirs, list):
        assert len(mine) == len(theirs) and all(abs(a - b) <= 3 for a, b in zip(mine, theirs)), where  # a waveform: 8-bit peaks
    elif isinstance(theirs, float):
        assert mine == pytest.approx(theirs, abs=0.6), where  # dB to a tenth, seconds to a millisecond: room for another ffmpeg build
    else:
        assert mine == theirs, where


@needs_ffmpeg
@pytest.mark.parametrize("name", list(media_files.FIXTURES))
def test_a_file_gives_the_same_numbers_as_studios_jobs(name, marvin, tmp_path):
    file, mime = media_files.make(name, tmp_path / "files")
    asset = marvin.add_asset(file, mime=mime, asset_id=ID)
    describe({"asset": ID}, tmp_path / "media")
    (rec,) = marvin.live()
    data = rec["dataJson"]
    studio = _studio(name, file, mime, tmp_path / "studio", data["has_audio"])

    key = studio["asset"]["key"]
    assert data["kind"] == key and set(asset["metadataJson"]) == {key}
    _same(asset["metadataJson"][key], studio["asset"]["facts"], f"{name}: metadata.{key}")  # the file's own metadata, as describe-video writes it
    _same(data["duration_seconds"], studio["asset"]["facts"]["duration_seconds"], f"{name}: duration")
    if key == "video":  # describe-video's clip_seconds, clip_width, clip_height, clip_has_audio (and the fps it puts on the metadata)
        facts = studio["asset"]["facts"]
        _same({k: data.get(k) for k in ("width", "height", "fps", "has_audio")}, {k: facts[k] for k in ("width", "height", "fps", "has_audio")}, f"{name}: picture")
    else:
        _same({k: data[k] for k in ("codec", "sample_rate", "channels", "format")}, {k: studio["asset"]["facts"][k] for k in ("codec", "sample_rate", "channels", "format")}, f"{name}: make")
    if not data["has_audio"]:
        assert "audio" not in studio and not {"level_db", "peak_db", "waveform"} & set(data)
        return
    heard, level = studio["audio"], studio["level"]
    _same(data["format"], heard["format"], f"{name}: format")  # describe-audio's one line, for the sound in a video too
    for mine, theirs in (("level_lufs", "level_lufs"), ("peak_db", "peak_db"), ("noise_floor_db", "noise_floor_db"), ("lead_silence", "lead_silence"), ("tail_silence", "tail_silence")):
        _same(data[mine], heard[theirs], f"{name}: {mine} (describe-audio)")
    assert data["clipped"] is (heard["clipped"] == "yes"), name  # Studio's select (yes/no) is a checkbox here
    _same(data["level_db"], level["level_db"], f"{name}: level_db (measure-level)")
    _same(data["peak_db"], level["peak_db"], f"{name}: peak_db (measure-level)")
    _same(data["waveform"], studio["waveform"]["waveform"], f"{name}: waveform (make-waveform)")


def test_the_golden_numbers_came_from_studios_describe_code():
    assert GOLDEN["studio"] == "09bef82" and set(GOLDEN["files"]) == set(media_files.FIXTURES)
