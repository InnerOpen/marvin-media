import shutil
from pathlib import Path

import pytest
from marvin_worker import marvin_api
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


def transcribe(args: dict, work: Path, job: str = "transcribe-1") -> dict:
    """Run media-transcribe as the worker would: in its own scratch folder, with the worker's stamp on what it uploads (the runner sets it)."""
    work.mkdir(parents=True, exist_ok=True)
    with marvin_api.working(jobs.TRANSCRIBE, job):
        return jobs.media_transcribe({"job": job, "kind": jobs.TRANSCRIBE, "args": args}, Context(creds=("http://marvin.test", "t"), work=work, name=job))
