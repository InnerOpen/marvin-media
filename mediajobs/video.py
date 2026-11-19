# Shared with Screenplay Studio: from P6 its storyrender/clip.py imports the describe half from here (describe, poster, seconds and
# the rest below), so Studio's describe-video and the render's reading of a clip run this very code; how a clip is laid over a shot
# stays in Studio. It came from Studio's render/storyrender/clip.py at 09bef82 (v229). A change here changes what Studio renders
# (see README, "Shared with Screenplay Studio").
"""What a video is: its length as a player plays it, its picture's size after a phone's turn, its frame rate, whether it has sound, and a
poster frame. The describe half of Studio's clip.py."""

import subprocess
from collections.abc import Callable
from pathlib import Path


def seconds(file: Path, probe: Callable[[Path], float]) -> float:
    """How long a clip's file runs: what `probe` measures (the same probe the lines use), or, when that fails, how long its picture runs."""
    try:
        return float(probe(file))
    except Exception as unheard:  # noqa: BLE001
        try:  # a browser's webm with no sound (the microphone was refused) and no length in its header: the probe listens, so watch it instead
            return _picture_seconds(file)
        except Exception:  # noqa: BLE001
            raise unheard from None  # what the probe said is the reason worth giving


def _picture_seconds(path: Path) -> float:
    """How long a video's picture runs, by decoding it to nowhere (for a file whose header has no length and which has no sound to measure)."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-an", "-f", "null", "-progress", "pipe:1", "-"], capture_output=True, text=True)
    micro = max((int(v) for k, _, v in (line.partition("=") for line in done.stdout.splitlines()) if k == "out_time_us" and v.strip().isdigit()), default=0)
    if done.returncode != 0 or micro <= 0:
        raise ValueError(f"ffmpeg can't read {path.name} as a video: {done.stderr.strip()[-200:]}")
    return micro / 1_000_000



# ── what a clip is (the describe-video job) ──────────────────────────────────
POSTER_AT = 1.0  # seconds in: past a webcam's first dark frames; a clip shorter than twice this is pictured at its middle
POSTER_LONG_SIDE = 720  # the poster's longest side, in pixels (smaller frames are kept as they are)
MAX_FPS = 240  # a frame rate above this is a container's time base (a browser's webm says 1000), not the picture's


def _rate(text) -> float | None:
    top, _, bottom = str(text or "").partition("/")
    try:
        rate = float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if 0 < rate <= MAX_FPS else None


def _turn(stream: dict) -> int:
    """The quarter turns a player gives the picture, in degrees (0, 90, 180, 270): from its side data, or an older muxer's `rotate` tag."""
    raw = next((d.get("rotation") for d in stream.get("side_data_list") or [] if "rotation" in d), (stream.get("tags") or {}).get("rotate", 0))
    try:
        return round(float(raw)) % 360
    except (TypeError, ValueError):
        return 0


def describe(file: Path) -> dict:
    """A clip's facts as a player shows them: {duration_seconds, width, height, fps, has_audio}. The size is after a phone's turn (a portrait
    take stored landscape reads portrait); `fps` is None when neither the file's rates nor its frame count say. A file with no picture raises."""
    from . import ffmpeg

    streams = ffmpeg.probe_video(file).get("streams") or []
    picture = next((s for s in streams if s.get("codec_type") == "video"), None)
    if picture is None:
        raise ffmpeg.FfmpegError(f"{file.name} has no picture")
    total = seconds(file, ffmpeg.duration)
    width, height = int(picture.get("width") or 0), int(picture.get("height") or 0)
    if _turn(picture) % 180 == 90:
        width, height = height, width
    fps = _rate(picture.get("avg_frame_rate")) or _rate(picture.get("r_frame_rate"))
    if fps is None and total > 0:
        fps = _rate(f"{ffmpeg.packets(file)}/{total}")
    return {
        "duration_seconds": round(total, 3), "width": width, "height": height, "fps": round(fps, 3) if fps else None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }  # fmt: skip


def poster_at(total: float) -> float:
    return POSTER_AT if total >= 2 * POSTER_AT else max(total / 2, 0.0)


def poster(file: Path, total: float, out: Path) -> Path:
    """A JPEG of the clip's picture about a second in (the middle of a short one), turned as a player turns it. The seek is made after reading
    the file's start, so a browser's webm (no index to seek by) is read up to it; a seek that finds no frame falls back to the first."""
    from . import ffmpeg

    scale = f"scale='if(gte(iw,ih),min({POSTER_LONG_SIDE},iw),-2)':'if(gte(iw,ih),-2,min({POSTER_LONG_SIDE},ih))'"
    for at in dict.fromkeys((poster_at(total), 0.0)):
        out.unlink(missing_ok=True)
        ffmpeg.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(file), "-ss", f"{at:.3f}", "-frames:v", "1", "-vf", scale, "-q:v", "3", str(out)])
        if out.is_file() and out.stat().st_size > 0:
            return out
    raise ffmpeg.FfmpegError(f"no frame of {file.name} could be pictured")
