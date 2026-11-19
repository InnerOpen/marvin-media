"""media-backfill and media-link, against the stand-in Marvin with a library like the live one: a person's own recordings and uploads among
files a worker made, versions, ElevenLabs takes, voice previews with no metadata at all, files in the Trash and files that have a Recording.

A dry run reads and writes nothing (no write, no download); a real run makes at most `limit` Recordings, oldest first, says where the next
run starts, and a second run over the same library writes nothing at all. media-link joins a Recording to the Transcript Studio made earlier
from its file. Describing is media-describe's own (stood in for here, except in the one test that runs ffmpeg on real files)."""

import json
from types import SimpleNamespace as NS

import pytest
from marvin_worker import marvin_api
from marvin_worker.jobs import META, Context, check_args, load_kinds

from mediajobs import jobs, library, recording, transcribe
from tests import media_files
from tests.conftest import needs_ffmpeg
from tests.fake_marvin import FakeMarvin


def _id(n: int) -> str:
    return f"{n:08x}-1362-43f3-8bfa-c11642c974d2"


def backfill(args: dict, work, job: str = "backfill-1") -> dict:
    """Run media-backfill as the worker would: its own scratch folder, the worker's stamp on what it uploads."""
    work.mkdir(parents=True, exist_ok=True)
    with marvin_api.working(jobs.BACKFILL, job):
        return jobs.media_backfill({"job": job, "kind": jobs.BACKFILL, "args": args}, Context(creds=("http://marvin.test", "t"), work=work, name=job))


def link(args: dict, work, job: str = "link-1") -> dict:
    work.mkdir(parents=True, exist_ok=True)
    return jobs.media_link({"job": job, "kind": jobs.LINK, "args": args}, Context(creds=("http://marvin.test", "t"), work=work, name=job))


# What each file in the library is, and what a backfill should say of it.
WANTED = ["take", "upload", "phone", "bed"]  # oldest first: the order they were added
LEFT_OUT = {
    "stamped": "worker-file", "render": "worker-file", "cut": "worker-file", "converted": "derived", "copy": "derived", "elevenlabs": "generated",
    "preview": "voice-preview", "preview-by-voice": "voice-preview", "preview-new": "voice-preview", "recorded": "has-recording",
    "recording-binned": "recording-in-trash", "binned": "trashed",
}  # fmt: skip


@pytest.fixture
def lib(monkeypatch, tmp_path):
    """The stand-in Marvin with the library, and `ids` naming each file."""
    fake = FakeMarvin(types={"job": "type-job", "recording": "type-recording", "transcript": "type-transcript", "voice": "type-voice"}).wire(monkeypatch)
    folder = tmp_path / "files"
    folder.mkdir()

    def add(key: str, n: int, mime: str, **kw) -> None:
        path = folder / f"{key}.bin"
        path.write_bytes(b"x" * kw.pop("size", 4000))
        fake.add_asset(path, mime=mime, asset_id=_id(n), name=kw.pop("name", key), **kw)

    add("take", 1, "audio/webm;codecs=opus", name="Take one", meta={"recording": {"source": "browser", "mimeType": "audio/webm"}})
    add("stamped", 2, "audio/mpeg", meta={"worker": {"kind": "import-voices", "job": "job-9"}})
    add("upload", 3, "audio/mpeg", name="Narration take 3.mp3")
    add("render", 4, "video/mp4", meta={"kind": "video", "story": "the-attic"})
    add("cut", 5, "audio/mpeg", meta={"kind": "audio-cut"})
    add("converted", 6, "audio/mpeg", meta={"kind": "converted-audio", "derived_from": _id(1)}, origin="derived", source=_id(1))
    add("copy", 7, "audio/wav", meta={"derived_from": _id(3)})  # a version that says so only in its metadata
    add("elevenlabs", 8, "audio/mpeg", name="Line 12 (Rachel)", origin="generated", meta={"words": [{"text": "Hi", "start": 0, "end": 0.3}]})
    add("preview", 9, "audio/mpeg", name="Rachel (voice preview)", description="ElevenLabs' preview clip of the voice Rachel.")  # no metadata at all
    add("preview-by-voice", 10, "audio/mpeg", name="clip")  # named by nothing but a voice entry's preview field
    add("preview-new", 11, "audio/mpeg", name="Adam (voice preview)", meta={"kind": "voice-preview", "voice": "adam"})
    add("phone", 12, "video/mp4", name="Phone video")
    add("recorded", 13, "audio/wav", name="Already")
    add("recording-binned", 14, "audio/wav", name="Binned recording")
    add("binned", 15, "audio/wav", name="In the Trash", trashed=True)
    add("bed", 16, "audio/mpeg", name="Rain bed")
    add("picture", 17, "image/png", name="A picture")  # not a recording: never listed
    fake.add_entry("voice", "rachel", {"elevenlabs_voice_id": "v1", "preview": _id(10)})
    fake.add_entry("recording", "rec-0000000d-136", {"asset": _id(13), "kind": "audio", "status": "described", "duration_seconds": 3.0})
    fake.add_entry("recording", "rec-0000000e-136", {"asset": _id(14), "kind": "audio", "status": "new"}, status="trashed")
    ids = dict(zip(["take", "stamped", "upload", "render", "cut", "converted", "copy", "elevenlabs", "preview", "preview-by-voice", "preview-new", "phone", "recorded", "recording-binned", "binned", "bed"], [_id(n) for n in range(1, 17)]))  # fmt: skip
    return NS(fake=fake, ids=ids)


