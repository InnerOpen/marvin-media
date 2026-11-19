"""media-describe: a file gets ONE Recording, found or made, with the file's facts; the same facts go on the file's own metadata exactly as
Studio writes them; doing it again (a retry, a second upload event, a manual ask) never makes a second Recording or a second poster."""

import json
from pathlib import Path

import pytest
from marvin_worker.jobs import HANDLERS, META, JobError, check_args, load_kinds

from mediajobs import audio_measure, ffmpeg, jobs, recording, video
from tests import media_files
from tests.conftest import describe, needs_ffmpeg

ID = "a6e76f6c-1362-43f3-8bfa-c11642c974d2"
SLUG = "rec-a6e76f6c-136"
PACKAGE = json.loads((Path(__file__).resolve().parents[1] / "docs" / "media.marvin-package.json").read_text(encoding="utf-8"))
PACKAGE_FIELDS = {f["key"]: f for b in PACKAGE["blueprints"] if b["slug"] == "recording" for f in b["payload"]["schema_json"]["fields"]}


def _file(marvin, tmp_path, fixture, **kw):
    path, mime = media_files.make(fixture, tmp_path / "files")
    return marvin.add_asset(path, mime=kw.pop("mime", mime), asset_id=kw.pop("asset_id", ID), **kw)


# ── the slug ─────────────────────────────────────────────────────────────────
def test_a_recordings_slug_is_rec_and_the_first_twelve_characters_of_its_files_id():
    assert recording.slug_for(ID) == SLUG == "rec-" + ID[:12]
    assert recording.slug_for(ID.upper()) == SLUG and recording.poster_slug(SLUG) == SLUG + "-poster"


# ── the contract ─────────────────────────────────────────────────────────────
def test_the_kind_is_registered_under_the_media_prefix_and_takes_an_audio_or_video_file():
    assert jobs.DESCRIBE == "media-describe" and "media-describe" in load_kinds("mediajobs.jobs")
    contract = META["media-describe"]
    assert contract["inputs"]["asset"] == {"type": "asset", "required": False, "description": "the audio or video file (its slug or id)", "accepts": ["audio/*", "video/*"]}
    assert contract["needs"] == ["ffmpeg"] and contract["produces"] == ["image/jpeg"]
    ours = [kind for kind, fn in HANDLERS.items() if fn.__module__ == "mediajobs.jobs"]
    assert ours and all(kind.startswith("media-") for kind in ours)  # never the name of another package's kind


def test_a_picture_is_refused_before_anything_runs(marvin, tmp_path):
    marvin.add_asset(None, mime="image/png", asset_id=ID)
    with pytest.raises(JobError, match="takes audio/\\*, video/\\*"):
        check_args("media-describe", {"asset": ID}, ("http://marvin.test", "t"))


# ── a video ──────────────────────────────────────────────────────────────────
@needs_ffmpeg
def test_a_browsers_portrait_webm_gets_a_recording_with_its_facts_a_poster_and_the_same_facts_on_its_metadata(marvin, tmp_path):
    asset = _file(marvin, tmp_path, "phone-webm", name="Recording Nov 18", meta={"recording": {"source": "browser", "mimeType": "video/webm"}})
    out = describe({"asset": ID}, tmp_path / "w1")

    (rec,) = marvin.live()
    assert rec["slug"] == SLUG and rec["title"] == "Recording Nov 18"
    # made with the bare minimum first, then the facts in a second write: a workflow listening for `described` hears a new Recording too
    creates = [w for w in marvin.writes if w == ("POST", "/api/platform/entries")]
    assert len(creates) == 1 and ("PATCH", f"/api/platform/entries/{rec['id']}") in marvin.writes
    data = rec["dataJson"]
    assert data["asset"] == ID and data["kind"] == "video" and data["source"] == "recorded" and data["status"] == "described" and data["job"] == "describe-1"
    assert (data["width"], data["height"], data["has_audio"]) == (120, 160, True)
    assert data["duration_seconds"] == pytest.approx(2.5, abs=0.1) and 0 < data["fps"] <= 240
    assert data["codec"] == "opus" and data["channels"] == 1 and data["format"].startswith("opus · 48 kHz · mono")
    assert -60 <= data["level_db"] < 0 and data["peak_db"] <= 0 and isinstance(data["clipped"], bool) and "level_lufs" in data
    assert data["waveform"]["length"] == 240 and len(data["waveform"]["data"]) == 480

    poster = marvin.assets[data["poster"]]
    assert poster["slug"] == SLUG + "-poster" and poster["origin"] == "derived"
    assert poster["metadataJson"] == {"kind": "recording-poster", "recording": SLUG, "video": ID, "at": 1.0, "derived_from": ID, "worker": {"kind": "media-describe", "job": "describe-1"}}

    facts = asset["metadataJson"]["video"]
    assert facts == video.describe(marvin.files[ID]) and asset["metadataJson"]["recording"]["source"] == "browser"  # the other keys are kept
    assert "audio" not in asset["metadataJson"]
    assert out["result"]["recording"] == SLUG and out["result"]["created"] is True and "waveform" not in out["result"]


