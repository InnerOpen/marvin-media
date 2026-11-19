"""media-transcribe: what is said in a Recording's file goes on the Recording (language, text, words, status) and, as files, on its Transcript;
a file with no speech or no sound says so without failing; a recording too long, a made voice or one heard already is left alone (unless
`force`); a Recording not described yet is described first. The model is a stand-in here (as in Studio's transcribe tests); one test at the
end runs the real one where it is installed and cached."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from marvin_worker.jobs import HANDLERS, META, check_args, load_kinds

from mediajobs import jobs, text, transcribe
from tests import media_files
from tests.conftest import describe, needs_ffmpeg
from tests.conftest import transcribe as run

ID = "a6e76f6c-1362-43f3-8bfa-c11642c974d2"
SLUG = "rec-a6e76f6c-136"
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = json.loads((ROOT / "docs" / "media.marvin-package.json").read_text(encoding="utf-8"))
FIELDS = {b["slug"]: {f["key"]: f for f in b["payload"]["schema_json"]["fields"]} for b in PACKAGE["blueprints"] if b["kind"] == "entry_type"}

SAID = [  # (segment text, start, end, [(word, start, end)])
    ("Hello, I'm Jared.", 0.5, 1.8, [(" Hello,", 0.52, 0.9), (" I'm", 1.0, 1.2), (" Jared.", 1.2, 1.7123)]),
    ("This is the take.", 3.4, 4.6, [(" This", 3.4, 3.6), (" is", 3.6, 3.7), (" the", 3.7, 3.85), (" take.", 3.85, 4.6)]),
]


class FakeModel:
    """Yields segments, each with its words, as faster-whisper does with word_timestamps: lazily. `said=[]` is what it gives a tone or a hush."""

    def __init__(self, said=SAID, language="en"):
        self.said, self.language, self.calls = said, language, []

    def transcribe(self, path, **kw):
        self.calls.append((path, kw))
        segments = (
            SimpleNamespace(text=t, start=s, end=e, words=[SimpleNamespace(word=w, start=ws, end=we) for w, ws, we in words]) for t, s, e, words in self.said
        )  # fmt: skip
        return segments, SimpleNamespace(language=self.language, language_probability=0.97, duration=5.0)


class Deaf:
    def transcribe(self, *a, **kw):
        raise AssertionError("the model must not be asked")


@pytest.fixture
def heard(monkeypatch):
    """The stand-in model, and a file that has a sound stream (the real check needs av, which the tests may not have)."""
    model = FakeModel()
    monkeypatch.setattr(transcribe, "load_model", lambda name: model)
    monkeypatch.setattr(transcribe, "has_sound", lambda path: True)
    return model


def _described(marvin, tmp_path, *, slug=SLUG, **data):
    """A file and its Recording as media-describe leaves it: described, 5 s of sound, uploaded."""
    (tmp_path / "take.wav").write_bytes(b"RIFF....WAVE")
    marvin.add_asset(tmp_path / "take.wav", mime="audio/wav", name="Take one", asset_id=ID, slug="take-one", meta={"audio": {"duration_seconds": 5.0}})
    fields = {"asset": ID, "kind": "audio", "source": "uploaded", "status": "described", "duration_seconds": 5.0, "has_audio": True, **data}
    return marvin.add_entry("recording", slug, fields, title="Take one")


# ── the contract ─────────────────────────────────────────────────────────────
def test_the_kind_is_registered_under_the_media_prefix_and_takes_a_file_or_a_recording():
    assert jobs.TRANSCRIBE == "media-transcribe" and "media-transcribe" in load_kinds("mediajobs.jobs")
    contract = META["media-transcribe"]
    assert contract["inputs"]["asset"]["accepts"] == ["audio/*", "video/*"] and contract["inputs"]["recording"]["type"] == "string"
    assert {k for k, v in contract["inputs"].items() if not v["required"]} == {"asset", "recording", "language", "hotwords", "model", "force", "entry", "field", "ref_field"}
    assert contract["needs"] == ["faster-whisper"] and contract["produces"] == ["text/plain", "application/json"]
    assert all(kind.startswith("media-") for kind, fn in HANDLERS.items() if fn.__module__ == "mediajobs.jobs")


def test_a_picture_is_refused_before_anything_runs(marvin):
    marvin.add_asset(None, mime="image/png", asset_id=ID)
    with pytest.raises(Exception, match="takes audio/\\*, video/\\*"):
        check_args("media-transcribe", {"asset": ID}, ("http://marvin.test", "t"))


def test_it_needs_a_file_or_a_recording(marvin, tmp_path):
    with pytest.raises(Exception, match="needs args.asset"):
        run({}, tmp_path / "w")


# ── what is said ─────────────────────────────────────────────────────────────
def test_the_words_go_on_the_recording_and_the_files_on_its_transcript(marvin, heard, tmp_path):
    _described(marvin, tmp_path, notes="mine")
    out = run({"recording": SLUG, "language": "en", "hotwords": "Jared"}, tmp_path / "w")

    data = marvin.entry(SLUG)["dataJson"]
    assert data["status"] == "transcribed" and data["language"] == "en" and data["job"] == "transcribe-1" and data["notes"] == "mine"
    assert data["text"] == "Hello, I'm Jared. This is the take."
    assert data["words"][:3] == [{"text": "Hello,", "start": 0.52, "end": 0.9}, {"text": "I'm", "start": 1.0, "end": 1.2}, {"text": "Jared.", "start": 1.2, "end": 1.712}]
    assert len(data["words"]) == 7 and all(set(w) == {"text", "start", "end"} for w in data["words"])  # the `text` key, as asset metadata.words
    assert data["transcript"] == SLUG + "-transcript"

    (tr,) = marvin.live("transcript")
    t = tr["dataJson"]
    assert tr["slug"] == SLUG + "-transcript" and tr["title"] == "Transcript: Take one"
    assert t["source"] == ID and t["recording"] == SLUG and t["language"] == "en" and t["duration_seconds"] == 5.0 and t["job"] == "transcribe-1"
    assert set(t) == {"source", "recording", "language", "duration_seconds", "job", "paragraphs", "timestamped", "words", "json"}
    assert marvin.blobs[t["paragraphs"]].decode() == "Hello, I'm Jared.\n\nThis is the take.\n"  # a 1.6 s pause: a new paragraph
    assert marvin.blobs[t["timestamped"]].decode().splitlines() == ["[0:00:00] Hello, I'm Jared.", "[0:00:03] This is the take."]
    assert marvin.blobs[t["words"]].decode() == "Hello, I'm Jared.\nThis is the take.\n"
    assert json.loads(marvin.blobs[t["json"]])["words"] == data["words"]

    model_kw = heard.calls[0][1]
    assert model_kw["word_timestamps"] is True and model_kw["vad_filter"] is True and (model_kw["language"], model_kw["hotwords"]) == ("en", "Jared")
    assert len(heard.calls) == 1  # one pass gives both the text and the word times
    assert out["result"]["words"] == 7 and out["result"]["transcript"] == SLUG + "-transcript" and "7 words heard" in out["log"]


def test_every_file_says_it_is_a_transcript_of_the_recording_and_which_worker_made_it(marvin, heard, tmp_path):
    _described(marvin, tmp_path)
    asset = marvin.assets[ID]
    before = json.loads(json.dumps(asset["metadataJson"]))
    run({"recording": SLUG}, tmp_path / "w")
    files = marvin.uploads()
    assert sorted(f["mimeType"] for f in files) == ["application/json", "text/plain", "text/plain", "text/plain"]
    for f in files:
        meta = f["metadataJson"]
        assert meta["kind"] == "transcript" and meta["derived_from"] == ID and meta["source"] == ID and meta["recording"] == SLUG, meta
        assert meta["worker"] == {"kind": "media-transcribe", "job": "transcribe-1"} and f["origin"] == "derived"
    assert {f["name"] for f in files} >= {"Transcript in paragraphs: Take one", "Transcript (JSON): Take one"}
    after = dict(asset["metadataJson"])
    assert after.pop("description_by")["kind"] == "media-transcribe"  # the speech written to its description says so
    assert after == before  # the file's other metadata (its facts, its caption words) is kept as it was


@pytest.mark.parametrize("asked", ["take-one", ID])
def test_asked_by_the_files_slug_or_id_the_transcript_names_the_file_by_its_id(marvin, heard, tmp_path, asked):
    """An asset field keeps only an id: a slug written into the Transcript's `source` would come out empty (Studio's v229 fix)."""
    _described(marvin, tmp_path)
    out = run({"asset": asked}, tmp_path / "w")
    assert out["result"]["recording"] == SLUG and marvin.live("transcript")[0]["dataJson"]["source"] == ID
    assert marvin.downloads == [ID]


def test_more_words_than_the_recording_keeps_stay_in_the_transcripts_json_file(marvin, monkeypatch, heard, tmp_path):
    monkeypatch.setattr(transcribe, "WORDS_ON_RECORDING", 5)
    _described(marvin, tmp_path)
    out = run({"recording": SLUG}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["words"] == [] and data["text"] and data["status"] == "transcribed" and "JSON file only" in out["log"]
    assert len(json.loads(marvin.blobs[marvin.live("transcript")[0]["dataJson"]["json"]])["words"]) == 7


# ── nothing to hear ──────────────────────────────────────────────────────────
def test_no_speech_says_so_without_failing_and_makes_no_files(marvin, monkeypatch, tmp_path):
    _described(marvin, tmp_path)
    monkeypatch.setattr(transcribe, "has_sound", lambda path: True)
    monkeypatch.setattr(transcribe, "load_model", lambda name: FakeModel(said=[]))
    out = run({"recording": SLUG}, tmp_path / "w")
    data = marvin.entry(SLUG)["dataJson"]
    assert (data["status"], data["text"], data["words"]) == ("no-speech", "", []) and "transcript" not in data and "language" not in data
    assert not marvin.uploads() and not marvin.live("transcript") and "no speech heard" in out["log"] and out["result"]["words"] == 0


def test_a_file_with_no_sound_stream_is_never_handed_to_the_model(marvin, monkeypatch, tmp_path):
    _described(marvin, tmp_path)
    monkeypatch.setattr(transcribe, "has_sound", lambda path: False)
    monkeypatch.setattr(transcribe, "load_model", lambda name: Deaf())
    out = run({"recording": SLUG}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "no-speech" and "no sound stream" in out["log"] and not marvin.uploads()


def test_a_video_the_recording_says_has_no_sound_is_not_even_downloaded(marvin, monkeypatch, tmp_path):
    _described(marvin, tmp_path, kind="video", has_audio=False)
    monkeypatch.setattr(transcribe, "load_model", lambda name: Deaf())
    out = run({"recording": SLUG}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "no-speech" and "has no sound" in out["log"] and marvin.downloads == []


# ── what it leaves alone ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("data", "why"),
    [
        ({"duration_seconds": 1800.5}, "over the 1800 s"),
        ({"duration_seconds": 7200}, "over the 1800 s"),
        ({"duration_seconds": 0.4}, "under the 1 s"),
        ({"source": "generated"}, "a service made it"),
        ({"status": "transcribed"}, "heard already"),
        ({"status": "no-speech"}, "heard already"),
    ],
)
def test_too_long_too_short_made_or_heard_already_is_an_answer_not_a_failure_and_nothing_is_touched(marvin, monkeypatch, tmp_path, data, why):
    _described(marvin, tmp_path, **data)
    monkeypatch.setattr(transcribe, "load_model", lambda name: Deaf())
    before = json.loads(json.dumps(marvin.entry(SLUG)["dataJson"]))
    out = run({"recording": SLUG}, tmp_path / "w")
    assert out["result"]["skipped"] is True and why in out["log"]
    assert marvin.writes == [] and marvin.downloads == [] and marvin.entry(SLUG)["dataJson"] == before


def test_the_length_limit_holds_even_with_force(marvin, monkeypatch, tmp_path):
    _described(marvin, tmp_path, duration_seconds=1801)
    monkeypatch.setattr(transcribe, "load_model", lambda name: Deaf())
    assert run({"recording": SLUG, "force": True}, tmp_path / "w")["result"]["skipped"] is True and marvin.writes == []


def test_a_made_voice_is_transcribed_when_asked_with_force(marvin, heard, tmp_path):
    _described(marvin, tmp_path, source="generated")
    run({"recording": SLUG, "force": True}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "transcribed"


def test_transcribing_again_is_skipped_and_with_force_it_replaces_the_files_on_the_same_transcript(marvin, heard, tmp_path):
    _described(marvin, tmp_path)
    run({"recording": SLUG}, tmp_path / "w1")
    (tr,) = marvin.live("transcript")
    first = dict(tr["dataJson"])
    written = list(marvin.writes)

    again = run({"recording": SLUG}, tmp_path / "w2", job="transcribe-2")
    assert again["result"]["skipped"] is True and marvin.writes == written and len(heard.calls) == 1

    tr["dataJson"]["notes"] = "Jared speaking"
    run({"recording": SLUG, "force": True}, tmp_path / "w3", job="transcribe-3")
    (tr,) = marvin.live("transcript")
    now = tr["dataJson"]
    assert now["notes"] == "Jared speaking" and now["job"] == "transcribe-3" and len(heard.calls) == 2
    for key in ("paragraphs", "timestamped", "words", "json"):
        assert now[key] != first[key] and marvin.assets[first[key]]["trashedAt"] and not marvin.assets[now[key]]["trashedAt"], key
    assert marvin.entry(SLUG)["dataJson"]["transcript"] == tr["slug"]


def test_a_job_tried_again_after_its_last_write_failed_finds_the_transcript_it_made(marvin, heard, tmp_path):
    _described(marvin, tmp_path)
    marvin.fail_entry_patch = True  # the Transcript is made (a create); the Recording's write after it fails
    with pytest.raises(Exception, match="500"):
        run({"recording": SLUG}, tmp_path / "w1")
    marvin.fail_entry_patch = False
    assert marvin.entry(SLUG)["dataJson"]["status"] == "described" and len(marvin.live("transcript")) == 1
    run({"recording": SLUG}, tmp_path / "w2")
    (tr,) = marvin.live("transcript")
    assert marvin.entry(SLUG)["dataJson"]["transcript"] == tr["slug"] and marvin.entry(SLUG)["dataJson"]["status"] == "transcribed"
    assert sum(1 for f in marvin.uploads() if not f["trashedAt"]) == 4  # the first try's files went to the Trash


def test_a_job_tried_again_finds_the_files_its_first_try_uploaded_under_their_slugs(marvin, heard, tmp_path):
    """A file's slug is unique in the workspace: the same job run again (its last write failed) must not be refused for the slugs it took."""
    _described(marvin, tmp_path)
    marvin.strict_asset_slugs = True
    marvin.fail_entry_patch = True
    with pytest.raises(Exception, match="500"):
        run({"recording": SLUG}, tmp_path / "w1")
    marvin.fail_entry_patch = False
    first = sorted(f["slug"] for f in marvin.uploads())
    run({"recording": SLUG}, tmp_path / "w2")
    assert sorted(f["slug"] for f in marvin.uploads()) == first and len(first) == 4  # the same four files, none made twice
    (tr,) = marvin.live("transcript")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "transcribed" and tr["dataJson"]["json"] in {f["id"] for f in marvin.uploads()}