@pytest.fixture
def described(monkeypatch, lib):
    """media-describe stood in for: it writes what the real one writes last (the facts and `described`) in its own PATCH, and says each
    Recording it was asked to describe."""
    calls: list[str] = []

    def fake_describe(job: dict, ctx: Context) -> dict:
        slug = job["args"]["recording"]
        calls.append(slug)
        data = lib.fake.entry(slug)["dataJson"]
        if data.get("status") == "described":
            return {"result": {"recording": slug, "asset": data["asset"], "skipped": True}}
        jobs.write_entry(ctx, slug, {"status": "described", "duration_seconds": 5.0, "has_audio": True, "source": "uploaded", "job": job["job"]})
        return {"result": {"recording": slug, "asset": data["asset"], "created": False, "duration_seconds": 5.0}}

    monkeypatch.setattr(jobs, "media_describe", fake_describe)
    return calls


def _rec(asset_id: str) -> str:
    return recording.slug_for(asset_id)


# ── the contract ─────────────────────────────────────────────────────────────
def test_both_kinds_are_registered_with_the_media_prefix_and_their_contracts():
    assert {"media-backfill", "media-link"} <= set(load_kinds("mediajobs.jobs"))
    assert set(META["media-backfill"]["inputs"]) == {"limit", "kinds", "after", "min_bytes"}  # the yes/no ones are read as text or booleans
    assert META["media-backfill"]["inputs"]["kinds"]["enum"] == ["audio", "video", "both"]
    assert set(META["media-link"]["inputs"]) == {"asset", "recording", "entry", "field", "ref_field"}  # mirror and transcribe: yes/no, read as text or booleans
    check_args("media-backfill", {"limit": 50, "kinds": "both", "dry_run": True, "describe": "yes", "transcribe": False})
    with pytest.raises(Exception, match="kinds"):
        check_args("media-backfill", {"kinds": "pictures"})


# ── the rules ────────────────────────────────────────────────────────────────
def test_each_file_gets_one_reason_in_a_fixed_order():
    """A file that fits several rules is said under the first: a generated file with a Recording made by hand is `has-recording`; a
    worker's version of another file is `derived`."""
    reason = library.reason
    asset = {"id": "a", "mimeType": "audio/mpeg", "origin": "generated", "metadataJson": {"kind": "audio-cut", "derived_from": "b"}}
    assert reason(asset, recorded={"a": "live"})[0] == "has-recording"
    assert reason(asset, recorded={"a": "trashed"})[0] == "recording-in-trash"
    assert reason(asset, recorded={})[0] == "derived"
    assert reason({**asset, "metadataJson": {"kind": "audio-cut"}}, recorded={})[0] == "generated"
    assert reason({**asset, "trashedAt": "2026-11-01"}, recorded={"a": "live"})[0] == "trashed"
    preview = {"id": "p", "mimeType": "audio/mpeg", "name": "Rachel (voice preview)", "metadataJson": {"worker": {"kind": "import-voices"}}}
    assert reason(preview, recorded={})[0] == "voice-preview"  # a preview, before it is a worker's file
    assert reason(preview, recorded={}, include_worker_files=True)[0] == "voice-preview"  # and never taken in by include_worker_files
    assert reason({"id": "s", "mimeType": "audio/mpeg", "fileSize": 10}, recorded={}, min_bytes=100) == ("too-small", "10 bytes")
    assert reason({"id": "s", "mimeType": "application/octet-stream"}, recorded={})[0] == "not-a-recording"
    assert reason({"id": "s", "mimeType": "audio/mpeg"}, recorded={}) is None