@needs_ffmpeg
def test_a_phones_turned_take_reads_portrait(marvin, tmp_path):
    _file(marvin, tmp_path, "turned-mp4")
    describe({"asset": ID}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert (data["width"], data["height"], data["source"]) == (120, 160, "uploaded")


@needs_ffmpeg
def test_a_video_with_no_sound_has_no_levels_or_waveform_but_a_poster(marvin, tmp_path):
    _file(marvin, tmp_path, "silent-webm")
    describe({"asset": ID}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["has_audio"] is False and data["duration_seconds"] == pytest.approx(2.0, abs=0.1) and data["poster"]
    assert not {"level_db", "peak_db", "level_lufs", "waveform", "codec", "format"} & set(data)


@needs_ffmpeg
def test_a_webm_recorded_with_the_camera_off_is_a_sound(marvin, tmp_path):
    asset = _file(marvin, tmp_path, "camera-off-webm")
    describe({"asset": ID}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["kind"] == "audio" and "poster" not in data and "width" not in data  # made as video (its type), corrected once looked at
    assert asset["metadataJson"]["audio"] == audio_measure.describe(marvin.files[ID]) and "video" not in asset["metadataJson"]
    assert not marvin.uploads()


# ── a sound ──────────────────────────────────────────────────────────────────
@needs_ffmpeg
def test_a_stereo_wav_gets_its_make_levels_silences_and_waveform(marvin, tmp_path):
    asset = _file(marvin, tmp_path, "stereo-wav")
    describe({"asset": ID}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["kind"] == "audio" and data["has_audio"] is True and data["codec"] == "pcm_s16le" and (data["sample_rate"], data["channels"]) == (44100, 2)
    assert data["duration_seconds"] == pytest.approx(1.9, abs=0.02)
    assert data["lead_silence"] == pytest.approx(0.5, abs=0.05) and data["tail_silence"] == pytest.approx(0.4, abs=0.05)
    assert data["noise_floor_db"] <= -60 and data["clipped"] is False and data["waveform"]["length"] == 240
    assert asset["metadataJson"] == {"audio": audio_measure.describe(marvin.files[ID])}
    assert not marvin.uploads()


@needs_ffmpeg
def test_a_generated_file_says_so(marvin, tmp_path):
    _file(marvin, tmp_path, "stereo-wav", origin="generated")
    describe({"asset": ID}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["source"] == "generated"


# ── once per file ────────────────────────────────────────────────────────────
@needs_ffmpeg
def test_describing_a_file_again_finds_its_recording_and_writes_nothing(marvin, tmp_path):
    _file(marvin, tmp_path, "phone-webm")
    describe({"asset": ID}, tmp_path / "w1")
    written, downloads = list(marvin.writes), list(marvin.downloads)
    again = describe({"asset": ID}, tmp_path / "w2", job="describe-2")
    assert again["result"]["skipped"] is True and again["result"]["recording"] == SLUG
    assert marvin.writes == written and marvin.downloads == downloads  # not even downloaded
    assert len(marvin.live()) == 1 and len(marvin.uploads()) == 1


@needs_ffmpeg
def test_asked_with_force_it_is_described_again_with_a_new_poster_and_the_old_one_goes_to_the_trash(marvin, tmp_path):
    _file(marvin, tmp_path, "phone-webm")
    describe({"asset": ID}, tmp_path / "w1")
    first = marvin.entry(SLUG)["dataJson"]
    again = describe({"asset": ID, "force": True}, tmp_path / "w2", job="describe-2")
    data = marvin.entry(SLUG)["dataJson"]
    assert again["result"]["created"] is False and len(marvin.live()) == 1
    assert data["poster"] != first["poster"] and marvin.assets[first["poster"]]["trashedAt"] and not marvin.assets[data["poster"]]["trashedAt"]
    assert {k: v for k, v in data.items() if k not in ("poster", "job")} == {k: v for k, v in first.items() if k not in ("poster", "job")}


@needs_ffmpeg
def test_a_new_poster_under_a_slug_marvin_holds_takes_the_next_free_one(marvin, tmp_path):
    """Marvin refuses a second asset under a slug (one in the Trash included), so asking again with force would find the poster slug taken."""
    _file(marvin, tmp_path, "phone-webm")
    marvin.strict_asset_slugs = True
    describe({"asset": ID}, tmp_path / "w1")
    first = marvin.entry(SLUG)["dataJson"]
    describe({"asset": ID, "force": True}, tmp_path / "w2", job="describe-2")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["poster"] != first["poster"] and marvin.assets[data["poster"]]["slug"] == f"{SLUG}-poster-2"
    assert marvin.assets[first["poster"]]["trashedAt"] and not marvin.assets[data["poster"]]["trashedAt"]
    describe({"asset": ID, "force": True}, tmp_path / "w3", job="describe-3")  # and the old one is in the Trash, still holding its slug
    assert marvin.assets[marvin.entry(SLUG)["dataJson"]["poster"]]["slug"] == f"{SLUG}-poster-3"


@needs_ffmpeg
def test_a_new_poster_that_cannot_be_made_leaves_the_old_one_in_place(marvin, tmp_path, monkeypatch):
    _file(marvin, tmp_path, "phone-webm")
    describe({"asset": ID}, tmp_path / "w1")
    old = marvin.entry(SLUG)["dataJson"]["poster"]

    def no_frame(*a, **k):
        raise ffmpeg.FfmpegError("no frame")

    monkeypatch.setattr(video, "poster", no_frame)
    out = describe({"asset": ID, "force": True}, tmp_path / "w2", job="describe-2")
    assert marvin.entry(SLUG)["dataJson"]["poster"] == old and not marvin.assets[old]["trashedAt"] and "poster" not in out["result"]


@needs_ffmpeg
def test_a_job_tried_again_after_its_last_write_failed_uses_the_poster_it_already_uploaded(marvin, tmp_path):
    _file(marvin, tmp_path, "phone-webm")
    marvin.fail_entry_patch = True
    with pytest.raises(Exception, match="500"):
        describe({"asset": ID}, tmp_path / "w1")
    assert marvin.entry(SLUG)["dataJson"] == {"asset": ID, "kind": "video", "status": "new"} and len(marvin.uploads()) == 1
    marvin.fail_entry_patch = False
    describe({"asset": ID}, tmp_path / "w2", job="describe-1")
    (poster,) = marvin.uploads()
    assert marvin.entry(SLUG)["dataJson"]["poster"] == poster["id"] and marvin.entry(SLUG)["dataJson"]["status"] == "described"


@needs_ffmpeg
def test_a_recording_made_by_hand_for_the_file_is_the_one_used(marvin, tmp_path):
    _file(marvin, tmp_path, "stereo-wav")
    marvin.add_entry("recording", "my-interview", {"asset": ID, "notes": "mine", "source": "uploaded"})
    out = describe({"asset": ID}, tmp_path / "w")
    assert out["result"]["recording"] == "my-interview" and out["result"]["created"] is False and len(marvin.live()) == 1
    data = marvin.entry("my-interview")["dataJson"]
    assert data["notes"] == "mine" and data["status"] == "described" and data["duration_seconds"] > 0


@needs_ffmpeg
def test_two_jobs_racing_for_one_file_end_with_one_recording(marvin, tmp_path):
    _file(marvin, tmp_path, "stereo-wav")
    marvin.before_create = lambda slug: marvin.add_entry("recording", slug, {"asset": ID, "kind": "audio", "status": "new"})  # the rival lands first
    out = describe({"asset": ID}, tmp_path / "w")
    (rec,) = marvin.live()
    assert rec["slug"] == SLUG and out["result"]["recording"] == SLUG and out["result"]["created"] is False
    assert [e["status"] for e in marvin.entries if e["slug"] == SLUG + "-2"] == ["trashed"]  # this job's copy
    assert rec["dataJson"]["status"] == "described"


@needs_ffmpeg
def test_a_recording_in_the_trash_holding_the_slug_leaves_the_new_one_its_own(marvin, tmp_path):
    _file(marvin, tmp_path, "stereo-wav")
    marvin.add_entry("recording", SLUG, {"asset": ID}, status="trashed")
    out = describe({"asset": ID}, tmp_path / "w")
    (rec,) = marvin.live()
    assert rec["slug"] == SLUG + "-2" == out["result"]["recording"] and out["result"]["created"] is True


@needs_ffmpeg
def test_describing_again_never_undoes_a_transcription(marvin, tmp_path):
    _file(marvin, tmp_path, "stereo-wav")
    marvin.add_entry("recording", SLUG, {"asset": ID, "kind": "audio", "status": "transcribed", "text": "hello"})
    describe({"asset": ID, "force": True}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["status"] == "transcribed" and data["text"] == "hello" and data["duration_seconds"] > 0


@needs_ffmpeg
def test_a_recording_can_be_named_instead_of_its_file(marvin, tmp_path):
    _file(marvin, tmp_path, "stereo-wav")
    marvin.add_entry("recording", SLUG, {"asset": ID, "kind": "audio", "status": "new"})
    out = describe({"recording": SLUG}, tmp_path / "w")
    assert out["result"]["recording"] == SLUG and marvin.entry(SLUG)["dataJson"]["status"] == "described"
    with pytest.raises(JobError, match="another file"):
        describe({"recording": SLUG, "asset": "something-else"}, tmp_path / "w2")


# ── what it refuses ──────────────────────────────────────────────────────────
def test_it_needs_a_file_or_a_recording(marvin, tmp_path):
    with pytest.raises(JobError, match="needs args.asset"):
        describe({}, tmp_path / "w")


def test_a_picture_or_a_file_in_the_trash_is_refused_and_no_recording_is_made(marvin, tmp_path):
    marvin.add_asset(None, mime="image/png", name="cat.png", asset_id=ID)
    with pytest.raises(JobError, match="not a recording"):
        describe({"asset": ID}, tmp_path / "w")
    marvin.assets[ID].update(mimeType="audio/mpeg", trashedAt="2026-11-01T00:00:00Z")
    with pytest.raises(JobError, match="in the Trash"):
        describe({"asset": ID}, tmp_path / "w")
    assert not marvin.live() and not marvin.writes


def test_a_workspace_without_the_media_package_is_told_to_install_it(monkeypatch, tmp_path):
    from tests.fake_marvin import FakeMarvin

    bare = FakeMarvin(types={"job": "type-job"}).wire(monkeypatch)
    bare.add_asset(tmp_path / "x.wav", mime="audio/wav", asset_id=ID)
    with pytest.raises(JobError, match="install the Media package"):
        describe({"asset": ID}, tmp_path / "w")


@needs_ffmpeg
def test_a_file_that_cannot_be_read_marks_its_recording_failed(marvin, tmp_path):
    (tmp_path / "broken.mp3").write_bytes(b"not a sound at all" * 50)
    marvin.add_asset(tmp_path / "broken.mp3", mime="audio/mpeg", asset_id=ID)
    with pytest.raises(JobError, match="could not read"):
        describe({"asset": ID}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"] == {"asset": ID, "kind": "audio", "status": "failed", "job": "describe-1"}


# ── what it writes fits the package ──────────────────────────────────────────
def test_every_number_the_worker_can_write_fits_its_field():
    """The floors and ceilings the measures clamp to are inside the Recording's limits, so Marvin never refuses a write (the v228 lesson)."""
    fields = PACKAGE_FIELDS
    assert fields["fps"]["max"] >= video.MAX_FPS
    assert fields["noise_floor_db"]["min"] <= audio_measure.NOISE_FLOOR_LOW and fields["level_db"]["min"] <= audio_measure.LEVEL_FLOOR
    assert fields["level_lufs"]["min"] <= -70.0  # parse_loudness reads -inf as -70


@needs_ffmpeg
@pytest.mark.parametrize("fixture", list(media_files.FIXTURES))
def test_every_field_the_worker_writes_is_a_field_of_the_recording_type_within_its_limits(fixture, marvin, tmp_path):
    _file(marvin, tmp_path, fixture)
    describe({"asset": ID}, tmp_path / "w")
    for key, value in marvin.entry(SLUG)["dataJson"].items():
        field = PACKAGE_FIELDS[key]
        if field["type"] == "number":
            assert isinstance(value, (int, float)) and field["min"] <= value <= field["max"], (key, value)
        elif field["type"] == "select":
            assert value in field["options"], (key, value)
        elif field["type"] == "boolean":
            assert isinstance(value, bool), key


# ── the pure part ────────────────────────────────────────────────────────────
def test_where_a_file_came_from():
    assert recording.source_of({"metadataJson": {"recording": {"source": "browser"}}}) == "recorded"
    assert recording.source_of({"metadataJson": {}, "origin": "generated"}) == "generated"
    assert recording.source_of({"metadataJson": {"words": [{"text": "a"}]}, "origin": "upload"}) == "uploaded"


def test_the_sound_inside_a_video_is_described_as_a_sound_file_is():
    probed = {"streams": [{"codec_type": "video"}, {"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000", "channels": 1}], "format": {"bit_rate": "64000"}}
    assert recording.sound_make(probed) == {"codec": "opus", "sample_rate": 48000, "channels": 1, "format": "opus · 48 kHz · mono · 64 kbit/s"}
    assert recording.sound_make({"streams": [{"codec_type": "video"}]}) == {}
