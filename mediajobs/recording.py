"""What a Recording entry holds, worked out from a file's facts: the pure part of `media-describe`, with no call to Marvin or ffmpeg.

A Recording is the companion record of ONE asset (an audio or video file in the library). It is found by its `asset` field; a worker
creates it under the fixed slug `slug_for(asset id)`, so the many ways a file reaches Media (an upload, a manual describe, later a link from
another package or a backfill) end at the same entry."""

from pathlib import Path

from . import audio_measure

ENTRY_TYPE = "recording"
SLUG_PREFIX = "rec-"
ID_CHARS = 12  # the first 12 characters of the asset's id (a UUID: 8 hex, a hyphen, 3 hex): rec-a6e76f6c-136
POSTER_KIND = "recording-poster"  # metadata.kind on a poster this package made: worker output, for images built before metadata.worker
STATUSES = ("new", "described", "transcribed", "no-speech", "failed")
HEARD = ("transcribed", "no-speech")  # what a transcriber has said: describing again never takes it back to `described`
VIDEO_SUFFIXES = (".webm", ".mp4", ".mov", ".m4v", ".mkv")  # as Studio's describe-video reads a file whose type was not sent
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".weba")


def slug_for(asset_id: str) -> str:
    """The Recording's slug for an asset: `rec-` and the first 12 characters of the asset's id, lower-case. Fixed, so whoever asks first makes
    it and everyone else finds it."""
    return SLUG_PREFIX + str(asset_id).strip().lower()[:ID_CHARS]


def poster_slug(recording_slug: str) -> str:
    """The poster's slug: fixed too, so a job tried again finds the poster it already uploaded instead of making a second one."""
    return f"{recording_slug}-poster"


def is_video(mime: "str | None", name: str) -> bool:
    return str(mime or "").startswith("video/") or Path(name).suffix.lower() in VIDEO_SUFFIXES


def is_audio(mime: "str | None", name: str) -> bool:
    return str(mime or "").startswith("audio/") or Path(name).suffix.lower() in AUDIO_SUFFIXES


def kind_guess(mime: "str | None", name: str) -> str:
    """What the file's type says it is, before anyone has looked inside: `video` or `audio`. A browser's video/webm that holds only sound is
    found out when it is described (see `kind_of`)."""
    return "audio" if str(mime or "").startswith("audio/") or not is_video(mime, name) else "video"


def kind_of(mime: "str | None", streams: list[dict]) -> str:
    """`video` for a file with a picture, else `audio`: Studio's v229 rule. A file typed audio/* is a sound even with cover art in it (its
    streams are not looked at); a video/webm a browser recorded with the camera off is a sound too."""
    if str(mime or "").startswith("audio/"):
        return "audio"
    return "video" if any(s.get("codec_type") == "video" for s in streams) else "audio"


def source_of(asset: dict) -> str:
    """Where the file came from: `recorded` when the browser's recorder made it (it marks `metadata.recording.source`), `generated` when a
    service made it (Marvin's `origin`), else `uploaded`."""
    meta = asset.get("metadataJson") or {}
    if isinstance(meta.get("recording"), dict) and meta["recording"].get("source") == "browser":
        return "recorded"
    if asset.get("origin") == "generated":
        return "generated"
    return "uploaded"


def sound_make(probed: dict) -> dict:
    """The sound stream's codec, sample rate and channels, and the one-line format, from ffprobe's JSON (`audio_measure.probe_audio`): the same
    values `audio_measure.describe` gives a sound, for the sound inside a video. Empty when the file has no sound."""
    stream = next((s for s in probed.get("streams", []) if s.get("codec_type") == "audio"), None)
    if stream is None:
        return {}
    rate, channels = str(stream.get("sample_rate") or ""), stream.get("channels")
    return {
        "codec": str(stream.get("codec_name") or "") or None, "sample_rate": int(rate) if rate.isdigit() else None,
        "channels": channels if isinstance(channels, int) and channels > 0 else None, "format": audio_measure.format_summary(probed),
    }  # fmt: skip


def heard_facts(*, seconds: float, edges: tuple[float, float], loudness: "tuple | None", levels: tuple[float, float], stats: "dict | None", wave: "dict | None") -> dict:
    """The Recording's sound fields from the measures Studio's jobs take: describe-audio's (LUFS, peak, noise floor, clipped, the silence before
    and after), measure-level's average (`level_db`, the volumedetect mean, as a Sound's) and make-waveform's peaks."""
    mean, peak = levels
    out: dict = {"level_db": mean, "peak_db": peak, "clipped": peak >= audio_measure.CLIP_DB, "lead_silence": edges[0], "tail_silence": edges[1]}
    if loudness:
        out["level_lufs"] = loudness[0]
    if stats:
        out["noise_floor_db"] = stats["noise_floor_db"]
    if wave:
        out["waveform"] = wave
    return out


def fields(kind: str, described: dict, make: dict, heard: dict) -> dict:
    """The Recording's fact fields: the file's own facts (`described`: video.describe's for a video, audio_measure.describe's for a sound),
    the sound's make, and what was heard in it. A fact the file does not say (None) is left out rather than written empty."""
    out: dict = {"kind": kind, "duration_seconds": described["duration_seconds"]}
    if kind == "video":
        out.update(width=described["width"], height=described["height"], fps=described["fps"], has_audio=described["has_audio"])
    else:
        out["has_audio"] = True
    for key in ("codec", "sample_rate", "channels", "format"):
        out[key] = make.get(key, described.get(key))
    out.update(heard)
    return {k: v for k, v in out.items() if v is not None and v != ""}


def next_status(current: "str | None") -> str:
    """`described`, unless a transcriber has already been: describing again never undoes `transcribed` or `no-speech`."""
    return current if current in HEARD else "described"


def is_done(data: dict, asset_meta: dict) -> bool:
    """A Recording that was described and whose file's own metadata holds the facts too: nothing to do unless asked to again (`force`)."""
    key = data.get("kind")
    return data.get("status") in ("described", *HEARD) and data.get("duration_seconds") is not None and key in ("audio", "video") and isinstance(asset_meta.get(key), dict)