def test_a_voice_preview_is_known_by_any_of_studios_marks():
    marks = [
        {"metadataJson": {"kind": "voice-preview"}},
        {"name": "Rachel (Voice Preview)"},
        {"description": "ElevenLabs' preview clip of the voice Rachel."},
    ]
    for mark in marks:
        assert library.is_voice_preview({"id": "x", **mark}), mark
    assert library.is_voice_preview({"id": "x"}, {"x"})  # a voice entry names it
    assert not library.is_voice_preview({"id": "x", "name": "Preview of my voice.mp3", "description": "my own"})


def test_yes_no_and_count_arguments_are_read_as_a_run_form_or_a_job_sends_them():
    assert [library.flag(v, False) for v in (True, "true", "yes", "Yes", "1", "on")] == [True] * 6
    assert [library.flag(v, True) for v in (False, "false", "no", "0", "off")] == [False] * 5
    assert library.flag("maybe", True) is True and library.flag(None, True) is True and library.flag("", False) is False  # unclear: the default
    assert library.whole("20", 50, low=1, high=500) == 20 and library.whole(None, 50, low=1, high=500) == 50 and library.whole(9999, 50, low=1, high=500) == 500
    assert library.whole("lots", 50, low=1, high=500) == 50 and library.whole(0, 50, low=1, high=500) == 1
    assert library.kinds_of(None) == ("audio", "video") == library.kinds_of("audio,video") and library.kinds_of("video") == ("video",)
    with pytest.raises(ValueError):
        library.kinds_of("pictures")


# ── the dry run ──────────────────────────────────────────────────────────────
def test_a_dry_run_writes_nothing_and_says_what_it_would_do_and_why_each_file_is_left_out(lib, described, tmp_path):
    out = backfill({}, tmp_path / "w")["result"]
    assert lib.fake.writes == [] and lib.fake.downloads == [] and described == []  # not one write, not one download
    assert out["dry_run"] is True and out["library"] == 15 and out["examined"] == 15  # the 15 audio and video files outside the Trash
    assert [c["asset"] for c in out["candidates"]] == [lib.ids[k] for k in WANTED]
    assert out["candidates"][0] == {"asset": lib.ids["take"], "recording": "rec-00000001-136", "name": "Take one", "bytes": 4000, "type": "audio/webm;codecs=opus", "kind": "audio", "source": "recorded"}
    assert out["candidates"][2]["kind"] == "video" and out["candidates"][1]["source"] == "uploaded"
    expected = {"candidates": 4}
    for why in LEFT_OUT.values():
        expected[why] = expected.get(why, 0) + 1
    assert out["counts"] == expected
    named = {f["asset"]: (why, f["why"]) for why, group in out["skipped"].items() for f in group["files"]}
    for key, why in LEFT_OUT.items():
        assert named[lib.ids[key]][0] == why, key
    assert named[lib.ids["stamped"]][1] == "metadata.worker.kind=import-voices" and named[lib.ids["render"]][1] == "metadata.kind=video"
    assert named[lib.ids["recorded"]][1] == "rec-0000000d-136" and out["skipped"]["generated"]["meaning"].startswith("a service made it")
    assert out["next"] is None and out["more"] == 0
    assert "4 of 15 files would get a Recording" in out["summary"] and "Nothing was written" in out["summary"]
    assert "transcribe each new recording" in out["summary"]  # the warning about the workflow that would transcribe what this describes


