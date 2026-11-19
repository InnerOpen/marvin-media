"""Hearing what is said in a recording, with faster-whisper on this machine's CPU: the part of `media-transcribe` that runs the model, with no
call to Marvin. Lifted from Screenplay Studio's storytranscribe/jobs.py (`load_model`, `transcribe_file`, `heard_words`, `has_sound`), with
one change: the model runs ONCE, with word times, and both the segments (the three text forms and the JSON) and the words come from that run.

faster-whisper and av are imported only when a file is heard, so the kinds load (and their tests run) where they are not installed. The model
is read once per worker and kept; its files are cached on disk (`HF_HOME`, a volume in the container), so only the first job ever waits for
the download.

Shared with Screenplay Studio (P6): its storytranscribe imports `load_model`, `has_sound` and `MODEL` from here, so one worker keeps one
copy of a model for Studio's kinds and these. `transcribe_file` is Media's own: Studio's `transcribe` runs the model without word times and
keeps its own (a word-timed run can cut the segments differently)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import text

MODEL = os.environ.get("WHISPER_MODEL", "small")
"""tiny, base, small, medium or large-v3: bigger is more exact and slower. `small` is about 4x real time on 10 CPU cores."""
THREADS = int(os.environ.get("WHISPER_THREADS", "0"))  # 0: all cores
MIN_SECONDS, MAX_SECONDS = 1.0, 1800.0  # the design's bounds (§4): shorter has nothing to say, longer holds the one worker up too long
WORDS_ON_RECORDING = 5000  # above this the Recording's `words` stays empty and the Transcript's JSON file holds them (design §2.2)
_models: dict[str, object] = {}


def load_model(name: str):
    """The model, read once and kept (CPU, int8: no GPU needed)."""
    if name not in _models:
        from faster_whisper import WhisperModel

        _models[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=THREADS)
    return _models[name]


def has_sound(path: Path) -> bool:
    """Whether the file holds a sound stream at all (decoding one with none fails inside faster-whisper with an IndexError)."""
    import av

    with av.open(str(path)) as container:
        return bool(container.streams.audio)


def _words_of(segment) -> list[dict]:
    """A segment's words as [{text, start, end}], seconds from the file's start (the key asset `metadata.words` uses, not clip_words' `word`)."""
    return [
        {"text": str(w.word).strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
        for w in (getattr(segment, "words", None) or []) if str(w.word).strip()
    ]  # fmt: skip


def transcribe_file(model, audio: Path, out: Path, *, language: str | None = None, hotwords: str | None = None, note=lambda _t: None, every: int = 50) -> dict:
    """Run `model` over `audio` once, with word times, writing `out`.txt as the segments arrive (a failure part-way leaves what was heard),
    then the timestamped, paragraph and JSON forms. Returns {language, seconds, segments, words, text, files}; `files` is empty when nothing
    was said (no segment), and `words` is every word heard."""
    segments_iter, info = model.transcribe(
        str(audio), language=language or None, hotwords=hotwords or None, vad_filter=True, beam_size=5, word_timestamps=True,
    )  # fmt: skip
    note(f"{info.language} ({info.language_probability:.0%}), {info.duration:.0f} s of audio")
    segments: list[dict] = []
    words: list[dict] = []
    started = time.monotonic()
    partial = out.with_suffix(".txt")
    with partial.open("w", encoding="utf-8") as live:
        for s in segments_iter:
            row = {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            if not row["text"]:
                continue
            segments.append(row)
            words += _words_of(s)
            live.write(row["text"] + "\n")
            live.flush()
            if len(segments) % every == 0:
                note(f"{s.end:.0f} of {info.duration:.0f} s heard, {time.monotonic() - started:.0f} s in")
    made = {"language": str(info.language or ""), "seconds": round(float(info.duration), 1), "segments": len(segments), "words": words,
            "text": " ".join(s["text"] for s in segments).strip(), "files": {}}  # fmt: skip
    if not segments:
        partial.unlink(missing_ok=True)
        return made
    made["files"] = {
        "text": partial,
        "timestamped": out.with_name(out.name + ".timestamped.txt"),
        "paragraphs": out.with_name(out.name + ".paragraphs.txt"),
        "json": out.with_name(out.name + ".json"),
    }
    made["files"]["timestamped"].write_text(text.timestamped(segments), encoding="utf-8")
    made["files"]["paragraphs"].write_text(text.paragraphs(segments), encoding="utf-8")
    body = {"language": made["language"], "duration": info.duration, "segments": segments, "words": words}
    made["files"]["json"].write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return made


def refusal(data: dict, *, force: bool) -> "str | None":
    """Why a described Recording is not handed to the model, from its own fields (nothing downloaded yet), or None to go on. Each is an
    answer, not a failure: the job ends done with this said, and the Recording is left as it is."""
    seconds = data.get("duration_seconds")
    if data.get("status") in ("transcribed", "no-speech") and not force:
        return f"it was heard already ({data['status']}): left as it is. Ask with force to transcribe it again"
    if data.get("source") == "generated" and not force:
        return "a service made it (source generated), and a made voice has its word times already: left alone. Ask with force to transcribe it anyway"
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return "its length is not known: describe it first (media-describe)"
    if seconds < MIN_SECONDS:
        return f"it is {seconds:g} s long, under the {MIN_SECONDS:g} s a transcription needs"
    if seconds > MAX_SECONDS:
        return f"it is {seconds:g} s long, over the {MAX_SECONDS:g} s ({MAX_SECONDS / 60:g} minutes) this worker transcribes: cut it shorter, or transcribe it elsewhere"
    return None


def on_recording(words: list[dict]) -> list[dict]:
    """The words the Recording itself keeps: all of them up to the cap, none above it (the Transcript's JSON file has them then)."""
    return words if len(words) <= WORDS_ON_RECORDING else []
