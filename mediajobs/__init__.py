"""The Media package's worker kinds: a Recording entry for each audio or video file, with what the file says about itself.

Two halves. `mediajobs.jobs` is the worker module (WORKER_JOB_MODULES=mediajobs.jobs): the `media-…` kinds, and the only module here that needs
marvin-worker. The rest makes no call to Marvin. `library` and `recording` are Media's own; these are shared with Screenplay Studio, whose
image builds FROM marvin-media-worker (P6):
`ffmpeg` (running ffmpeg and ffprobe), `audio_measure` (loudness, peak, silences), `waveform`, `video` (what a video is, and its poster),
`text` (a transcript's text forms) and `transcribe` (loading the model). Studio's storyrender and storytranscribe import those by these
names, so a change to one of them is a change to Studio's renders and jobs too."""