def test_a_dry_run_is_the_default_and_anything_unclear_stays_a_dry_run(lib, described, tmp_path):
    for args in ({}, {"dry_run": "maybe"}, {"dry_run": None}, {"dry_run": True}, {"dry_run": "yes"}):
        assert backfill(args, tmp_path / "w")["result"]["dry_run"] is True, args
    assert lib.fake.writes == []


def test_a_dry_run_lists_the_first_limit_files_and_says_where_a_run_of_that_size_would_stop(lib, described, tmp_path):
    out = backfill({"limit": 2}, tmp_path / "w")["result"]
    assert [c["asset"] for c in out["candidates"]] == [lib.ids["take"], lib.ids["upload"]] and out["more"] == 2
    assert out["next"] == lib.ids["upload"] and "the first 2 are listed" in out["summary"]
    after = backfill({"limit": 2, "after": out["next"]}, tmp_path / "w")["result"]
    assert [c["asset"] for c in after["candidates"]] == [lib.ids["phone"], lib.ids["bed"]] and after["next"] is None


def test_only_the_kinds_asked_for_are_listed(lib, described, tmp_path):
    out = backfill({"kinds": "video"}, tmp_path / "w")["result"]
    assert [c["asset"] for c in out["candidates"]] == [lib.ids["phone"]] and out["counts"] == {"candidates": 1, "worker-file": 1}
    assert all(q["asset_type"] == ["video"] for q in lib.fake.lists)


def test_worker_files_can_be_taken_in_but_never_previews_generated_takes_or_versions(lib, described, tmp_path):
    out = backfill({"include_worker_files": True}, tmp_path / "w")["result"]
    assert {c["asset"] for c in out["candidates"]} == {lib.ids[k] for k in [*WANTED, "stamped", "render", "cut"]}
    assert {"voice-preview", "generated", "derived"} <= set(out["counts"])


def test_small_files_can_be_left_out(lib, described, tmp_path):
    lib.fake.assets[lib.ids["bed"]]["fileSize"] = 120
    out = backfill({"min_bytes": 1000}, tmp_path / "w")["result"]
    assert lib.ids["bed"] not in {c["asset"] for c in out["candidates"]} and out["counts"]["too-small"] == 1


def test_the_library_and_the_recordings_are_read_a_page_at_a_time(lib, described, monkeypatch, tmp_path):
    """Marvin's list takes limit and offset (per_page is ignored) and says nothing of how many there are in the body: a short page is the last."""
    monkeypatch.setattr(jobs, "LIST_PAGE", 4)
    monkeypatch.setattr(jobs, "QUERY_PAGE", 1)
    out = backfill({}, tmp_path / "w")["result"]
    assert out["library"] == 15 and [c["asset"] for c in out["candidates"]] == [lib.ids[k] for k in WANTED]
    live = [q for q in lib.fake.lists if "trashed" not in q]
    assert [(q["limit"], q["offset"]) for q in live] == [("4", "0"), ("4", "4"), ("4", "8"), ("4", "12")]
    assert out["counts"]["has-recording"] == 1 and out["counts"]["recording-in-trash"] == 1  # the Recordings, one per page, all found


# ── the real run ─────────────────────────────────────────────────────────────
def test_a_run_gives_at_most_limit_files_a_recording_oldest_first_and_says_where_to_go_on(lib, described, tmp_path):
    out = backfill({"dry_run": False, "limit": 2}, tmp_path / "w")["result"]
    assert [o["asset"] for o in out["done"]] == [lib.ids["take"], lib.ids["upload"]]
    assert [o["recording"] for o in out["done"]] == ["rec-00000001-136", "rec-00000003-136"] and out["made"] == 2 and out["failed"] == 0
    assert described == ["rec-00000001-136", "rec-00000003-136"]
    for o in out["done"]:
        rec = lib.fake.entry(o["recording"])
        assert rec["dataJson"]["asset"] == o["asset"] and rec["dataJson"]["status"] == "described" and rec["title"] == lib.fake.assets[o["asset"]]["name"]
    assert out["next"] == lib.ids["upload"] and out["remaining"] == 2 and out["finished"] is False
    assert "run again with after=" + lib.ids["upload"] in out["summary"]
    rest = backfill({"dry_run": False, "limit": 2, "after": out["next"]}, tmp_path / "w", job="backfill-2")["result"]
    assert [o["asset"] for o in rest["done"]] == [lib.ids["phone"], lib.ids["bed"]] and rest["next"] is None and rest["finished"] is True
    assert {e["dataJson"]["asset"] for e in lib.fake.live()} == {lib.ids[k] for k in [*WANTED, "recorded"]}  # one Recording per file, no more


