"""Test recordings, made with ffmpeg's own generators (lavfi), so no media file is kept in the repository. The ones the design's parity check
names (§8.3): a phone's portrait webm with no length in its header, a stereo wav, a silent mp4; and a portrait take stored landscape with a
turn, and a browser's video/webm recorded with the camera off (sound only)."""

import subprocess
from pathlib import Path


def _ff(*args: str) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", *args]


def phone_webm(path: Path, seconds: float = 2.5) -> Path:
    """A browser's portrait webcam take with sound, written as a browser writes it (piped: no length in the header)."""
    cmd = _ff("-f", "lavfi", "-i", f"color=c=red:s=120x160:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
              "-c:v", "libvpx", "-c:a", "libopus", "-f", "webm", "pipe:1")  # fmt: skip
    path.write_bytes(subprocess.run(cmd, capture_output=True, check=True).stdout)
    return path


def silent_webm(path: Path, seconds: float = 2.0) -> Path:
    """A browser's take with the microphone refused: a picture, no sound, and no length in its header."""
    cmd = _ff("-f", "lavfi", "-i", f"color=c=green:s=160x120:r=30:d={seconds}", "-c:v", "libvpx", "-f", "webm", "pipe:1")
    path.write_bytes(subprocess.run(cmd, capture_output=True, check=True).stdout)
    return path


def camera_off_webm(path: Path, seconds: float = 1.5) -> Path:
    """A browser's video/webm recorded with the camera off: sound only, no length in its header."""
    cmd = _ff("-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "libopus", "-f", "webm", "pipe:1")
    path.write_bytes(subprocess.run(cmd, capture_output=True, check=True).stdout)
    return path


def stereo_wav(path: Path) -> Path:
    """A stereo wav at 44.1 kHz: half a second of silence, a second of tone, then 0.4 s of silence (so the silence at each end is measured)."""
    subprocess.run(_ff("-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=44100", "-af", "adelay=500,apad=pad_dur=0.4", "-ac", "2", str(path)), check=True)
    return path


def silent_mp4(path: Path, seconds: float = 3.0) -> Path:
    """A landscape mp4 with no sound track at all."""
    subprocess.run(_ff("-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=25:d={seconds}", "-pix_fmt", "yuv420p", str(path)), check=True)
    return path


def turned_mp4(path: Path, seconds: float = 2.0) -> Path:
    """A phone's portrait take as a phone stores it: landscape, with a quarter turn for the player, and sound."""
    flat = path.with_name(path.stem + "-flat.mp4")
    subprocess.run(_ff("-f", "lavfi", "-i", f"color=c=red:s=160x120:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(flat)), check=True)  # fmt: skip
    done = subprocess.run(_ff("-display_rotation", "90", "-i", str(flat), "-c", "copy", str(path)), capture_output=True)
    if done.returncode != 0:  # an older ffmpeg: the `rotate` tag
        subprocess.run(_ff("-i", str(flat), "-c", "copy", "-metadata:s:v:0", "rotate=90", str(path)), check=True)
    flat.unlink(missing_ok=True)
    return path


# name -> (maker, file name, mime type as a browser or the library sends it)
FIXTURES = {
    "phone-webm": (phone_webm, "phone.webm", "video/webm"),
    "silent-webm": (silent_webm, "silent.webm", "video/webm"),
    "camera-off-webm": (camera_off_webm, "voice.webm", "video/webm"),
    "stereo-wav": (stereo_wav, "stereo.wav", "audio/wav"),
    "silent-mp4": (silent_mp4, "silent.mp4", "video/mp4"),
    "turned-mp4": (turned_mp4, "turned.mp4", "video/mp4"),
}


def make(name: str, folder: Path) -> tuple[Path, str]:
    maker, file, mime = FIXTURES[name]
    folder.mkdir(parents=True, exist_ok=True)
    return maker(folder / file), mime
