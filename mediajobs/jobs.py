"""The kinds of job the Media package (docs/media.marvin-package.json) hands to a worker. Every kind is named `media-…`, so none can take the
place of another package's kind in an image that loads several (Screenplay Studio's, later).

m1: `media-describe`, a Recording entry for one audio or video file, with what the file says about itself.
m2: `media-transcribe`, what is said in a Recording's file (faster-whisper, on the CPU), on the Recording and in a Transcript's files.

The worker runs one job at a time (marvin_worker's runner claims a job and runs it to the end in its one loop before it polls again), so a
long transcription and a describe never share the CPU inside one container: the describe waits."""

import json
from pathlib import Path

from marvin_worker import marvin_api
from marvin_worker.jobs import Context, JobError, job_kind

from . import audio_measure, ffmpeg, recording, transcribe, video, waveform

DESCRIBE = "media-describe"
TRANSCRIBE = "media-transcribe"
TRANSCRIPT_TYPE = "transcript"
TRANSCRIPT_KIND = "transcript"  # metadata.kind on a transcript file: worker output, as Studio's transcribe marks its files


def _args(job: dict) -> dict:
    return (job.get("args") or {}) if isinstance(job.get("args"), dict) else {}


def _row(ctx: Context, slug: str) -> "dict | None":
    return next(iter(marvin_api.query(ctx.creds, {"entries": slug}, limit=1)), None)


def _data(row: dict) -> dict:
    return dict(row.get("dataJson") or row.get("data") or {})


def recordings_of(ctx: Context, asset_id: str) -> list[dict]:
    """The Recordings of one asset, oldest first (the Trash left out). One is expected; a second can only come from a race or a hand-made one."""
    spec = {"entry_type": recording.ENTRY_TYPE, "data": {"asset": asset_id}, "sort": {"by": "created_at", "direction": "asc"}}
    return marvin_api.query(ctx.creds, spec, limit=20)


def _type_id(ctx: Context, slug: str) -> str:
    types = {t["slug"]: t["id"] for t in json.loads(marvin_api.request(ctx.creds, "/api/platform/entry-types"))}
    if slug not in types:
        raise JobError(f"this workspace has no “{slug}” entry type: install the Media package (docs/media.marvin-package.json) first")
    return str(types[slug])


def find_or_create(ctx: Context, asset: dict, kind: str) -> tuple[dict, bool]:
    """(the asset's Recording, whether this job made it). Found by its `asset` field first, so a Recording made by hand (with a slug of its own)
    counts. Otherwise made under the fixed slug with ONLY {asset, kind, status: new}: the facts follow in a second write, so a workflow that
    listens for a Recording becoming `described` hears every one, a new one too.

    Marvin gives a create whose slug is taken the next free one (`rec-…-2`) instead of refusing it. So when the slug comes back changed, the
    Recordings of the asset are read again: if another job made one meanwhile (the oldest), this job's is moved to the Trash and that one is
    used. If this job's is the only one (the slug is held by a Recording in the Trash), it is kept as it is."""
    asset_id = str(asset["id"])
    found = recordings_of(ctx, asset_id)
    if found:
        return found[0], False
    slug = recording.slug_for(asset_id)
    title = str(asset.get("name") or asset.get("slug") or slug)
    made = marvin_api.create_entry(ctx.creds, _type_id(ctx, recording.ENTRY_TYPE), slug, title, {"asset": asset_id, "kind": kind, "status": "new"})
    if made.get("slug") == slug:
        return made, True
    first = next(iter(recordings_of(ctx, asset_id)), None)
    if first is not None and first.get("id") != made.get("id"):
        marvin_api.request(ctx.creds, f"/api/platform/entries/{made['id']}", method="DELETE")
        ctx.note(f"another job made {first.get('slug')} for this file at the same moment: that one is used, the copy ({made.get('slug')}) is in the Trash")
        return first, False
    ctx.note(f"{slug} is taken (by a Recording in the Trash): this one is {made.get('slug')}")
    return made, True


