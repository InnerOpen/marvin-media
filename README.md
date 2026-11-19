# Marvin Media

A package for [Marvin](https://github.com/InnerOpen/marvin) that gives every audio and video file a **Recording** entry: what the file says
about itself (its length, its make, for a video its picture's size, frame rate and a poster frame, how loud it is, its waveform) measured by a
worker, one Recording per file, and, when asked, what is said in it (transcribed on the worker's CPU with faster-whisper). The file itself is
never changed.

It has two parts:

- **The package** (`docs/media.marvin-package.json`, slug `media`): the entry types, a collection and the workflows. A workspace installs it.
- **The worker kinds** (`mediajobs/`, kinds named `media-…`): the work the workflows hand off, run by the Media worker image on the generic
  [Marvin worker](https://github.com/InnerOpen/marvin-worker).

## What the package brings (m1, m2, m3)

| | slug | what it is |
|---|---|---|
| entry type | `recording` | One audio or video file (`asset`) and its facts: `kind` (audio/video), `source` (recorded/uploaded/generated), `status` (new, described, transcribed, no-speech, failed), `duration_seconds`, `format`, `codec`, `sample_rate`, `channels`, `width`, `height`, `fps`, `has_audio`, `poster`, `level_lufs`, `level_db`, `peak_db`, `noise_floor_db`, `clipped`, `lead_silence`, `tail_silence`, `waveform`, `language`, `text`, `words` (`[{text, start, end}]`, seconds from the file's start), `transcript`; `job`, `notes`. Limits are wide from the start (a length up to 24 hours), because a package update never widens a limit. |
| entry type | `transcript` | Screenplay Studio's Transcript exactly, plus `recording` (a link to the Recording). Where Studio is installed the type is already there and an update adds the one field. |
| collection | `media-recordings` | Every Recording and Transcript. |
| workflow | `media-recording-on-upload` | On `asset_uploaded`: an audio or video file a **person** uploaded or recorded queues a `media-describe` job. Left alone: anything a worker uploaded (`metadata.worker`, or `metadata.kind` from images older than the stamp), a version of another file (`derived_from`), a generated file (`origin: generated`). |
| workflow | `media-describe` | By hand: describe one file (its slug or id), again if it was described before. |
| workflow | `media-archive-done-job` | A finished `media-…` job is archived (`media-transcribe` ones too). **Only for a workspace without Screenplay Studio**, whose own "put a finished job away" already archives media jobs. |
| workflow (m2) | `media-transcribe-recording` | By hand: transcribe one file (its slug or id), with optional names to listen for (`hotwords`) and `language`. Asked by hand, so it runs with `force`: a recording heard before is heard again, and a generated one is transcribed too. |
| workflow (m3) | `media-backfill-dry-run` | By hand: the plan for giving the files **already in the library** a Recording (`media-backfill`, dry run): which files would get one and why each other file would not. Writes nothing. Inputs: how many to list (`limit`, default 50), `kinds` (both, audio, video). |
| workflow (m3) | `media-backfill-run` | By hand, after the dry run: gives up to `limit` of those files a Recording, oldest first, and describes each; transcribes too only if you choose it. Run it again for the next batch. |
| workflow (m3) | `media-recording-asset-trashed` | On `asset_trashed` of an audio or video file: its Recording goes to the Trash too (an `entry trash` step that finds the Recording by its `asset`, `entity_type: entry` spelled out, since on an asset event a bare `trash` would act on the file). A file deleted forever was trashed first, so that is covered. Restoring the file does not restore the Recording. A file with no Recording is passed over; one with two (one made by hand) fails the run, which says so. |
| workflow (m2) | `media-transcribe-on-describe` | On `entry_updated` of a Recording whose `status` **changes to** `described`, when it has sound (`has_audio`, or kind `audio`), runs 1 to 1800 s, is not `generated` and has no `text` yet (never heard), and its publish status did not move in the same write: queues `media-transcribe` for it. It fires once per Recording: the transcriber's own write moves the status on to `transcribed` or `no-speech`, and describing again never moves it back. |

**Every workflow arrives switched off.** Marvin creates a package's workflows disabled; switching one on is a deliberate act (Workflows page).
A package update replaces a workflow's steps but keeps its on/off state.

## The worker kinds

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

`media-transcribe` (m2) takes `asset` (the file's slug or id) or `recording` (a Recording's slug), and `language`, `hotwords`, `model`
(default `small`, or `WHISPER_MODEL`), `force`.

- Finds the Recording as media-describe does. One not described yet (none, `new`, `failed`) is described first with media-describe's own
  logic; that write is a `described` one, so with `media-transcribe-on-describe` on, a second transcribe job is queued, which (the worker
  doing one job at a time) starts after this one and finds the work done.
- Leaves alone, as a result and not a failure, writing nothing: a recording under 1 s or over **1800 s** (30 minutes; `force` does not lift
  this), one a service made (`source: generated`) and one heard already (`transcribed` / `no-speech`), the last two unless `force`.
- A file with no sound (the Recording's `has_audio` false, or no sound stream in it, checked with `av` before the model sees it) or no speech
  (a tone, a hush: the voice filter finds nothing) gets `status: no-speech`, empty `text` and `words`, and no files.
- Otherwise the model runs once, on the CPU, with word times. The Recording gets `language`, `text`, `words` (`[{text, start, end}]` in
  seconds from the file's start, the key asset `metadata.words` uses; above 5,000 words left empty, the JSON file holds them), `status:
  transcribed` and `transcript`. The four files (the words, with times, in paragraphs, as JSON) are uploaded with `metadata.kind:
  transcript`, `derived_from` the recording's file (and `metadata.worker`, stamped by the worker) and set on the Recording's Transcript entry
  (found by the Recording's `transcript` field or by its `recording` field, else made as `<recording>-transcript`), with `source` the file's
  **id** whichever way it was asked. Transcribing again (`force`) writes the same Transcript and moves its old files to the Trash.
- Never writes the file's own `metadata.words` (the captions' source).

`media-link` (m3) takes `asset` (the file's slug or id) or `recording` (a Recording's slug). It makes nothing: it joins the file's Recording
to the Transcript made earlier from the same file (a Transcript whose `source` is the file's id, such as Studio's older
`transcript-recording-nov-18-2026-54618-pm`). The Recording's `transcript` names it (the one it names already while that one is live, else the
oldest that names this Recording back, else the oldest heard from the file), and every Transcript of the file with no `recording` gets this
Recording. A Transcript that names another Recording is left alone. Only what differs is written; a second run writes nothing. A file with no
Recording is said, not made. (The mirror maps onto Studio's beat, take, sound and line come later, in m4.)

`media-backfill` (m3) gives the files already in the library a Recording. Arguments: `limit` (default 50, at most 500), `dry_run` (default
**true**), `kinds` (`audio`, `video` or `both`, the default), `describe` (default true), `transcribe` (default false), `after` (the `next` a
run gave), `include_worker_files` (default false), `min_bytes`. The yes/no ones take a JSON boolean or the text `yes`/`no`/`true`/`false`
(a workflow's Run form sends a choice as text), and anything unclear is the default, so a typo in `dry_run` stays a dry run.

- It reads the library with Marvin's list, `GET /api/platform/assets?asset_type=audio&asset_type=video&sort=oldest&limit=100&offset=N`, page
  after page until a page comes back short (the list pages with `limit`/`offset`; it ignores `per_page`, returns no created-at, and gives the
  total only in a header). It reads the Trash the same way (`trashed=true`), only to count it, and every Recording (in the Trash too) and
  every `voice` entry with a paged `entries/query`.
- Each file gets **one** reason, the first that holds, in this order:

  | reason | the rule |
  |---|---|
  | `trashed` | the file is in the Trash (counted from the Trash's list; never touched) |
  | `recording-in-trash` | a Recording of the file is in the Trash: someone put it there, so it is not made again |
  | `has-recording` | a live Recording names the file in its `asset` |
  | `derived` | `origin: derived`, or `sourceAssetId`, or `metadata.derived_from` (versions, converted audio) |
  | `generated` | `origin: generated`: an ElevenLabs take or sound. Marvin's integration step stores those as a derivative with no source, and in the live sample all 90 generated takes and sounds carry it, so no take or sound entry needs reading |
  | `voice-preview` | `metadata.kind: voice-preview` (Studio v232 on), or the name ends `(voice preview)`, or the description starts `ElevenLabs' preview clip of the voice`, or a `voice` entry names the file in its `preview` field. The 28 older previews carry no metadata at all; Studio's import-voices named and described every one this way |
  | `worker-file` | `metadata.worker` (every worker upload since marvin-worker #16) or `metadata.kind` (renders, audio cuts, converted audio, transcript files, filmstrips, posters from older images). `include_worker_files` takes these in (never the reasons above it) |
  | `too-small` | `fileSize` under `min_bytes` |
  | `not-a-recording` | listed as audio or video but typed neither `audio/*` nor `video/*` |

- A **dry run** reads only: no write, no download. Its result (on the job) is the plan. An illustration, with the numbers the live
  sample (design §1b) leads to expect, not a real run's output:

  ```json
  {"dry_run": true, "kinds": ["audio", "video"], "after": null, "library": 197, "examined": 197,
   "counts": {"candidates": 35, "generated": 90, "worker-file": 44, "voice-preview": 28, "trashed": 3},
   "candidates": [{"asset": "…", "recording": "rec-a6e76f6c-136", "name": "Take one", "bytes": 81234, "type": "audio/webm;codecs=opus",
                   "kind": "audio", "source": "recorded"}, …],
   "more": 0, "next": null,
   "skipped": {"generated": {"count": 90, "meaning": "a service made it (origin generated: …)",
                             "files": [{"asset": "…", "name": "Line 12 (Rachel)", "type": "audio/mpeg", "bytes": 40421, "why": "origin generated"}, …]}, …},
   "summary": "Dry run: 35 of 197 files would get a Recording. Left out: 90 generated, 44 worker-file, 28 voice-preview, 3 trashed. Nothing was written. …"}
  ```

  `candidates` lists the first `limit` files; `more` says how many follow and `next` where a run of that size would stop. Each reason in
  `skipped` names every one of its files (up to 1000 a reason), with what was seen (`metadata.kind=video`, `origin generated`, the
  Recording's slug…), so each group can be checked file by file.
- A **real run** (`dry_run` false) takes the next `limit` files of the plan, oldest first, and for each: finds or makes its Recording
  (`rec-` + 12 characters of the file's id, made with only `{asset, kind, status: new}`, as media-describe makes it), describes it with
  media-describe's own logic in the same job, joins it to an older Transcript of the file (media-link's logic), and with `transcribe`
  transcribes it with media-transcribe's logic (1 to 1800 s; a file with a Transcript already is not heard again). Each file is downloaded into
  a folder of its own, removed when it is done. A file that cannot be read is said in `done[].error` (its Recording is marked `failed`) and
  the batch goes on; Marvin out of reach stops the batch and says where to go on. The result: `done` (one line per file), `made`, `failed`,
  `remaining`, `next`, `finished`; the job's log has a line per file as it goes (`[3/50] Take one: made rec-…, described (12.4 s)`).
- **Resumable and idempotent.** Run it again and it carries on: a file done has a Recording and is passed over (and `after`, the `next` a
  run gave, starts after the last file done without looking at the earlier ones again). A second run over the same library writes nothing.

## Giving the files already in the library a Recording (backfill)

Rebuild the image and recreate the container first (below): the worker must offer `media-backfill` (`WORKER_KINDS` in `docker/compose.yml`).

1. **Dry run.** Workflows → "Media: which files would get a Recording (dry run)" → Run (keep 50, both). Open the job it made (Entries →
   Jobs) once it is `done` and read its result:
   - `counts`: on lowburnstudio (157 audio and 40 video files when this was written) expect about **35 candidates** (3 browser takes and about
     32 hand uploads and sound beds), about 90 `generated`, 28 `voice-preview`, and the renders, cuts and converted audio under `worker-file`.
     If `candidates` is near 125 or 63, the generated or the voice-preview rule did not hold: stop and look.
   - `candidates`: the names should all be files a person recorded or uploaded. `skipped.<reason>.files`: spot-check a few in each.
2. **Switch off** "Media: transcribe each new recording" (`media-transcribe-on-describe`) unless every file the backfill describes should be
   transcribed: each Recording the backfill describes becomes `described`, which is what that workflow listens for.
3. **Run 50.** Workflows → "Media: give existing files a Recording" → Run with How many 50, Which files both, Describe yes, Transcribe no.
   The job takes a few seconds per file (one ffprobe and one decode each; a long video longer). Then check:
   - the job's result: `made` and `failed` (each failure says why in `done[].error`), and `remaining`;
   - the Recordings collection (`media-recordings`): one per file, status `described`, a length, a waveform, a poster for each video;
   - nothing else: no new audio or video in the library (posters are images, filed as versions of their video), and the Studio entries untouched.
4. **Run again** for the next 50, until the result says "All done". Running it once more writes nothing.
5. **Transcribing** is a separate choice: Transcribe yes, in **small batches** (5 to 10). faster-whisper runs on this machine's CPU, minutes
   for a long recording, and the worker does one job at a time, so a batch of 50 long recordings holds every describe behind it for an hour or
   more. Files over 1800 s are skipped, and a file with a Transcript already (Studio's older ones, joined by media-link) is not heard again.

## Installing it in a workspace

1. **Set up workers** first (Settings → Workers → "Set up workers"): core's `job`, `job-kind` and `worker` types. The package needs `job` and
   does not bring it; its workflows are skipped ("needs entry_type:job") until it is there.
2. **Install the package**: Settings → Automation → Packages, with `docs/media.marvin-package.json`; or by API, preview first:
   `POST /api/groups/packages/update/preview`, then `POST /api/groups/packages/update`. In a workspace that already has Screenplay Studio's
   `transcript` type, use **update** (or install, then update once): install leaves an existing type as it is, and only update adds the
   `recording` field to it.
3. **Run a Media worker** for the workspace (below).
4. **Switch on** what you want: `media-recording-on-upload` (every new recording gets a Recording), and, only in a workspace without Screenplay
   Studio, `media-archive-done-job`. Transcription is separate and costs CPU: use `media-transcribe-recording` by hand, or switch on
   `media-transcribe-on-describe` only if every new recording (up to 30 minutes) should be transcribed.

Before switching on the upload workflow, make sure every image that uploads into the workspace stamps its uploads (`metadata.worker`, from
marvin-worker #16) or marks them with `metadata.kind`; otherwise a worker's audio uploads would be described as a person's.

## Running the worker

The image is the generic Marvin worker plus ffmpeg, faster-whisper 1.2.1 and `av` (pinned as Screenplay Studio pins them) and `mediajobs`.
Build it with a checkout of
[marvin-worker](https://github.com/InnerOpen/marvin-worker) **on main** next to this one (`../MarvinWorker`): the kinds need its safe create
(#15) and upload stamp (#16), and the build stops if the base lacks them.

```bash
docker/build.sh                       # builds marvin-worker-base from ../MarvinWorker, then marvin-media-worker:m3 (and :latest)
MEDIA_SKIP_BASE=1 docker/build.sh     # or on the marvin-worker-base already on this machine
docker run --rm -e WORKER_KINDS=media-describe,media-transcribe,media-link,media-backfill marvin-media-worker:m3 python -m marvin_worker kinds   # what it offers; needs no Marvin
```

One worker per workspace (a worker's token is pinned to one workspace). The compose file names everything after the workspace, so several
workspaces run side by side on one machine:

```bash
mkdir -p ~/.config/marvin-media
install -m 600 docker/worker.env.example ~/.config/marvin-media/<workspace>.env   # fill in MARVIN_API_URL and MARVIN_USER_TOKEN
docker volume create transcribe-models        # the model's cache; Screenplay Studio's transcriber made it already where Studio runs
MEDIA_WORKSPACE=<workspace> docker compose -f docker/compose.yml up -d           # container <workspace>-media, offers every kind
```

**The transcriber's cost.** One container offers every kind and runs **one job at a time** (marvin-worker's runner claims a job and runs it
to the end before it polls again), so a transcription never shares the container's CPU with a describe; describes queued behind it wait.
faster-whisper's `small` model on the CPU (int8) takes minutes for a long recording; `MEDIA_WHISPER_THREADS` caps the cores it uses
(default 4, `0` for all) and `MEDIA_WHISPER_MODEL` picks another model (`tiny`, `base`, `small`, `medium`, `large-v3`). The model is
downloaded once, on the first transcription, into the `transcribe-models` volume (`HF_HOME=/models`), shared with Screenplay Studio's
transcriber on the same machine. The 1800 s limit keeps one job from holding the worker for long. A backfill with `transcribe` hears
each file inside its own job, one after another: that is why the owner runs it in small batches.

The token: `marvin user tokens create --name media-worker --pin <workspace>`. It lives only in that env file (mode 600), never in the repository.
Pushing a tag `mN` that matches the package version publishes the image to `ghcr.io/inneropen/marvin-media-worker` (`.github/workflows/publish-image.yml`).

## Shared with Screenplay Studio

`mediajobs/ffmpeg.py`, `audio_measure.py`, `waveform.py` and `video.py` (the describe half of Studio's `clip.py`) came unchanged from Screenplay
Studio (`render/storyrender`, commit 09bef82, v229), `text.py` from its `storytranscribe` (v231), and `transcribe.py`'s `load_model` and
`has_sound` from its `storytranscribe/jobs.py`. Since P6 Studio no longer has copies: its image builds `FROM marvin-media-worker` and its code
imports these modules (`storyrender.ffmpeg`, `storyrender.audio_measure`, `storyrender.waveform` and `storytranscribe.text` *are* these
modules, and `storyrender.clip` takes its describe half from `video.py`). So **a change to one of them changes Studio's renders and jobs**:

- the CI job `studio-on-this-mediajobs` runs Screenplay Studio's own suites (`render` and `transcribe`, with its byte-identical render and
  pull goldens) on this checkout's `mediajobs`;
- `tests/test_parity.py` holds the numbers together: against a Studio checkout when `STUDIO_RENDER` names its `render` folder (exact), else
  against `tests/data/studio_golden.json` (`scripts/studio_golden.py` wrote it from Studio 09bef82). Studio's CI runs it in the exact mode.

Studio gets `mediajobs` the way it gets marvin-worker: in the image from the base it builds on, and in its tests from a checkout next to
its own (`--with-editable ../MarvinMedia`). There is no index package and no tag to pin: the image tag (`marvin-media-worker:mN`) is the pin.

## Development

```bash
uv run --python 3.12 --extra dev --with-editable ../MarvinWorker python -m pytest -q    # needs ffmpeg and ffprobe on PATH
uv run --python 3.12 --extra dev ruff check .
# the package checks with Marvin's own validators and matcher (skipped where Marvin is not installed):
PYTHONPATH=../MarvinWorker:. ../Marvin/.venv/bin/python -m pytest -q tests/test_package.py
# the one test with the real model (a tone video: no speech), where the small model is in the local HF cache; never downloads it:
uv run --python 3.12 --extra dev --extra model --with-editable ../MarvinWorker python -m pytest -q -m slow -rs
```

The package's `version` counts up (1, 2, …); its release tag is `mN`, signed. Changes go through pull requests with CI green.

## Layout

```
docs/media.marvin-package.json   the package (edit by hand; tests/test_package.py checks it)
mediajobs/jobs.py                the worker kinds (media-describe, media-transcribe, media-link, media-backfill)
mediajobs/library.py             which library files a backfill gives a Recording, and why not the others (pure)
mediajobs/recording.py           what a Recording holds, from a file's facts (pure)
mediajobs/transcribe.py          the model (faster-whisper): load, hear once with word times, the limits (from Studio's storytranscribe)
mediajobs/text.py                the transcript's text forms (shared with Studio's storytranscribe)
mediajobs/{ffmpeg,audio_measure,waveform,video}.py   shared with Studio's storyrender: its renders run them
docker/                          the image, its build script, compose for one workspace, the env file's shape
tests/                           a stand-in Marvin (fake_marvin.py), test recordings made with ffmpeg (media_files.py)
tests/data/                      Studio's v229 Transcript (Media's must equal it plus one field); Studio's numbers for the parity check;
                                 the package as m1 and m2 shipped it (each later version keeps every earlier item exactly)
scripts/studio_golden.py         writes those numbers from a Studio checkout
```
