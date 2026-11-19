"""Which files in a workspace's Assets library are a person's own recordings that have no Recording yet: the pure part of `media-backfill`,
with no call to Marvin. The job lists the library (and its Trash) and the Recordings, and this module says, for each audio or video file,
whether it gets a Recording or why not, with ONE reason per file, tested in a fixed order (`REASONS`).

The rules follow the upload filter (design §3) and the live library they were written against (lowburnstudio, 2026-11-18: 157 audio and 40
video files, every one uploaded with the owner's own token, so `uploadedBy` tells nothing apart):
- a version of another file: `origin` derived, `sourceAssetId`, or `metadata.derived_from`;
- a file a service made (an ElevenLabs take or sound): `origin` generated. Every generated take and sound in the sample (90 of 90) is stored
  by Marvin's integration step as a derivative with no source (`create_derivative(source=None)`), which is what `origin: generated` says, so
  the entries that hold the file (a take's or sound's `source: generated`) need not be read;
- an ElevenLabs voice preview: `metadata.kind` voice-preview (Studio v232 and later), or, for the 28 older ones that carry no metadata at
  all, Studio's import-voices naming: the name ends “(voice preview)”, or the description starts “ElevenLabs' preview clip of the voice”,
  or a `voice` entry names the file in its `preview` field;
- anything else a worker made: `metadata.worker` (every upload since marvin-worker #16) or `metadata.kind` (renders, audio cuts, converted
  audio, transcript files, posters, filmstrips… from images older than that).
"""

from __future__ import annotations

from . import recording

PREVIEW_NAME = "(voice preview)"  # Studio's import-voices names a preview “<voice> (voice preview)”
PREVIEW_DESCRIPTION = "elevenlabs' preview clip of the voice"  # …and describes it “ElevenLabs' preview clip of the voice <name>.”
PREVIEW_KIND = "voice-preview"
KINDS = {"audio": ("audio",), "video": ("video",), "both": ("audio", "video")}  # args.kinds -> the library's asset types to list

# Every reason a file is left out, in the order they are tested (the first that holds is the one said), and what it means.
REASONS = {
    "trashed": "the file is in the Trash",
    "recording-in-trash": "its Recording is in the Trash: someone put it there, so it is not made again",
    "has-recording": "it has a Recording already",
    "derived": "a version made from another file (origin derived, or derived_from)",
    "generated": "a service made it (origin generated: an ElevenLabs take or sound)",
    "voice-preview": "an ElevenLabs voice preview (Studio's import-voices)",
    "worker-file": "a worker made it (metadata.worker or metadata.kind: a render, audio cut, converted audio, transcript file…)",
    "too-small": "smaller than min_bytes",
    "not-a-recording": "its type is neither audio/* nor video/*",
}


def flag(value, default: bool) -> bool:
    """A yes/no argument as a job or a workflow's run form sends it: a JSON boolean, or text (a run form's choice is always text). Anything
    unclear is the default, so a typo in `dry_run` stays a dry run."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "yes", "y", "1", "on"):
        return True
    if text in ("false", "no", "n", "0", "off"):
        return False
    return default


def whole(value, default: int, *, low: int, high: int) -> int:
    """A count argument (a number, or the text of one), kept between `low` and `high`; the default when it is missing or not a number."""
    try:
        n = int(float(str(value).strip())) if value is not None and str(value).strip() else default
    except ValueError:
        n = default
    return max(low, min(high, n))


def kinds_of(value) -> tuple[str, ...]:
    text = str(value or "both").strip().lower().replace(" ", "")
    if text in ("audio,video", "video,audio", ""):
        text = "both"
    if text not in KINDS:
        raise ValueError(f"kinds is audio, video or both, not {value!r}")
    return KINDS[text]


def is_voice_preview(asset: dict, previews: "set[str] | frozenset[str]" = frozenset()) -> bool:
    meta = asset.get("metadataJson") or {}
    name = str(asset.get("name") or "").strip().lower()
    description = str(asset.get("description") or "").strip().lower()
    return (
        meta.get("kind") == PREVIEW_KIND
        or name.endswith(PREVIEW_NAME)
        or description.startswith(PREVIEW_DESCRIPTION)
        or str(asset.get("id")) in previews
    )


def worker_marker(asset: dict) -> "str | None":
    """What says a worker made the file, as text for the plan (`metadata.kind=video`), or None for a file with no such marker."""
    meta = asset.get("metadataJson") or {}
    if isinstance(meta.get("worker"), dict) or meta.get("worker"):
        who = meta["worker"].get("kind") if isinstance(meta["worker"], dict) else meta["worker"]
        return f"metadata.worker.kind={who}"
    if meta.get("kind"):
        return f"metadata.kind={meta['kind']}"
    return None


def reason(asset: dict, *, recorded: dict[str, str], previews=frozenset(), include_worker_files: bool = False, min_bytes: int = 0) -> "tuple[str, str] | None":
    """(reason, detail) when the file gets no Recording, else None. `recorded` maps an asset id to `live` or `trashed` (the state of its
    Recording: a live one wins over one in the Trash); `previews` the ids a `voice` entry names as its preview."""
    meta = asset.get("metadataJson") or {}
    asset_id = str(asset.get("id"))
    mime = str(asset.get("mimeType") or "")
    if asset.get("trashedAt"):
        return "trashed", f"trashed {asset['trashedAt']}"
    state = recorded.get(asset_id)
    if state == "live":
        return "has-recording", recording.slug_for(asset_id)
    if state == "trashed":
        return "recording-in-trash", recording.slug_for(asset_id)
    if asset.get("origin") == "derived" or asset.get("sourceAssetId") or meta.get("derived_from"):
        return "derived", f"from {asset.get('sourceAssetId') or meta.get('derived_from') or '?'}"
    if asset.get("origin") == "generated":
        return "generated", "origin generated"
    if is_voice_preview(asset, previews):
        return "voice-preview", f"metadata.kind={meta['kind']}" if meta.get("kind") == PREVIEW_KIND else "named as Studio's import-voices names a preview"
    if not include_worker_files and (marker := worker_marker(asset)):
        return "worker-file", marker
    size = asset.get("fileSize")
    if min_bytes and isinstance(size, int) and size < min_bytes:
        return "too-small", f"{size} bytes"
    if not (mime.startswith("audio/") or mime.startswith("video/")):
        return "not-a-recording", mime or "no type"
    return None


def candidate(asset: dict) -> dict:
    """What the plan says about a file that gets a Recording: the Recording's slug and the file's name, size and type."""
    meta = asset.get("metadataJson") or {}
    mime, name = str(asset.get("mimeType") or ""), str(asset.get("name") or asset.get("slug") or "")
    return {
        "asset": str(asset["id"]), "recording": recording.slug_for(str(asset["id"])), "name": name, "bytes": asset.get("fileSize"), "type": mime,
        "kind": recording.kind_guess(mime, name), "source": "recorded" if isinstance(meta.get("recording"), dict) and meta["recording"].get("source") == "browser" else "uploaded",
    }  # fmt: skip


def after(rows: list[dict], cursor: "str | None") -> tuple[list[dict], bool]:
    """The rows after the one whose id is `cursor` (all of them when there is no cursor), and whether the cursor was found. A cursor that
    is not in the list any more (its file was trashed since) starts again from the top, which is safe: a file done already has a Recording
    and is passed over."""
    if not cursor:
        return rows, True
    ids = [str(r.get("id")) for r in rows]
    if cursor not in ids:
        return rows, False
    return rows[ids.index(cursor) + 1 :], True