def write_entry(ctx: Context, slug: str, put: dict) -> bool:
    """Merge `put` into the entry's fields as they are NOW (read just before), and write only when something changes: an unchanged write
    would still be an `entry_updated` for every workflow to weigh. True when it wrote."""
    row = _row(ctx, slug)
    if row is None:
        raise JobError(f"the Recording “{slug}” is gone (moved to the Trash while the file was being described?)")
    before = _data(row)
    after = {**before, **put}
    if after == before:
        return False
    body = json.dumps({"title": row.get("title"), "slug": row.get("slug"), "dataJson": after}).encode()
    marvin_api.request(ctx.creds, f"/api/platform/entries/{row['id']}", method="PATCH", data=body, content_type="application/json")
    return True


def write_asset_facts(ctx: Context, asset: dict, key: str, facts: dict) -> bool:
    """The facts on the file's own metadata too, under `video` or `audio`, exactly as Studio's describe-video writes them (v226, v229), the other
    keys kept. Written only when they change. One that cannot be written is said, not fatal: the Recording has them."""
    meta = dict(asset.get("metadataJson") or {})
    if meta.get(key) == facts:
        return False
    try:
        body = json.dumps({"metadata": {**meta, key: facts}}).encode()
        marvin_api.request(ctx.creds, f"/api/platform/assets/{asset['id']}", method="PATCH", data=body, content_type="application/json")
    except marvin_api.MarvinError as e:
        ctx.note(f"could not write the facts to the file's own metadata ({str(e).splitlines()[0][:120]}); the Recording has them")
        return False
    return True


def _find_asset(ctx: Context, ref: str) -> "dict | None":
    try:
        found = marvin_api.asset(ctx.creds, ref)
    except marvin_api.MarvinError:
        return None
    return None if found.get("trashedAt") else found


def _ours(poster: "dict | None", asset_id: str) -> bool:
    meta = (poster or {}).get("metadataJson") or {}
    return meta.get("kind") == recording.POSTER_KIND and meta.get("video") == asset_id


def make_poster(ctx: Context, src: Path, total: float, rec_slug: str, asset_id: str, name: str, job_slug: str, *, force: bool) -> "str | None":
    """The video's poster frame as an image asset: its id, or None (said) when no frame could be pictured. The poster has a fixed slug, so a
    job tried again finds the one it uploaded (unless `force`, which makes a new one). It says what made it: `metadata.worker` and `kind`
    (worker output, never a person's upload) and `derived_from` the video (Marvin files it as a version of the video)."""
    slug = recording.poster_slug(rec_slug)
    if not force and _ours(had := _find_asset(ctx, slug), asset_id):
        return str(had["id"])
    out = ctx.work / f"{slug}.jpg"
    at = round(video.poster_at(total), 3)
    try:
        video.poster(src, total, out)
        made = marvin_api.upload_asset(
            ctx.creds, out, slug=slug, name=f"Poster: {name}", mime="image/jpeg", description=f"A frame of {name}, about {at:g} s in.",
            metadata={"kind": recording.POSTER_KIND, "recording": rec_slug, "video": asset_id, "at": at, "derived_from": asset_id, "worker": {"kind": DESCRIBE, "job": job_slug}},
        )  # fmt: skip
    except (ffmpeg.FfmpegError, marvin_api.MarvinError) as e:
        ctx.note(f"no poster: {str(e).splitlines()[0][:200]}")
        return None
    return str(made["id"])


def _trash_old_poster(ctx: Context, ref, keep: str, asset_id: str) -> None:
    """A poster this package made for this file and no longer used goes to the Trash; anything else in the field is left alone."""
    if not ref or not isinstance(ref, str) or ref == keep:
        return
    try:
        if _ours(_find_asset(ctx, ref), asset_id):
            marvin_api.request(ctx.creds, f"/api/platform/assets/{ref}", method="DELETE")
    except marvin_api.MarvinError as e:
        ctx.note(f"the old poster {ref} was left in the library ({str(e).splitlines()[0][:120]})")