def test_another_files_slug_is_not_taken_over_and_the_next_free_one_is_used(marvin, heard, tmp_path):
    _described(marvin, tmp_path)
    marvin.strict_asset_slugs = True
    marvin.add_asset(None, mime="text/plain", name="Somebody's", slug="transcribe-1-text", meta={"kind": "notes"})
    run({"recording": SLUG}, tmp_path / "w1")
    slugs = {f["slug"] for f in marvin.uploads()}
    assert "transcribe-1-text-2" in slugs and marvin.assets[next(a for a in marvin.assets if marvin.assets[a]["slug"] == "transcribe-1-text")]["metadataJson"] == {"kind": "notes"}


# ── describe first ───────────────────────────────────────────────────────────
@needs_ffmpeg
def test_a_file_with_no_recording_is_given_one_described_and_transcribed(marvin, heard, tmp_path):
    path, mime = media_files.make("stereo-wav", tmp_path / "files")
    marvin.add_asset(path, mime=mime, asset_id=ID, name="Interview")
    out = run({"asset": ID}, tmp_path / "w")
    (rec,) = marvin.live()
    data = rec["dataJson"]
    assert rec["slug"] == SLUG == out["result"]["recording"] and data["status"] == "transcribed" and data["duration_seconds"] == pytest.approx(1.9, abs=0.02)
    assert data["level_db"] < 0 and data["waveform"]["length"] == 240 and data["job"] == "transcribe-1"