def test_a_run_without_the_cursor_carries_on_where_the_last_one_stopped(lib, described, tmp_path):
    backfill({"dry_run": False, "limit": 1}, tmp_path / "w")
    out = backfill({"dry_run": False, "limit": 1}, tmp_path / "w", job="backfill-2")["result"]
    assert [o["asset"] for o in out["done"]] == [lib.ids["upload"]] and out["counts"]["has-recording"] == 2  # the first is done: passed over


def test_a_cursor_whose_file_is_gone_starts_from_the_top_safely(lib, described, tmp_path):
    out = backfill({"dry_run": False, "after": "gone-0000"}, tmp_path / "w")["result"]
    assert [o["asset"] for o in out["done"]] == [lib.ids[k] for k in WANTED]


def test_two_runs_over_the_same_library_make_each_recording_once_and_the_second_writes_nothing(lib, described, tmp_path):
    first = backfill({"dry_run": False}, tmp_path / "w")["result"]
    assert first["made"] == 4 and first["finished"] is True and "All done" in first["summary"]
    lib.fake.writes.clear()
    second = backfill({"dry_run": False}, tmp_path / "w", job="backfill-2")["result"]
    assert second["done"] == [] and second["made"] == 0 and second["counts"]["has-recording"] == 5
    assert lib.fake.writes == []  # not one write: nothing made, described or joined twice


def test_making_without_describing_leaves_new_recordings(lib, described, tmp_path):
    out = backfill({"dry_run": False, "describe": "no"}, tmp_path / "w")["result"]
    assert out["made"] == 4 and described == []
    assert {lib.fake.entry(o["recording"])["dataJson"]["status"] for o in out["done"]} == {"new"}


def test_a_file_that_cannot_be_described_is_said_and_the_batch_goes_on_and_is_not_tried_again(lib, monkeypatch, tmp_path):
    def broken(job, ctx):
        slug = job["args"]["recording"]
        if slug == "rec-00000003-136":
            jobs.write_entry(ctx, slug, {"status": "failed"})
            raise jobs.JobError("could not read “Narration take 3.mp3” as a recording: moov atom not found")
        jobs.write_entry(ctx, slug, {"status": "described", "duration_seconds": 2.0})
        return {"result": {"recording": slug}}

    monkeypatch.setattr(jobs, "media_describe", broken)
    out = backfill({"dry_run": False}, tmp_path / "w")["result"]
    assert out["failed"] == 1 and len(out["done"]) == 4
    bad = next(o for o in out["done"] if o.get("error"))
    assert bad["asset"] == lib.ids["upload"] and "moov atom" in bad["error"] and "1 failed" in out["summary"]
    again = backfill({"dry_run": True}, tmp_path / "w")["result"]
    assert again["counts"]["candidates"] == 0  # its Recording says failed: a person looks at it (media-describe by hand), no loop


def test_marvin_out_of_reach_stops_the_batch_with_what_was_done_and_where_to_go_on(lib, described, monkeypatch, tmp_path):
    real = jobs.find_or_create

    def flaky(ctx, asset, kind):
        if asset["id"] == lib.ids["phone"]:
            raise marvin_api.MarvinUnreachable("Marvin could not be reached after 6 tries")
        return real(ctx, asset, kind)

    monkeypatch.setattr(jobs, "find_or_create", flaky)
    out = backfill({"dry_run": False}, tmp_path / "w")["result"]
    assert [o["asset"] for o in out["done"]] == [lib.ids["take"], lib.ids["upload"]] and out["next"] == lib.ids["upload"] and out["finished"] is False
    assert "stopped after 2 files" in out["summary"]