def listen(ctx: Context, src: Path, seconds: float) -> dict:
    """What is heard in the file, measured as Studio's describe-audio, measure-level and make-waveform measure it. A measure that fails is said
    and left out: the file's length and make are known already."""
    try:
        edges = audio_measure.silence_edges(src, seconds)
        heard = audio_measure.loudness(src)
        levels = audio_measure.levels(src)
        stats = audio_measure.stats(src)
    except ffmpeg.FfmpegError as e:
        ctx.note(f"could not measure the sound: {str(e).splitlines()[0][:200]}")
        return {}
    try:
        wave = waveform.peaks(src)
    except ffmpeg.FfmpegError as e:
        ctx.note(f"no waveform: {str(e).splitlines()[0][:200]}")
        wave = None
    return recording.heard_facts(seconds=seconds, edges=edges, loudness=heard, levels=levels, stats=stats, wave=wave)


@job_kind(
    DESCRIBE,
    description="Make (or find) the Recording entry of an audio or video file and write what the file says about itself: its length, its make (codec, sample rate, channels), for a video its picture's size as a player shows it, frame rate, whether it has sound and a poster frame; for anything with sound how loud it is (LUFS, average, peak, noise floor, clipped), the silence before and after, and its waveform. The same facts go on the file's own metadata (`video` or `audio`). Nothing is changed in the file. A Recording already described is left as it is unless `force`",
    inputs={
        "asset": {"type": "asset", "required": False, "accepts": ["audio/*", "video/*"], "description": "the audio or video file (its slug or id)"},
        "recording": {"type": "string", "required": False, "description": "or the Recording entry's slug: its file is described again"},
        "force": {"type": "boolean", "required": False, "description": "describe it again even when it was described (and make a new poster)"},
    },
    produces=["image/jpeg"],
    needs=["ffmpeg"],
)
def media_describe(job: dict, ctx: Context) -> dict:
    """An audio or video file → its Recording (found or made) with the file's facts, and the same facts on the file's own metadata."""
    args = _args(job)
    ref, rec_ref, force = str(args.get("asset") or "").strip(), str(args.get("recording") or "").strip(), args.get("force") is True
    if rec_ref:
        rec_row = _row(ctx, rec_ref)
        if rec_row is None:
            raise JobError(f"no Recording “{rec_ref}”")
        held = str(_data(rec_row).get("asset") or "").strip()
        if not held:
            raise JobError(f"the Recording “{rec_ref}” has no file in its asset field")
        if ref and ref != held:
            raise JobError(f"the Recording “{rec_ref}” is of another file than “{ref}”: give one or the other")
        ref = held
    if not ref:
        raise JobError("media-describe needs args.asset (the file's slug or id) or args.recording (a Recording's slug)")
    try:
        asset = marvin_api.asset(ctx.creds, ref)
    except marvin_api.MarvinError as e:
        raise JobError(f"no file “{ref}” ({str(e).splitlines()[0][:160]})") from e
    asset_id, mime = str(asset["id"]), str(asset.get("mimeType") or "")
    name = str(asset.get("name") or asset.get("slug") or ref)
    if asset.get("trashedAt"):
        raise JobError(f"“{name}” is in the Trash")
    if not (recording.is_video(mime, name) or recording.is_audio(mime, name)):
        raise JobError(f"“{name}” ({mime or 'unknown type'}) is not a recording: media-describe takes an audio or video file")
    job_slug = str(job.get("job") or ctx.name or "")

    rec, made = find_or_create(ctx, asset, recording.kind_guess(mime, name))
    rec_slug, before = str(rec["slug"]), _data(rec)
    if not force and not made and recording.is_done(before, asset.get("metadataJson") or {}):
        said = f"{rec_slug} was described already ({before.get('duration_seconds'):g} s): left as it is. Ask with force to describe it again."
        return {"result": {"recording": rec_slug, "asset": asset_id, "skipped": True, "summary": said}, "log": said}

    src = ctx.work / f"recording{Path(str(asset.get('originalFilename') or name)).suffix or '.bin'}"
    marvin_api.download_asset(ctx.creds, asset_id, src)
    try:
        streams = [] if mime.startswith("audio/") else ffmpeg.probe_video(src).get("streams") or []
        key = recording.kind_of(mime, streams)
        described = video.describe(src) if key == "video" else audio_measure.describe(src)
        make = recording.sound_make(audio_measure.probe_audio(src)) if key == "video" and described["has_audio"] else {}
    except (ffmpeg.FfmpegError, ValueError) as e:
        if before.get("status") in (None, "", "new", "failed"):
            write_entry(ctx, rec_slug, {"status": "failed", "job": job_slug} if job_slug else {"status": "failed"})
        raise JobError(f"could not read “{name}” as a recording: {str(e).splitlines()[0][:200]}") from e
    total = float(described["duration_seconds"])
    ctx.note(f"{name}: {key}, {total:g} s" + (f", {described['width']}x{described['height']}" if key == "video" else ""))
    heard = listen(ctx, src, total) if key == "audio" or described["has_audio"] else {}
    wrote_meta = write_asset_facts(ctx, asset, key, described)

    put = recording.fields(key, described, make, heard)
    if key == "video":
        poster = make_poster(ctx, src, total, rec_slug, asset_id, name, job_slug, force=force)
        if poster:
            put["poster"] = poster
    if not str(before.get("source") or "").strip():  # a Recording that says where it came from is believed (a person may have corrected it)
        put["source"] = recording.source_of(asset)
    put["status"] = recording.next_status(before.get("status"))
    if job_slug:
        put["job"] = job_slug
    wrote = write_entry(ctx, rec_slug, put)
    if put.get("poster"):  # a new poster took the old one's place (a poster that failed leaves the old one where it is)
        _trash_old_poster(ctx, before.get("poster"), put["poster"], asset_id)

    facts = {k: v for k, v in put.items() if k not in ("waveform", "job")}
    said = f"{name}: {total:g} s of {key}" + (f", {described['width']}x{described['height']}" if key == "video" else "")
    said += (f", {heard['level_lufs']:g} LUFS" if "level_lufs" in heard else "") + (", no sound" if key == "video" and not described["has_audio"] else "")
    said += f"; {'made' if made else 'found'} {rec_slug}" + (", written" if wrote else ", unchanged") + ("; the file's metadata updated" if wrote_meta else "")
    return {"result": {"recording": rec_slug, "asset": asset_id, "created": made, **facts}, "log": said}


