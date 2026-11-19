"""What is said in a file goes into the file's own description, so Marvin's AI index finds it: a header naming the file, then the
transcript with times (`[h:mm:ss] text`, as text.stamp writes it). Written by media-transcribe after speech is heard, and by media-backfill
with `searchable` for the Recordings heard before. Never over a description a person wrote; the worker's own is refreshed by a forced
transcription; the file's other metadata is kept; a long transcript is cut at a line; no speech leaves the description alone."""

import json

import pytest
from marvin_worker import marvin_api
from marvin_worker.jobs import Context

from mediajobs import jobs, searchable, text, transcribe
from tests.conftest import transcribe as run
from tests.test_transcribe import ID, SAID, SLUG, FakeModel, _described

HEADER = "Speech in Take one, as text with where each part starts:"
STAMPED = ["[0:00:00] Hello, I'm Jared.", "[0:00:03] This is the take."]


@pytest.fixture
def model(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(transcribe, "load_model", lambda name: model)
    monkeypatch.setattr(transcribe, "has_sound", lambda path: True)
    return model


def backfill(args: dict, work, job: str = "backfill-1") -> dict:
    work.mkdir(parents=True, exist_ok=True)
    with marvin_api.working(jobs.BACKFILL, job):
        return jobs.media_backfill({"job": job, "kind": jobs.BACKFILL, "args": args}, Context(creds=("http://marvin.test", "t"), work=work, name=job))


# ── media-transcribe ─────────────────────────────────────────────────────────
def test_speech_heard_goes_into_the_files_description_with_a_header_and_its_times(marvin, model, tmp_path, capsys):
    _described(marvin, tmp_path)
    marvin.assets[ID]["metadataJson"].update(words=[{"text": "caption"}], recording={"source": "browser"})
    out = run({"recording": SLUG}, tmp_path / "w")

    asset = marvin.assets[ID]
    assert asset["description"].splitlines() == [HEADER, *STAMPED]
    assert asset["description"].splitlines()[1:] == text.timestamped([{"start": s, "text": t} for t, s, _e, _w in SAID]).splitlines()
    meta = asset["metadataJson"]
    assert meta["description_by"] == {"kind": "media-transcribe", "job": "transcribe-1", "hash": searchable.digest(asset["description"])}
    assert meta["audio"] == {"duration_seconds": 5.0} and meta["words"] == [{"text": "caption"}] and meta["recording"] == {"source": "browser"}
    assert out["result"]["description"] == "written" and "description" in out["log"]
    assert "the speech written to Take one's description" in capsys.readouterr().out  # the job's log says it


def test_a_description_a_person_wrote_is_never_replaced(marvin, model, tmp_path, capsys):
    _described(marvin, tmp_path)
    marvin.assets[ID]["description"] = "Jared's first take, in the kitchen."
    before = json.loads(json.dumps(marvin.assets[ID]))
    out = run({"recording": SLUG}, tmp_path / "w")
    assert marvin.assets[ID] == before and ("PATCH", f"/api/platform/assets/{ID}") not in marvin.writes
    assert out["result"]["description"] == "kept" and "a person wrote: left as it is" in capsys.readouterr().out
    assert marvin.entry(SLUG)["dataJson"]["status"] == "transcribed"  # the transcription itself is done


def test_a_forced_transcription_refreshes_the_workers_own_description(marvin, model, tmp_path):
    _described(marvin, tmp_path)
    run({"recording": SLUG}, tmp_path / "w1")
    model.said = [("Take two now.", 2.0, 3.0, [(" Take", 2.0, 2.3), (" two", 2.3, 2.6), (" now.", 2.6, 3.0)])]
    out = run({"recording": SLUG, "force": True}, tmp_path / "w2", job="transcribe-2")
    asset = marvin.assets[ID]
    assert asset["description"].splitlines() == [HEADER, "[0:00:02] Take two now."] and out["result"]["description"] == "refreshed"
    assert asset["metadataJson"]["description_by"]["job"] == "transcribe-2" and asset["metadataJson"]["audio"] == {"duration_seconds": 5.0}

    patches = marvin.writes.count(("PATCH", f"/api/platform/assets/{ID}"))
    again = run({"recording": SLUG, "force": True}, tmp_path / "w3", job="transcribe-3")
    assert again["result"]["description"] == "unchanged" and marvin.writes.count(("PATCH", f"/api/platform/assets/{ID}")) == patches


def test_a_person_editing_the_workers_description_makes_it_theirs(marvin, model, tmp_path):
    _described(marvin, tmp_path)
    run({"recording": SLUG}, tmp_path / "w1")
    marvin.assets[ID]["description"] += "\nJared fluffs the second line."  # description_by still names the worker, but the text is not its own now
    edited = marvin.assets[ID]["description"]
    out = run({"recording": SLUG, "force": True}, tmp_path / "w2", job="transcribe-2")
    assert marvin.assets[ID]["description"] == edited and out["result"]["description"] == "kept"


def test_a_long_transcript_is_cut_between_two_lines_and_says_where_it_goes_on(marvin, model, tmp_path, monkeypatch):
    model.said = [(f"Line number {n} of the long take.", n * 10.0, n * 10.0 + 5, []) for n in range(40)]
    monkeypatch.setattr(searchable, "MAX_CHARS", 400)
    _described(marvin, tmp_path)
    run({"recording": SLUG}, tmp_path / "w")
    lines = marvin.assets[ID]["description"].splitlines()
    assert len(marvin.assets[ID]["description"]) <= 400 and lines[0] == HEADER
    kept = lines[1:-1]
    assert kept and kept == [f"[{text.stamp(n * 10.0)}] Line number {n} of the long take." for n in range(len(kept))]  # whole lines only
    assert lines[-1] == f"(Cut here: the speech goes on from {text.stamp(len(kept) * 10.0)}; the whole of it is on the Transcript {SLUG}-transcript.)"
    assert not lines[-1].startswith("[")  # not read as a segment


def test_no_speech_leaves_the_description_alone(marvin, monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "load_model", lambda name: FakeModel(said=[]))
    monkeypatch.setattr(transcribe, "has_sound", lambda path: True)
    _described(marvin, tmp_path)
    before = json.loads(json.dumps(marvin.assets[ID]))
    run({"recording": SLUG}, tmp_path / "w")
    assert marvin.entry(SLUG)["dataJson"]["status"] == "no-speech" and marvin.assets[ID] == before


@pytest.mark.parametrize("fail", [True, "unreachable"])
def test_a_description_that_cannot_be_written_is_said_and_the_transcription_stands(marvin, model, tmp_path, fail):
    _described(marvin, tmp_path)
    marvin.fail_asset_patch = fail
    out = run({"recording": SLUG}, tmp_path / "w")
    assert out["result"]["description"] == "failed" and "could not be written" in out["log"] and len(model.calls) == 1
    assert marvin.entry(SLUG)["dataJson"]["status"] == "transcribed" and not marvin.assets[ID]["description"]


# ── the pure part ────────────────────────────────────────────────────────────
def test_lines_are_rebuilt_from_words_at_sentence_ends_and_pauses_in_the_same_stamp():
    words = [{"text": "Hello,", "start": 0.5, "end": 0.9}, {"text": "Jared.", "start": 1.0, "end": 1.7}, {"text": "So", "start": 65.2, "end": 65.4},
             {"text": "then", "start": 65.5, "end": 65.8}, {"word": "later", "start": 70.0, "end": 70.3}]  # fmt: skip
    assert searchable.lines_from_words(words).splitlines() == ["[0:00:00] Hello, Jared.", "[0:01:05] So then", "[0:01:10] later"]
    assert searchable.lines_from_words([{"text": "x", "start": None}]) is None and searchable.lines_from_words([]) is None


def test_the_header_is_one_line_whatever_the_name():
    assert searchable.header("Take\none  (kitchen)") == "Speech in Take one (kitchen), as text with where each part starts:"


def test_a_first_line_too_long_for_the_limit_leaves_the_header_and_where_it_goes_on():
    out, cut = searchable.compose("A", "[0:00:00] " + "word " * 100 + "\n[0:00:09] more\n", limit=200)
    assert cut and out.splitlines() == [searchable.header("A"), "(Cut here: the speech goes on from 0:00:00; the whole of it is in its Transcript's files.)"]


# ── media-backfill with `searchable` ─────────────────────────────────────────
def _heard_before(marvin, tmp_path, *, words: bool = True, description: str = "", by: "dict | None" = None):
    """A Recording heard before this change: transcribed, its words on it (or not: then only its Transcript's timestamped file has them)."""
    rec = _described(marvin, tmp_path)
    file = marvin.add_asset(None, mime="text/plain", name="Transcript with times: Take one", meta={"kind": "transcript"}, slug="t-timestamped")
    marvin.blobs[file["id"]] = "\n".join(STAMPED).encode() + b"\n"
    (tmp_path / "ts.txt").write_bytes(marvin.blobs[file["id"]])
    marvin.files[file["id"]] = tmp_path / "ts.txt"
    marvin.add_entry("transcript", f"{SLUG}-transcript", {"source": ID, "recording": SLUG, "timestamped": file["id"]})
    heard = [{"text": w.strip(), "start": s, "end": e} for _t, _s, _e, ws in SAID for w, s, e in ws] if words else []
    rec["dataJson"].update(status="transcribed", text="Hello, I'm Jared. This is the take.", words=heard, transcript=f"{SLUG}-transcript")
    marvin.assets[ID]["description"] = description or None
    if by:
        marvin.assets[ID]["metadataJson"]["description_by"] = by
    return rec


def test_the_speech_backfill_is_a_dry_run_by_default_and_writes_nothing(marvin, tmp_path):
    _heard_before(marvin, tmp_path)
    marvin.add_entry("recording", "rec-other", {"asset": "gone", "kind": "audio", "status": "described"})
    marvin.add_entry("recording", "rec-quiet", {"asset": "gone", "kind": "audio", "status": "no-speech"})
    out = backfill({"searchable": "yes"}, tmp_path / "w")["result"]
    assert out["dry_run"] is True and marvin.writes == [] and marvin.downloads == []
    assert out["counts"] == {"would-write": 1, "not-transcribed": 1, "no-speech": 1}
    assert out["outcomes"]["would-write"]["recordings"] == [{"recording": SLUG, "asset": ID, "name": "Take one", "from": "words"}]
    assert out["summary"].startswith("Dry run: 1 would-write")


def test_the_speech_backfill_writes_from_the_recordings_words_and_a_second_run_writes_nothing(marvin, tmp_path):
    _heard_before(marvin, tmp_path)
    out = backfill({"searchable": "yes", "dry_run": "no"}, tmp_path / "w")["result"]
    asset = marvin.assets[ID]
    assert out["counts"] == {"written": 1} and marvin.downloads == []  # no model, no download: the words carry their times
    assert asset["description"].splitlines() == [HEADER, "[0:00:00] Hello, I'm Jared.", "[0:00:03] This is the take."]
    assert asset["metadataJson"]["description_by"]["kind"] == "media-backfill" and asset["metadataJson"]["audio"] == {"duration_seconds": 5.0}

    written = list(marvin.writes)
    again = backfill({"searchable": "yes", "dry_run": "no"}, tmp_path / "w2", job="backfill-2")["result"]
    assert again["counts"] == {"written-already": 1} and marvin.writes == written


def test_the_speech_backfill_reads_the_transcripts_timestamped_file_when_the_recording_keeps_no_words(marvin, tmp_path):
    _heard_before(marvin, tmp_path, words=False)
    out = backfill({"searchable": "yes", "dry_run": "no"}, tmp_path / "w")["result"]
    assert out["counts"] == {"written": 1} and len(marvin.downloads) == 1
    assert marvin.assets[ID]["description"].splitlines() == [HEADER, *STAMPED]


@pytest.mark.parametrize("by", [None, {"kind": "media-transcribe", "job": "t", "hash": "not-this-text"}])
def test_the_speech_backfill_keeps_a_persons_description(marvin, tmp_path, by):
    _heard_before(marvin, tmp_path, description="Kitchen take.", by=by)
    out = backfill({"searchable": "yes", "dry_run": "no"}, tmp_path / "w")["result"]
    assert out["counts"] == {"kept": 1} and marvin.writes == [] and marvin.assets[ID]["description"] == "Kitchen take."


def test_the_speech_backfill_writes_at_most_limit_and_the_next_run_goes_on(marvin, tmp_path):
    for n in range(3):
        asset_id = f"{n:08x}-0000-0000-0000-000000000000"
        marvin.add_asset(None, mime="audio/wav", name=f"Take {n}", asset_id=asset_id)
        marvin.add_entry("recording", f"rec-{n}", {"asset": asset_id, "kind": "audio", "status": "transcribed", "words": [{"text": "Hi.", "start": 1.0, "end": 1.5}]})
    first = backfill({"searchable": "yes", "dry_run": "no", "limit": 2}, tmp_path / "w")["result"]
    assert first["counts"] == {"written": 2} and first["remaining"] is True and "run it again" in first["summary"]
    second = backfill({"searchable": "yes", "dry_run": "no", "limit": 2}, tmp_path / "w2")["result"]
    assert second["counts"] == {"written-already": 2, "written": 1} and second["remaining"] is False
    assert all(a["description"].endswith("\n[0:00:01] Hi.") for a in marvin.assets.values())
