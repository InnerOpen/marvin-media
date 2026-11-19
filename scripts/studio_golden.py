"""Write tests/data/studio_golden.json: what Screenplay Studio's own jobs give for each test recording (tests/media_files.py), so the parity
check (tests/test_parity.py) runs where no Studio checkout is. Run it again whenever Studio's describe code changes (until P6, when Studio uses
this package's code and the two cannot drift):

    STUDIO_RENDER=../ScreenplayStudio/render PYTHONPATH=../MarvinWorker:. python scripts/studio_golden.py

Studio's code is imported from STUDIO_RENDER; its commit (or STUDIO_COMMIT, for an exported copy) is written into the file."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), os.environ.get("STUDIO_RENDER") or sys.exit("set STUDIO_RENDER to a ScreenplayStudio checkout's render folder")]

from tests import media_files, studio_side  # noqa: E402

HAS_AUDIO = {"phone-webm": True, "silent-webm": False, "camera-off-webm": True, "stereo-wav": True, "silent-mp4": False, "turned-mp4": True}


def main() -> None:
    render = Path(os.environ["STUDIO_RENDER"]).resolve()
    commit = os.environ.get("STUDIO_COMMIT") or subprocess.run(["git", "-C", str(render), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"
    version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    out: dict = {"studio": commit, "ffmpeg": version, "files": {}}
    with tempfile.TemporaryDirectory() as tmp:
        for name in media_files.FIXTURES:
            file, mime = media_files.make(name, Path(tmp) / name)
            out["files"][name] = studio_side.run(file, mime, Path(tmp) / f"{name}-work", HAS_AUDIO[name])
    (ROOT / "tests" / "data" / "studio_golden.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote tests/data/studio_golden.json from Studio {commit} ({len(out['files'])} files)")


if __name__ == "__main__":
    main()