# ── media-transcribe ────────────────────────────────────────────────────────
FILE_MIME = {"text": "text/plain", "timestamped": "text/plain", "paragraphs": "text/plain", "json": "application/json"}
FILE_LABEL = {"text": "transcript", "timestamped": "transcript with times", "paragraphs": "transcript in paragraphs", "json": "transcript (JSON)"}
TRANSCRIPT_FIELD = {"text": "words", "timestamped": "timestamped", "paragraphs": "paragraphs", "json": "json"}  # file -> the Transcript's field


def transcript_slug(rec_slug: str) -> str:
    """The Transcript's slug: fixed, so a job tried again (or a second one) finds the Transcript it made instead of making another."""
    return f"{rec_slug}-transcript"


def described_recording(ctx: Context, args: dict, job_slug: str) -> dict:
    """The Recording to transcribe, described: found by its slug (`recording`) or its file (`asset`), and made and described first, with
    media-describe's own logic, when it is missing or not described yet. Describing writes `described`, which is what the workflow
    media-transcribe-on-describe listens for: with it on, that queues a second transcribe job, which finds this one's work done and skips."""
    ref, rec_ref = str(args.get("asset") or "").strip(), str(args.get("recording") or "").strip()
    row = None
    if rec_ref:
        row = _row(ctx, rec_ref)
        if row is None:
            raise JobError(f"no Recording “{rec_ref}”")
        held = str(_data(row).get("asset") or "").strip()
        if ref and held and ref != held:
            raise JobError(f"the Recording “{rec_ref}” is of another file than “{ref}”: give one or the other")
    elif ref:
        try:
            found = marvin_api.asset(ctx.creds, ref)
        except marvin_api.MarvinError as e:
            raise JobError(f"no file “{ref}” ({str(e).splitlines()[0][:160]})") from e
        row = next(iter(recordings_of(ctx, str(found["id"]))), None)
    else:
        raise JobError("media-transcribe needs args.asset (the file's slug or id) or args.recording (a Recording's slug)")
    if row is not None and _data(row).get("status") not in (None, "", "new", "failed"):
        return row
    ctx.note("not described yet: describing it first")
    said = media_describe({"job": job_slug, "kind": DESCRIBE, "args": {"recording": row["slug"]} if row else {"asset": ref}}, ctx)
    return _row(ctx, said["result"]["recording"]) or {}


