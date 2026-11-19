"""What is said in a file, as the file's own `description`, so Marvin's AI index (RAG) finds it: the pure part, with no call to Marvin.

Marvin embeds an asset's name, alt text, description and tags (a library asset with a description or alt text), again on every
`asset_updated`, in chunks of 1500 characters. A Recording's words live on an entry, and entries are indexed only when published, so the
words go on the file itself: a one-line header naming the file, then the transcript with times, one segment a line, `[h:mm:ss] text`
exactly as `text.timestamped` writes it, so each chunk carries the minutes it covers (Marvin can link `?t=<seconds>` from them).

A description is the worker's own only while it is exactly what the worker wrote: `metadata.description_by` says which kind and job wrote
it and the hash of its text. A description a person wrote, or edited after the worker, is never replaced."""

from __future__ import annotations

import hashlib
import re

from . import text

MAX_CHARS = 30000
"""The longest description this worker writes. Marvin sets none (the column is an unbounded string, AssetUpdate.description has no
max_length), so this is the worker's own bound: about 1800 s (the longest file it transcribes) of brisk speech with its times. A longer
transcript is cut at a line and says where it goes on."""
MARK = "description_by"  # the asset metadata key saying the worker wrote the description
KINDS = ("media-transcribe", "media-backfill")  # the kinds that write it
STAMPED = re.compile(r"^\[(\d+:\d{2}:\d{2})\] ")
SENTENCE_END = (".", "?", "!", "…")
GAP, LINE_CHARS = 1.2, 200  # a line rebuilt from words ends at a pause this long, or before it grows past this many characters


def header(name: str) -> str:
    """The first line: which file this is, so a chunk of the index read alone says what it is from."""
    name = " ".join(str(name or "").split()) or "this file"
    return f"Speech in {name}, as text with where each part starts:"


def compose(name: str, timestamped: str, *, limit: "int | None" = None, transcript: str = "") -> tuple[str, bool]:
    """(the description, whether it was cut). The header, then the timestamped lines while they fit under `limit`; a cut is made between
    two lines, never inside one, and ends with a line saying where the speech goes on and where the whole of it is. That line does not
    start with a time, so nothing reads it as a segment."""
    limit = MAX_CHARS if limit is None else limit
    lines = [line for line in timestamped.splitlines() if line.strip()]
    head = header(name)
    out = [head]
    size = len(head)
    for n, line in enumerate(lines):
        room = 0 if n == len(lines) - 1 else 1 + len(_more(lines[n + 1], transcript))  # a line is kept only with room left to say what follows
        if size + 1 + len(line) + room > limit:
            out.append(_more(line, transcript))
            return "\n".join(out), True
        out.append(line)
        size += 1 + len(line)
    return "\n".join(out), False


def _more(first_left: str, transcript: str) -> str:
    at = STAMPED.match(first_left)
    where = f" from {at.group(1)}" if at else ""
    whole = f" on the Transcript {transcript}" if transcript else " in its Transcript's files"
    return f"(Cut here: the speech goes on{where}; the whole of it is{whole}.)"


def lines_from_words(words: list) -> "str | None":
    """The timestamped form rebuilt from the words a Recording keeps ([{text, start, end}]; `word` for `text` as Studio's clip_words has
    it): a new line after a sentence ends, at a pause of GAP seconds or more, or before a line passes LINE_CHARS. None when there are no
    words or any has no start time."""
    if not isinstance(words, list) or not words:
        return None
    segments: list[dict] = []
    current: list[str] = []
    start = end = None
    for w in words:
        said = str((w or {}).get("text") or (w or {}).get("word") or "").strip() if isinstance(w, dict) else ""
        begins = w.get("start") if isinstance(w, dict) else None
        if not isinstance(begins, (int, float)) or isinstance(begins, bool):
            return None
        if not said:
            continue
        ends = w.get("end") if isinstance(w.get("end"), (int, float)) else begins
        joined = len(" ".join(current))
        if current and (begins - end >= GAP or joined + 1 + len(said) > LINE_CHARS):
            segments.append({"start": start, "end": end, "text": " ".join(current)})
            current = []
        if not current:
            start = begins
        current.append(said)
        end = ends
        if said.endswith(SENTENCE_END):
            segments.append({"start": start, "end": end, "text": " ".join(current)})
            current = []
    if current:
        segments.append({"start": start, "end": end, "text": " ".join(current)})
    return text.timestamped(segments) if segments else None


def digest(description: str) -> str:
    """The hash `description_by` keeps of the text written, to know later whether anyone has changed it since."""
    return hashlib.sha256(str(description or "").strip().encode("utf-8")).hexdigest()[:16]


def owner(asset: dict) -> str:
    """Whose the file's description is now: `empty`, `worker` (written by this worker and unchanged since) or `person`."""
    now = str(asset.get("description") or "").strip()
    if not now:
        return "empty"
    by = (asset.get("metadataJson") or {}).get(MARK)
    if isinstance(by, dict) and by.get("kind") in KINDS and by.get("hash") == digest(now):
        return "worker"
    return "person"


def mark(kind: str, job: str, description: str) -> dict:
    """`metadata.description_by`: which kind and job wrote the description, and the hash of what it wrote."""
    return {"kind": kind, "job": job, "hash": digest(description)}
