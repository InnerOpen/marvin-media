"""The Media package file (docs/media.marvin-package.json): what it brings, that Marvin's own checks accept every piece of it, that every
workflow arrives switched off, and which files and job events its workflows answer, through Marvin's own condition matcher.

The checks that need Marvin import it and are skipped where it is not installed; run them with Marvin's interpreter:
    PYTHONPATH=../MarvinWorker ../Marvin/.venv/bin/python -m pytest -q tests/test_package.py
CI runs them against a checkout of InnerOpen/marvin, where a missing Marvin fails them (.github/workflows/tests.yml)."""

import importlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs" / "media.marvin-package.json"
MANIFEST = json.loads(PACKAGE.read_text(encoding="utf-8"))
BLUEPRINTS = {b["slug"]: b for b in MANIFEST["blueprints"]}
STUDIO_TRANSCRIPT = json.loads((ROOT / "tests" / "data" / "studio_transcript_v229.json").read_text(encoding="utf-8"))
WORKFLOWS = ("media-recording-on-upload", "media-describe", "media-archive-done-job")


def marvin(module: str):
    """One of Marvin's own modules; the test is skipped where Marvin is not installed, unless MEDIA_REQUIRE_MARVIN=1 (CI's Marvin job), where
    a missing Marvin fails it: a check that silently skipped would prove nothing."""
    return importlib.import_module(module) if os.environ.get("MEDIA_REQUIRE_MARVIN") == "1" else pytest.importorskip(module)


def _flow(slug: str) -> dict:
    return BLUEPRINTS[slug]["payload"]["definition"]


def _fields(slug: str) -> dict:
    return {f["key"]: f for f in BLUEPRINTS[slug]["payload"]["schema_json"]["fields"]}


# ── what it is ───────────────────────────────────────────────────────────────
def test_the_package_is_media_at_its_own_version_with_exactly_these_pieces():
    assert (MANIFEST["format"], MANIFEST["slug"], MANIFEST["name"]) == (1, "media", "Media")
    assert MANIFEST["version"].isdigit() and int(MANIFEST["version"]) >= 1
    assert [(b["kind"], b["slug"]) for b in MANIFEST["blueprints"]] == [
        ("entry_type", "recording"), ("entry_type", "transcript"), ("collection", "media-recordings"), *(("workflow", s) for s in WORKFLOWS),
    ]  # fmt: skip


def test_it_never_brings_the_worker_types_core_sets_up():
    """job, job-kind and worker are core's (Settings, Workers, "Set up workers"): Media only needs them."""
    assert not {"job", "job-kind", "worker"} & set(BLUEPRINTS)
    for slug in WORKFLOWS:
        assert BLUEPRINTS[slug]["requires"] == ["entry_type:job"], slug


def test_every_workflow_arrives_switched_off():
    from_marvin = marvin("marvin.schemas.platform.blueprints")
    assert "workflow" in from_marvin.ACTS_WHEN_ENABLED_KINDS  # Marvin creates every workflow of a package disabled
    for slug in WORKFLOWS:
        assert "enabled" not in BLUEPRINTS[slug]["payload"] and "enabled" not in _flow(slug), slug
    assert "enabled" not in json.dumps(MANIFEST)


# ── Recording ────────────────────────────────────────────────────────────────
def test_a_recording_has_the_designs_fields_and_wide_limits_from_the_start():
    """An update never widens a field's min or max (v228: a Sound capped at 30 s refused a 72 s bed), so the limits are wide now."""
    fields = _fields("recording")
    assert list(fields) == [
        "asset", "kind", "source", "status", "duration_seconds", "format", "codec", "sample_rate", "channels", "width", "height", "fps", "has_audio",
        "poster", "level_lufs", "level_db", "peak_db", "noise_floor_db", "clipped", "lead_silence", "tail_silence", "waveform", "language", "text",
        "words", "transcript", "job", "notes",
    ]  # fmt: skip
    assert fields["asset"]["type"] == "asset" and fields["asset"]["required"] is True and fields["asset"]["accept"] == "audio/*,video/*"
    assert fields["kind"]["options"] == ["audio", "video"] and fields["source"]["options"] == ["recorded", "uploaded", "generated"]
    assert fields["status"]["options"] == ["new", "described", "transcribed", "no-speech", "failed"] and fields["status"]["defaultValue"] == "new"
    assert fields["duration_seconds"]["max"] >= 86400 and fields["duration_seconds"]["min"] == 0  # hours of recording; no 30 s trap
    assert (fields["width"]["max"], fields["height"]["max"], fields["fps"]["max"]) == (16384, 16384, 240)
    for key in ("level_lufs", "level_db", "peak_db", "noise_floor_db"):
        assert (fields[key]["min"], fields[key]["max"]) == (-120, 20), key
    for key in ("lead_silence", "tail_silence"):
        assert fields[key]["max"] >= 86400, key
    assert fields["has_audio"]["type"] == fields["clipped"]["type"] == "boolean"
    assert fields["waveform"]["type"] == fields["words"]["type"] == "json" and fields["text"]["type"] == "textarea"
    assert fields["transcript"] == {**fields["transcript"], "type": "text", "ref": "transcript"}
    assert fields["poster"]["type"] == "asset" and BLUEPRINTS["recording"]["payload"]["is_rendered"] is False