def _upload_files(ctx: Context, files: dict, *, asset_id: str, rec_slug: str, title: str, job_slug: str, language: str, seconds: float) -> dict:
    """The transcript's files as assets, each saying what it is (`kind: transcript`), which file it was heard from (`derived_from`, so Marvin
    files it as a version of the recording, and `source`) and which Recording; the worker adds `metadata.worker` itself. {file: asset id}."""
    out = {}
    for key, path in files.items():
        made = marvin_api.upload_asset(
            ctx.creds, path, slug=f"{job_slug or rec_slug}-{key}", name=f"{FILE_LABEL[key][:1].upper()}{FILE_LABEL[key][1:]}: {title}", mime=FILE_MIME[key],
            description=f"Speech in {title}, as text (faster-whisper).",
            metadata={"kind": TRANSCRIPT_KIND, "source": asset_id, "derived_from": asset_id, "recording": rec_slug, "language": language, "seconds": seconds},
        )  # fmt: skip
        out[key] = str(made["id"])
    return out


def _transcript_of(ctx: Context, rec: dict) -> "dict | None":
    """The Recording's Transcript: the one its `transcript` field names, else the oldest that names the Recording back."""
    named = str(_data(rec).get("transcript") or "").strip()
    if named and (row := _row(ctx, named)) is not None:
        return row
    spec = {"entry_type": TRANSCRIPT_TYPE, "data": {"recording": rec["slug"]}, "sort": {"by": "created_at", "direction": "asc"}}
    return next(iter(marvin_api.query(ctx.creds, spec, limit=5)), None)


def _trash_old_files(ctx: Context, before: dict, keep: set[str], asset_id: str) -> None:
    """Files a transcription of THIS recording made and a new one replaced go to the Trash; anything else in the fields is left alone."""
    for key in TRANSCRIPT_FIELD.values():
        ref = before.get(key)
        if not ref or not isinstance(ref, str) or ref in keep:
            continue
        old = _find_asset(ctx, ref)
        meta = (old or {}).get("metadataJson") or {}
        if meta.get("kind") == TRANSCRIPT_KIND and meta.get("derived_from") == asset_id:
            try:
                marvin_api.request(ctx.creds, f"/api/platform/assets/{ref}", method="DELETE")
            except marvin_api.MarvinError as e:
                ctx.note(f"the old transcript file {ref} was left in the library ({str(e).splitlines()[0][:120]})")


def keep_transcript(ctx: Context, rec: dict, fields: dict, title: str) -> str:
    """Find or make the Recording's Transcript and write the files and facts to it (its notes kept). Its slug."""
    row = _transcript_of(ctx, rec)
    if row is None:
        slug = transcript_slug(rec["slug"])
        row = marvin_api.create_entry(ctx.creds, _type_id(ctx, TRANSCRIPT_TYPE), slug, f"Transcript: {title}", fields)
        return str(row["slug"])
    before = _data(row)
    write_entry(ctx, str(row["slug"]), fields)
    _trash_old_files(ctx, before, {str(v) for v in fields.values()}, str(fields["source"]))
    return str(row["slug"])