def test_each_file_is_worked_on_in_a_folder_of_its_own_removed_after_it(lib, monkeypatch, tmp_path):
    seen = []

    def look(job, ctx):
        seen.append((ctx.work, ctx.work.is_dir(), ctx.notes))
        ctx.note("looked")
        return {"result": {"recording": job["args"]["recording"]}}

    monkeypatch.setattr(jobs, "media_describe", look)
    ctx_notes = backfill({"dry_run": False, "limit": 2}, tmp_path / "w")["log"]
    assert [s[0].name for s in seen] == ["rec-00000001-136", "rec-00000003-136"] and all(s[1] for s in seen)
    assert not any(s[0].exists() for s in seen) and ctx_notes


# ── transcribing in a backfill ───────────────────────────────────────────────
def _whisper(monkeypatch):
    words = [NS(word=" Hello", start=0.1, end=0.4)]
    model = NS(transcribe=lambda path, **kw: (iter([NS(text="Hello", start=0.1, end=0.4, words=words)]), NS(language="en", language_probability=0.9, duration=5.0)))
    monkeypatch.setattr(transcribe, "load_model", lambda name: model)
    monkeypatch.setattr(transcribe, "has_sound", lambda path: True)


def test_transcribing_in_a_backfill_hears_each_file_under_slugs_of_its_own(lib, described, monkeypatch, tmp_path):
    """Two Recordings heard in one job: each one's transcript files get a slug of their own (Marvin refuses a second asset under a slug)."""
    _whisper(monkeypatch)
    lib.fake.strict_asset_slugs = True
    out = backfill({"dry_run": False, "kinds": "audio", "transcribe": "yes"}, tmp_path / "w")["result"]
    assert out["failed"] == 0 and [o["asset"] for o in out["done"]] == [lib.ids["take"], lib.ids["upload"], lib.ids["bed"]]
    for o in out["done"]:
        rec = lib.fake.entry(o["recording"])["dataJson"]
        assert rec["status"] == "transcribed" and rec["text"] == "Hello" and rec["transcript"] == f"{o['recording']}-transcript"
    slugs = [a["slug"] for a in lib.fake.uploads()]
    assert len(slugs) == len(set(slugs)) == 12 and "backfill-1-rec-00000001-136-json" in slugs
    assert all(a["metadataJson"]["worker"] == {"kind": "media-backfill", "job": "backfill-1"} for a in lib.fake.uploads())


def test_a_file_with_a_transcript_already_is_joined_to_it_and_not_heard_again(lib, described, monkeypatch, tmp_path):
    _whisper(monkeypatch)
    lib.fake.add_entry("transcript", "transcript-recording-nov-18-2026-54618-pm", {"source": lib.ids["take"], "language": "en"})
    out = backfill({"dry_run": False, "kinds": "audio", "transcribe": True, "limit": 1}, tmp_path / "w")["result"]
    (done,) = out["done"]
    assert done["transcript"] == "transcript-recording-nov-18-2026-54618-pm" and done["transcribed"].startswith("no: it has a Transcript already")
    assert lib.fake.entry("rec-00000001-136")["dataJson"]["transcript"] == "transcript-recording-nov-18-2026-54618-pm"
    assert lib.fake.entry("transcript-recording-nov-18-2026-54618-pm")["dataJson"]["recording"] == "rec-00000001-136"
    assert lib.fake.uploads() == []


# ── media-link ───────────────────────────────────────────────────────────────
STUDIO_TRANSCRIPT = "transcript-recording-nov-18-2026-54618-pm"


@pytest.fixture
def one(marvin, tmp_path):
    """A file with a Recording, and the Transcript Studio made from it before Media (its `source` the file, no `recording`)."""
    (tmp_path / "t.webm").write_bytes(b"x")
    marvin.add_asset(tmp_path / "t.webm", mime="audio/webm", asset_id=_id(1), name="Recording Nov 18, 2026, 5:46:18 PM")
    marvin.add_entry("recording", "rec-00000001-136", {"asset": _id(1), "kind": "audio", "status": "described"})
    marvin.add_entry("transcript", STUDIO_TRANSCRIPT, {"source": _id(1), "language": "en", "words": "asset-w"})
    return marvin


