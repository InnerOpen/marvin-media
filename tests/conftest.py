import shutil
from pathlib import Path

import pytest
from marvin_worker.jobs import Context

from mediajobs import jobs
from tests.fake_marvin import FakeMarvin

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


@pytest.fixture
def marvin(monkeypatch) -> FakeMarvin:
    return FakeMarvin().wire(monkeypatch)


def describe(args: dict, work: Path, job: str = "describe-1") -> dict:
    """Run media-describe as the worker would, in its own scratch folder."""
    work.mkdir(parents=True, exist_ok=True)
    return jobs.media_describe({"job": job, "kind": jobs.DESCRIBE, "args": args}, Context(creds=("http://marvin.test", "t"), work=work, name=job))