@job_kind(
    TRANSCRIBE,
    description="Hear what is said in a Recording's audio or video file with faster-whisper, on this machine's CPU (free), and write it to the Recording: the language, the words as text, and each word with its start and end in seconds from the file's start ([{text, start, end}]); status transcribed, or no-speech when nothing is said. The words, the words with times, in paragraphs and as JSON are kept as files on the Recording's Transcript entry (made or found). A Recording not described yet is described first. Only recordings of 1 to 1800 seconds; a generated one (a made voice) or one heard already is left alone unless `force`. The file itself and its metadata are never changed",
    inputs={
        "asset": {"type": "asset", "required": False, "accepts": ["audio/*", "video/*"], "description": "the audio or video file (its slug or id)"},
        "recording": {"type": "string", "required": False, "description": "or the Recording entry's slug"},
        "language": {"type": "string", "required": False, "description": "the spoken language as a code, e.g. en; empty to detect it"},
        "hotwords": {"type": "string", "required": False, "description": "names and unusual words to listen for, so they are spelled right"},
        "model": {"type": "string", "required": False, "description": "tiny, base, small, medium or large-v3 (default small)"},
        "force": {"type": "boolean", "required": False, "description": "transcribe it even when it was heard already, or a service made it"},
    },
    produces=["text/plain", "application/json"],
    needs=["faster-whisper"],
)
def media_transcribe(job: dict, ctx: Context) -> dict:
    """A Recording's file → what is said in it, on the Recording (language, text, words, status) and as files on its Transcript."""
    args = _args(job)
    force, job_slug = args.get("force") is True, str(job.get("job") or ctx.name or "")
    rec = described_recording(ctx, args, job_slug)
    rec_slug, data = str(rec.get("slug") or ""), _data(rec)

    def answer(put: dict, said: str, **result) -> dict:
        if put and job_slug:
            put["job"] = job_slug
        if put:
            write_entry(ctx, rec_slug, put)
        return {"result": {"recording": rec_slug, "asset": data.get("asset"), **result, "summary": said}, "log": said}

    if why := transcribe.refusal(data, force=force):
        return answer({}, f"{rec_slug} was not transcribed: {why}.", skipped=True)
    asset_id = str(data.get("asset") or "")
    if data.get("has_audio") is False:
        return answer({"status": "no-speech", "text": "", "words": []}, f"{rec_slug} has no sound: nothing to hear", words=0)
    try:
        asset = marvin_api.asset(ctx.creds, asset_id)
    except marvin_api.MarvinError as e:
        raise JobError(f"the file of {rec_slug} cannot be read ({str(e).splitlines()[0][:160]})") from e
    title = str(asset.get("name") or asset.get("slug") or asset_id)
    src = ctx.work / f"recording{Path(str(asset.get('originalFilename') or title)).suffix or '.bin'}"
    marvin_api.download_asset(ctx.creds, asset_id, src)
    if not transcribe.has_sound(src):
        return answer({"status": "no-speech", "text": "", "words": []}, f"{title} has no sound stream: nothing to hear", words=0)

    model = transcribe.load_model(str(args.get("model") or transcribe.MODEL))
    language, hotwords = str(args.get("language") or "").strip() or None, str(args.get("hotwords") or "").strip() or None
    made = transcribe.transcribe_file(model, src, ctx.work / (job_slug or "transcript"), language=language, hotwords=hotwords, note=ctx.note)
    if not made["files"]:
        return answer({"status": "no-speech", "text": "", "words": []}, f"no speech heard in {title}", words=0, seconds=made["seconds"])

    files = _upload_files(ctx, made["files"], asset_id=asset_id, rec_slug=rec_slug, title=title, job_slug=job_slug, language=made["language"], seconds=made["seconds"])
    fields = {TRANSCRIPT_FIELD[k]: v for k, v in files.items()}
    fields.update(source=asset_id, recording=rec_slug, language=made["language"], duration_seconds=data.get("duration_seconds", made["seconds"]))
    if job_slug:
        fields["job"] = job_slug
    kept = keep_transcript(ctx, rec, fields, title)
    words = transcribe.on_recording(made["words"])
    said = f"{len(made['words'])} words heard in {title} ({made['language']}), {made['segments']} segments; written to {rec_slug} and {kept}"
    if made["words"] and not words:
        said += f" (over {transcribe.WORDS_ON_RECORDING} words: the word times are in the Transcript's JSON file only)"
    put = {"language": made["language"], "text": made["text"], "words": words, "status": "transcribed", "transcript": kept}
    return answer(put, said, language=made["language"], seconds=made["seconds"], segments=made["segments"], words=len(made["words"]), transcript=kept, files=files)
