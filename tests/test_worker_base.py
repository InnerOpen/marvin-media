"""The generic Marvin worker this package's kinds run on (InnerOpen/marvin-worker, checked out next to this repository) is one that has what
they use: a create that is safe to try again (marvin-worker #15) and the `metadata.worker` stamp on every file a job uploads (#16), which is how
Media's upload workflow tells a worker's file from a person's."""

from marvin_worker import marvin_api
from marvin_worker.jobs import job_kind


def test_the_worker_base_has_a_safe_create_and_stamps_what_a_job_uploads():
    assert callable(marvin_api.create_entry) and callable(job_kind)
    with marvin_api.working("media-describe", "job-1"):
        assert marvin_api.stamped({"kind": "x"}) == {"kind": "x", "worker": {"kind": "media-describe", "job": "job-1"}}
    assert marvin_api.stamped({}) == {}  # outside a job, nothing is added