@needs_ffmpeg
def test_a_recording_still_new_is_described_first_and_a_second_job_the_describe_set_off_skips(marvin, heard, tmp_path):
    """With media-transcribe-on-describe on, the describe this job runs first queues a second transcribe job. The worker runs one job at a
    time, so that one starts after this one has finished: it finds the Recording heard already and does nothing."""
    path, mime = media_files.make("stereo-wav", tmp_path / "files")
    marvin.add_asset(path, mime=mime, asset_id=ID)
    marvin.add_entry("recording", SLUG, {"asset": ID, "kind": "audio", "status": "new"})
    run({"recording": SLUG}, tmp_path / "w1")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "transcribed" and len(heard.calls) == 1
    second = run({"recording": SLUG}, tmp_path / "w2", job="transcribe-2")
    assert second["result"]["skipped"] is True and len(heard.calls) == 1 and len(marvin.live("transcript")) == 1


@needs_ffmpeg
def test_a_file_that_cannot_be_described_fails_the_job_and_marks_the_recording(marvin, heard, tmp_path):
    (tmp_path / "broken.mp3").write_bytes(b"not a sound at all" * 50)
    marvin.add_asset(tmp_path / "broken.mp3", mime="audio/mpeg", asset_id=ID)
    with pytest.raises(Exception, match="could not read"):
        run({"asset": ID}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "failed" and not heard.calls


@needs_ffmpeg
def test_describing_after_a_transcription_keeps_it(marvin, heard, tmp_path):
    path, mime = media_files.make("stereo-wav", tmp_path / "files")
    marvin.add_asset(path, mime=mime, asset_id=ID)
    run({"asset": ID}, tmp_path / "w1")
    describe({"asset": ID, "force": True}, tmp_path / "w2", job="describe-2")
    data = marvin.entry(SLUG)["dataJson"]
    assert data["status"] == "transcribed" and data["text"] and data["transcript"]


# ── what it writes fits the package ──────────────────────────────────────────
def test_every_field_written_is_a_field_of_its_type(marvin, heard, tmp_path):
    _described(marvin, tmp_path)
    run({"recording": SLUG}, tmp_path / "w")
    for slug, row in ((SLUG, marvin.entry(SLUG)), (None, marvin.live("transcript")[0])):
        fields = FIELDS["recording" if slug else "transcript"]
        for key, value in row["dataJson"].items():
            field = fields[key]
            if field["type"] == "select":
                assert value in field["options"], (key, value)
            if field["type"] == "asset":
                assert value in marvin.assets, key  # an id, never a slug
            if field.get("ref"):
                assert any(e["slug"] == value for e in marvin.entries), key


# ── the text forms (Studio's, copied) ────────────────────────────────────────
SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Thieving, lying, swearing."},
    {"start": 2.2, "end": 4.0, "text": "Joined to the most abominable lewdness."},
    {"start": 6.5, "end": 8.0, "text": "They were cowards."},
]


