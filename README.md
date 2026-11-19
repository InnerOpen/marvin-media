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

## The worker kinds (m1)

`media-describe` takes `asset` (the file's slug or id) or `recording` (a Recording's slug), and `force`.

- Finds the file's Recording by its `asset` field, or makes one under the fixed slug `rec-` + the first 12 characters of the file's id
  (`rec-a6e76f6c-136`), with only `{asset, kind, status: new}`; the facts follow in a second write (so a workflow listening for a Recording
  becoming `described` hears new ones too). Marvin hands a taken slug the next free one (`-2`) instead of refusing it, so two jobs racing for
  one file end with one Recording: the later copy goes to the Trash.
- Probes the file (a browser's webm has no length in its header: it is decoded to measure it), measures its sound (LUFS, average, peak, noise
  floor, clipped, silence before and after, a 240-point waveform), and for a video makes a poster frame (an image asset with `derived_from` the
  video and `metadata.worker`, under the fixed slug `rec-…-poster`, so a retry reuses it).
- Writes the Recording, and the same facts on the file's own metadata (`video` or `audio`, exactly as Studio's describe-video writes them).
  Writes only what changed. Never takes a Recording's status back from `transcribed` / `no-speech`.
- A Recording already described is left as it is (not even downloaded) unless `force`: so an upload event and a manual ask for the same file
  never do the work twice.

## Installing it in a workspace

1. **Set up workers** first (Settings → Workers → "Set up workers"): core's `job`, `job-kind` and `worker` types. The package needs `job` and
   does not bring it; its workflows are skipped ("needs entry_type:job") until it is there.
2. **Install the package**: Settings → Automation → Packages, with `docs/media.marvin-package.json`; or by API, preview first:
   `POST /api/groups/packages/update/preview`, then `POST /api/groups/packages/update`. In a workspace that already has Screenplay Studio's
   `transcript` type, use **update** (or install, then update once): install leaves an existing type as it is, and only update adds the
   `recording` field to it.
3. **Run a Media worker** for the workspace (below).
4. **Switch on** what you want: `media-recording-on-upload` (every new recording gets a Recording), and, only in a workspace without Screenplay
   Studio, `media-archive-done-job`.

Before switching on the upload workflow, make sure every image that uploads into the workspace stamps its uploads (`metadata.worker`, from
marvin-worker #16) or marks them with `metadata.kind`; otherwise a worker's audio uploads would be described as a person's.

## Running the worker

The image is the generic Marvin worker plus ffmpeg and `mediajobs`. Build it with a checkout of
[marvin-worker](https://github.com/InnerOpen/marvin-worker) next to this one (`../MarvinWorker`):

```bash
docker/build.sh                       # builds marvin-worker-base from ../MarvinWorker, then marvin-media-worker:m1 (and :latest)
MEDIA_SKIP_BASE=1 docker/build.sh     # or on the marvin-worker-base already on this machine
docker run --rm -e WORKER_KINDS=media-describe marvin-media-worker:m1 python -m marvin_worker kinds   # what it offers; needs no Marvin
```

One worker per workspace (a worker's token is pinned to one workspace). The compose file names everything after the workspace, so several
workspaces run side by side on one machine:

```bash
mkdir -p ~/.config/marvin-media
install -m 600 docker/worker.env.example ~/.config/marvin-media/<workspace>.env   # fill in MARVIN_API_URL and MARVIN_USER_TOKEN
MEDIA_WORKSPACE=<workspace> docker compose -f docker/compose.yml up -d           # container <workspace>-media, offers media-describe
```

The token: `marvin user tokens create --name media-worker --pin <workspace>`. It lives only in that env file (mode 600), never in the repository.
Pushing a tag `mN` that matches the package version publishes the image to `ghcr.io/inneropen/marvin-media-worker` (`.github/workflows/publish-image.yml`).

## Where the code came from

`mediajobs/ffmpeg.py`, `audio_measure.py`, `waveform.py` and `video.py` (the describe half of `clip.py`) are copied unchanged from Screenplay
Studio (`render/storyrender`, commit 09bef82, v229), so a file described here gives exactly the numbers Studio's describe-video, describe-audio,
measure-level and make-waveform give. Studio keeps its own copy for now; later Studio's image builds `FROM marvin-media-worker` and imports
these instead. `tests/test_parity.py` holds the two together: against a Studio checkout when `STUDIO_RENDER` names its `render` folder, else
against `tests/data/studio_golden.json` (`scripts/studio_golden.py` writes it).

## Development

```bash
uv run --python 3.12 --extra dev --with-editable ../MarvinWorker python -m pytest -q    # needs ffmpeg and ffprobe on PATH
uv run --python 3.12 --extra dev ruff check .
# the package checks with Marvin's own validators and matcher (skipped where Marvin is not installed):
PYTHONPATH=../MarvinWorker:. ../Marvin/.venv/bin/python -m pytest -q tests/test_package.py
```

The package's `version` counts up (1, 2, …); its release tag is `mN`, signed. Changes go through pull requests with CI green.

## Layout

```
docs/media.marvin-package.json   the package (edit by hand; tests/test_package.py checks it)
mediajobs/jobs.py                the worker kinds (media-describe)
mediajobs/recording.py           what a Recording holds, from a file's facts (pure)
mediajobs/{ffmpeg,audio_measure,waveform,video}.py   copied from Studio, unchanged
docker/                          the image, its build script, compose for one workspace, the env file's shape
tests/                           a stand-in Marvin (fake_marvin.py), test recordings made with ffmpeg (media_files.py)
tests/data/                      Studio's v229 Transcript (Media's must equal it plus one field); Studio's numbers for the parity check
scripts/studio_golden.py         writes those numbers from a Studio checkout
```