def test_link_joins_a_recording_and_studios_older_transcript_both_ways_and_again_writes_nothing(one, tmp_path):
    out = link({"asset": _id(1)}, tmp_path / "w")["result"]
    assert out["recording"] == "rec-00000001-136" and out["transcript"] == STUDIO_TRANSCRIPT
    assert out["wrote"] == ["rec-00000001-136.transcript", f"{STUDIO_TRANSCRIPT}.recording"]
    assert one.entry("rec-00000001-136")["dataJson"] == {"asset": _id(1), "kind": "audio", "status": "described", "transcript": STUDIO_TRANSCRIPT}
    assert one.entry(STUDIO_TRANSCRIPT)["dataJson"] == {"source": _id(1), "language": "en", "words": "asset-w", "recording": "rec-00000001-136"}
    one.writes.clear()
    again = link({"recording": "rec-00000001-136"}, tmp_path / "w", job="link-2")["result"]
    assert again["wrote"] == [] and one.writes == [] and "joined already" in again["summary"]


def test_link_keeps_the_transcript_a_recording_names_and_gives_every_transcript_of_the_file_its_recording(one, tmp_path):
    one.add_entry("transcript", "rec-00000001-136-transcript", {"source": _id(1), "recording": "rec-00000001-136"})
    one.entry("rec-00000001-136")["dataJson"]["transcript"] = "rec-00000001-136-transcript"
    out = link({"recording": "rec-00000001-136"}, tmp_path / "w")["result"]
    assert out["transcript"] == "rec-00000001-136-transcript" and out["wrote"] == [f"{STUDIO_TRANSCRIPT}.recording"]


def test_link_leaves_a_transcript_that_names_another_recording_alone(one, tmp_path):
    one.entry(STUDIO_TRANSCRIPT)["dataJson"]["recording"] = "my-own-recording"
    out = link({"asset": _id(1)}, tmp_path / "w")["result"]
    assert out["transcript"] is None and out["wrote"] == [] and one.writes == []
    assert one.entry(STUDIO_TRANSCRIPT)["dataJson"]["recording"] == "my-own-recording"


def test_link_looks_again_when_the_named_transcript_is_in_the_trash(one, tmp_path):
    one.add_entry("transcript", "old-one", {"source": _id(1), "recording": "rec-00000001-136"}, status="trashed")
    one.entry("rec-00000001-136")["dataJson"]["transcript"] = "old-one"
    out = link({"asset": _id(1)}, tmp_path / "w")["result"]
    assert out["transcript"] == STUDIO_TRANSCRIPT and one.entry("rec-00000001-136")["dataJson"]["transcript"] == STUDIO_TRANSCRIPT


def test_link_makes_nothing_for_a_file_with_no_recording(marvin, tmp_path):
    (tmp_path / "t.wav").write_bytes(b"x")
    marvin.add_asset(tmp_path / "t.wav", mime="audio/wav", asset_id=_id(5))
    marvin.add_entry("transcript", "t-5", {"source": _id(5)})
    out = link({"asset": _id(5)}, tmp_path / "w")["result"]
    assert out["skipped"] is True and out["recording"] is None and marvin.writes == []
    with pytest.raises(jobs.JobError, match="needs args.asset"):
        link({}, tmp_path / "w")
    with pytest.raises(jobs.JobError, match="no Recording"):
        link({"recording": "rec-nope"}, tmp_path / "w")


# ── end to end, with ffmpeg ──────────────────────────────────────────────────
@needs_ffmpeg
def test_a_backfill_describes_real_files_with_media_describes_own_logic(marvin, tmp_path):
    for n, fixture in ((1, "stereo-wav"), (2, "phone-webm")):
        path, mime = media_files.make(fixture, tmp_path / "files")
        marvin.add_asset(path, mime=mime, asset_id=_id(n), name=fixture)
    out = backfill({"dry_run": False}, tmp_path / "w")["result"]
    assert out["failed"] == 0 and out["made"] == 2, json.dumps(out)[:600]
    wav, webm = (marvin.entry(_rec(_id(n)))["dataJson"] for n in (1, 2))
    assert wav["status"] == webm["status"] == "described" and wav["kind"] == "audio" and webm["kind"] == "video"
    assert wav["duration_seconds"] > 0 and webm["width"] and webm["poster"] and wav["job"] == "backfill-1"
    assert marvin.assets[_id(1)]["metadataJson"]["audio"]["duration_seconds"] == wav["duration_seconds"]  # the file's own metadata too