# ── Transcript ───────────────────────────────────────────────────────────────
def test_the_transcript_is_studios_exactly_plus_a_link_to_its_recording():
    """Studio's v229 `transcript` blueprint, unchanged, and one field more. Where Studio is installed the type is there already, and an update
    only adds the missing `recording` field; a label or limit that differed would never be changed by an update, so they must not differ."""
    mine = BLUEPRINTS["transcript"]
    studio_fields = STUDIO_TRANSCRIPT["payload"]["schema_json"]["fields"]
    assert mine["payload"]["schema_json"]["fields"][:-1] == studio_fields
    assert mine["payload"]["schema_json"]["fields"][-1] == {
        "key": "recording", "label": "Recording entry", "type": "text", "ref": "recording", "helpText": "The Recording entry of the file that was transcribed (Media keeps it).",
    }  # fmt: skip
    assert {k: v for k, v in mine.items() if k != "payload"} == {k: v for k, v in STUDIO_TRANSCRIPT.items() if k != "payload"}
    assert {k: v for k, v in mine["payload"].items() if k != "schema_json"} == {k: v for k, v in STUDIO_TRANSCRIPT["payload"].items() if k != "schema_json"}


def test_the_collection_gathers_recordings_and_transcripts():
    c = BLUEPRINTS["media-recordings"]
    assert c["requires"] == ["entry_type:recording", "entry_type:transcript"]
    assert c["payload"]["is_smart"] is True and c["payload"]["smart_rules"] == {"entry_types": ["recording", "transcript"], "match": "all"}


# ── Marvin's own checks ──────────────────────────────────────────────────────
def test_the_whole_file_is_a_package_marvin_accepts():
    packages = marvin("marvin.schemas.platform.packages")
    manifest = packages.PackageManifest.model_validate(MANIFEST)  # also refuses an id or a secret in any payload
    assert [b.slug for b in manifest.blueprints] == list(BLUEPRINTS)


def test_both_entry_types_are_schemas_marvin_accepts():
    schema = marvin("marvin.schemas.platform.entry_type_schema")
    for slug in ("recording", "transcript"):
        schema.EntryTypeSchemaDefinition.model_validate(BLUEPRINTS[slug]["payload"]["schema_json"])


def test_every_workflow_passes_marvins_structural_and_advisory_checks():
    validation = marvin("marvin.services.automation.validation")
    for slug in WORKFLOWS:
        assert validation.structural_issues(_flow(slug)) == [], slug
        assert validation.validate_definition(_flow(slug)) == [], slug


# ── the upload workflow ──────────────────────────────────────────────────────
ID = "a6e76f6c-1362-43f3-8bfa-c11642c974d2"


def _upload(mime, meta=None, *, derived=None, origin="upload"):
    """An asset_uploaded event's context as Marvin's engine builds it (automation/engine.py `_item_context`)."""
    return {"event": {"event_type": "asset_uploaded"}, "asset": {"id": ID, "name": "r", "mime_type": mime, "metadata": meta or {}, "derived_from": derived, "origin": origin}}


def test_an_upload_queues_a_describe_job_for_the_file():
    flow = _flow("media-recording-on-upload")
    assert flow["trigger"] == {"type": "event", "event": "asset_uploaded"}
    (act,) = flow["actions"]
    assert act == {"kind": "entry", "op": "create", "entry_type": "job", "title": "Describe: ${asset.name|truncate:60}", "status": "draft",
                   "data": {"kind": "media-describe", "args": {"asset": "${asset.id}"}, "status": "queued"}}  # fmt: skip