def test_the_three_forms():
    assert text.plain(SEGMENTS).splitlines()[0] == "Thieving, lying, swearing."
    assert text.timestamped(SEGMENTS).splitlines()[2] == "[0:00:06] They were cowards."
    assert text.paragraphs(SEGMENTS) == "Thieving, lying, swearing. Joined to the most abominable lewdness.\n\nThey were cowards.\n"
    assert text.stamp(3725) == "1:02:05"


def test_the_transcript_is_written_as_the_model_speaks_so_a_failure_keeps_what_was_heard(tmp_path):
    class Dies(FakeModel):
        def transcribe(self, path, **kw):
            segments, info = super().transcribe(path, **kw)

            def run_out():
                yield next(segments)
                raise RuntimeError("decoder died")

            return run_out(), info

    with pytest.raises(RuntimeError):
        transcribe.transcribe_file(Dies(), tmp_path / "a.wav", tmp_path / "job-1")
    assert (tmp_path / "job-1.txt").read_text() == "Hello, I'm Jared.\n"


def test_empty_hotwords_and_language_reach_the_model_as_none(tmp_path):
    model = FakeModel()
    transcribe.transcribe_file(model, tmp_path / "a.wav", tmp_path / "j1", language="", hotwords="")
    assert (model.calls[0][1]["language"], model.calls[0][1]["hotwords"]) == (None, None)


