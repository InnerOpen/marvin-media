# Marvin Media

A package for [Marvin](https://github.com/InnerOpen/marvin) that gives every audio and video file a **Recording** entry: what the file says
about itself (its length, its make, for a video its picture's size, frame rate and a poster frame, how loud it is, its waveform) measured by a
worker, one Recording per file. The file itself is never changed.

It has two parts:

- **The package** (`docs/media.marvin-package.json`, slug `media`): the entry types, a collection and the workflows. A workspace installs it.
- **The worker kinds** (`mediajobs/`, kinds named `media-…`): the work the workflows hand off, run by the Media worker image on the generic
  [Marvin worker](https://github.com/InnerOpen/marvin-worker).

## What the package brings (m1)

| | slug | what it is |
|---|---|---|
| entry type | `recording` | One audio or video file (`asset`) and its facts: `kind` (audio/video), `source` (recorded/uploaded/generated), `status` (new, described, transcribed, no-speech, failed), `duration_seconds`, `format`, `codec`, `sample_rate`, `channels`, `width`, `height`, `fps`, `has_audio`, `poster`, `level_lufs`, `level_db`, `peak_db`, `noise_floor_db`, `clipped`, `lead_silence`, `tail_silence`, `waveform`, and for later (m2) `language`, `text`, `words`, `transcript`; `job`, `notes`. Limits are wide from the start (a length up to 24 hours), because a package update never widens a limit. |
| entry type | `transcript` | Screenplay Studio's Transcript exactly, plus `recording` (a link to the Recording). Where Studio is installed the type is already there and an update adds the one field. |
| collection | `media-recordings` | Every Recording and Transcript. |
| workflow | `media-recording-on-upload` | On `asset_uploaded`: an audio or video file a **person** uploaded or recorded queues a `media-describe` job. Left alone: anything a worker uploaded (`metadata.worker`, or `metadata.kind` from images older than the stamp), a version of another file (`derived_from`), a generated file (`origin: generated`). |
| workflow | `media-describe` | By hand: describe one file (its slug or id), again if it was described before. |
| workflow | `media-archive-done-job` | A finished `media-…` job is archived. **Only for a workspace without Screenplay Studio**, whose own "put a finished job away" already archives media jobs. |

**Every workflow arrives switched off.** Marvin creates a package's workflows disabled; switching one on is a deliberate act (Workflows page).
A package update replaces a workflow's steps but keeps its on/off state.

## Installing it in a workspace

1. **Set up workers** first (Settings → Workers → "Set up workers"): core's `job`, `job-kind` and `worker` types. The package needs `job` and
   does not bring it; its workflows are skipped ("needs entry_type:job") until it is there.
2. **Install the package**: Settings → Automation → Packages, with `docs/media.marvin-package.json`; or by API, preview first:
   `POST /api/groups/packages/update/preview`, then `POST /api/groups/packages/update`. In a workspace that already has Screenplay Studio's
   `transcript` type, use **update** (or install, then update once): install leaves an existing type as it is, and only update adds the
   `recording` field to it.
3. **Run a Media worker** for the workspace (the `media-describe` kind and its image come in the next change).
4. **Switch on** what you want: `media-recording-on-upload` (every new recording gets a Recording), and, only in a workspace without Screenplay
   Studio, `media-archive-done-job`.

Before switching on the upload workflow, make sure every image that uploads into the workspace stamps its uploads (`metadata.worker`, from
marvin-worker #16) or marks them with `metadata.kind`; otherwise a worker's audio uploads would be described as a person's.

## Development

```bash
uv run --python 3.12 --extra dev --with-editable ../MarvinWorker python -m pytest -q
uv run --python 3.12 --extra dev ruff check .
# the package checks with Marvin's own validators and matcher (skipped where Marvin is not installed):
PYTHONPATH=../MarvinWorker:. ../Marvin/.venv/bin/python -m pytest -q tests/test_package.py
```

The package's `version` counts up (1, 2, …); its release tag is `mN`, signed. Changes go through pull requests with CI green.

## Layout

```
docs/media.marvin-package.json   the package (edit by hand; tests/test_package.py checks it)
tests/data/                      Screenplay Studio's v229 Transcript, which Media's must equal plus one field
```