def test_a_persons_recording_or_upload_is_matched_and_anything_a_worker_or_a_service_made_is_not():
    matcher = marvin("marvin.services.automation.matcher")
    conds = _flow("media-recording-on-upload")["conditions"]
    matched = {
        "browser recording": _upload("audio/webm;codecs=opus", {"recording": {"source": "browser", "mimeType": "audio/webm"}}),
        "browser video": _upload("video/webm", {"recording": {"source": "browser"}}),
        "hand-uploaded mp3": _upload("audio/mpeg"),
        "phone video": _upload("video/mp4"),
        "no origin said": {**_upload("audio/wav"), "asset": {**_upload("audio/wav")["asset"], "origin": None}},
    }
    skipped = {
        "a worker's upload (P0 stamp)": _upload("audio/mpeg", {"worker": {"kind": "import-voices", "job": "job-1"}}),
        "a voice preview stamped by the worker": _upload("audio/mpeg", {"worker": {"kind": "import-voices", "job": "j"}, "voice": "abc"}),
        "an older worker's render": _upload("video/mp4", {"kind": "video"}),
        "an older worker's audio cut": _upload("audio/mpeg", {"kind": "audio-cut"}),
        "a version of another file": _upload("audio/wav", derived="b0b0"),
        "a generated take": _upload("audio/mpeg", origin="generated"),
        "a picture": _upload("image/png"),
        "a poster": _upload("image/jpeg", {"kind": "recording-poster", "worker": {"kind": "media-describe", "job": "j"}}, derived=ID),
        "a transcript file": _upload("text/plain", {"worker": {"kind": "transcribe", "job": "j"}}),
    }
    for why, ctx in matched.items():
        assert matcher.matches(conds, ctx), why
    for why, ctx in skipped.items():
        assert not matcher.matches(conds, ctx), why


# ── describing by hand ───────────────────────────────────────────────────────
def test_describing_by_hand_asks_for_the_file_and_always_describes_it_again():
    flow = _flow("media-describe")
    (given,) = flow["trigger"]["inputs"]
    assert flow["trigger"]["type"] == "manual"
    assert given == {"name": "asset", "label": "Recording (the audio or video asset's slug or id)", "type": "text", "required": True}
    (act,) = flow["actions"]
    assert act["data"] == {"kind": "media-describe", "args": {"asset": "${input.asset}", "force": True}, "status": "queued"}


# ── putting a finished job away ──────────────────────────────────────────────
DONE_JOB = "media-archive-done-job"


def test_a_finished_media_job_is_archived_by_a_workflow_that_only_archives():
    flow = _flow(DONE_JOB)
    assert flow["trigger"] == {"type": "event", "event": "entry_updated"}
    assert flow["conditions"] == [
        {"field": "entry.entry_type", "op": "eq", "value": "job"},
        {"field": "entry.data.kind", "op": "starts_with", "value": "media-"},  # Media's jobs only: another package's are its own business
        {"field": "entry.data.status", "op": "changed_to", "value": "done"},  # the moment it finishes, not every later write
        {"field": "entry.status", "op": "eq", "value": "draft"},  # never moves a job someone published, or one in the Trash
    ]
    assert flow["actions"] == [{"kind": "entry", "op": "archive"}]


def _event(*, status="draft", data_status="done", changed=("data.status",), after=None, kind="media-describe"):
    """As Studio's test_job_archive builds it: the job's own update, or the echo of archiving it."""
    return {
        "event": {"event_type": "entry_updated", "changed_fields": list(changed), "before": {}, "after": after if after is not None else {"data": {"status": data_status}}},
        "entry": {"entry_type": "job", "status": status, "data": {"kind": kind, "status": data_status}, "metadata": {}},
    }


def test_with_marvins_matcher_finishing_fires_once_and_the_archive_echo_fires_nothing():
    """Archiving is an edit too (v224): it sends its own entry_updated. The job's Status did not change in it, so `changed_to` fails."""
    matcher = marvin("marvin.services.automation.matcher")
    conds = _flow(DONE_JOB)["conditions"]
    assert matcher.matches(conds, _event(changed=("data.status", "data.result", "data.log")))
    assert not matcher.matches(conds, _event(status="archived", changed=("status",), after={"status": "archived"}))  # the echo
    for still_working in ("queued", "claimed", "running", "failed"):
        assert not matcher.matches(conds, _event(data_status=still_working)), still_working
    for other in ("render", "transcribe", "audio-cut", "describe-video"):
        assert not matcher.matches(conds, _event(kind=other)), other
    assert not matcher.matches(conds, _event(status="published"))