# ── the image ────────────────────────────────────────────────────────────────
def test_the_image_has_the_transcriber_and_the_container_offers_it_with_the_model_on_a_volume():
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert 'faster-whisper==1.2.1 "av>=14,<17"' in dockerfile and "HF_HOME=/models" in dockerfile and "WHISPER_MODEL=small" in dockerfile
    compose = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
    assert "WORKER_KINDS: media-describe,media-transcribe" in compose and "transcribe-models:/models" in compose
    assert "container_name: ${MEDIA_WORKSPACE}-media" in compose  # the same one container as m1: lowburnstudio-media


# ── the real model ───────────────────────────────────────────────────────────
def _cached(model: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return isinstance(try_to_load_from_cache(f"Systran/faster-whisper-{model}", "model.bin"), str)


@pytest.mark.slow
def test_the_real_model_hears_no_speech_in_a_tone_video(marvin, tmp_path):
    """faster-whisper itself, on a generated video of a red picture and a 440 Hz tone: av finds the sound stream, the voice filter finds no
    speech, and the Recording says no-speech. Skipped where faster-whisper is not installed (CI) or its model is not cached (never downloaded)."""
    pytest.importorskip("faster_whisper")
    pytest.importorskip("av")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    model = transcribe.MODEL
    if not _cached(model):
        pytest.skip(f"the {model} model is not in this machine's cache (HF_HOME): the test never downloads it")
    video = tmp_path / "tone.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=30:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)  # fmt: skip
    assert transcribe.has_sound(video)
    marvin.add_asset(video, mime="video/mp4", asset_id=ID, name="Tone")
    marvin.add_entry("recording", SLUG, {"asset": ID, "kind": "video", "source": "uploaded", "status": "described", "duration_seconds": 3.0, "has_audio": True})
    out = run({"recording": SLUG}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "no-speech" and "no speech heard" in out["log"] and not marvin.uploads()
