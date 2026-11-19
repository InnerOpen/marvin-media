"""The mirror (design §2.3): a Recording's facts copied into the fields another package's entry has always held them in, so what reads those
fields today (Screenplay Studio's pages, Marvin's Timeline, the script export) keeps working unchanged while the Recording becomes the source.

Each map is a pure function of the Recording's fields and the entry's fields as they are now, giving {entry field: value}:
- a value is what the field should hold;
- None means the Recording does not know that fact (not heard yet, no poster, a measure that failed): the field is emptied only when the
  entry's facts came from ANOTHER Recording (`stale`), else left as it is (an entry from before Media keeps what Studio's own jobs wrote);
- a field the map does not name is never touched. No map names the field that holds the file (`clip`, `audio`), so a mirror write never sets
  off a workflow that listens for that field changing (the Studio link workflows), and never moves an entry on.

Trust (design §2.3): an entry's facts come from a Recording only when that Recording's `asset` is the very file the entry's field holds now."""

AUDIO_SOURCES = ("uploaded", "recorded")  # a Sound's own file: its length is measured. A generated one's length is what was asked for (ElevenLabs)
HEARD = ("transcribed", "no-speech")  # a Recording a transcriber has been through: its text and words are known (empty for no-speech)
REF_FIELD = "recording"  # the entry's field naming its Recording (Studio's v233 `recording`, "Recording (kept by Media)")


def trusted(rec: dict, held: str) -> bool:
    """Whether a Recording's facts may stand for the file an entry's field holds: only when the Recording is of that very file."""
    held = str(held or "").strip()
    return bool(held) and str(rec.get("asset") or "").strip() == held


def clip_words(words) -> list[dict]:
    """A Recording's words (`[{text, start, end}]`, as asset `metadata.words` and media-transcribe give them) as a beat's `clip_words`:
    `[{word, start, end}]`, seconds from the file's start to a millisecond, exactly as Studio's transcribe-clip writes them (v226). A word
    with no text is left out, as transcribe-clip leaves it out."""
    out = []
    for w in words if isinstance(words, list) else []:
        if not isinstance(w, dict):
            continue
        text = str(w.get("text", w.get("word")) or "").strip()
        if text:
            out.append({"word": text, "start": round(float(w.get("start") or 0), 3), "end": round(float(w.get("end") or 0), 3)})
    return out


def clip_text(rec: dict) -> str:
    """What was said, as a beat's `clip_text`: its words joined by a space, as transcribe-clip joins them (so a beat reads the same as before);
    the Recording's own text only when it keeps no words (over 5,000: they are in the Transcript's JSON file)."""
    words = clip_words(rec.get("words"))
    return " ".join(w["word"] for w in words) if words else str(rec.get("text") or "").strip()


def _yes_no(value) -> "str | None":
    """A Recording's `clipped` (a checkbox) as a Take's (a select of no and yes, as describe-audio writes it)."""
    return None if value is None else ("yes" if value is True else "no")


def beat_clip(rec: dict, entry: dict) -> dict:
    """A beat's clip facts (describe-video's and transcribe-clip's fields, v226). What was said is known once the Recording was heard."""
    heard = rec.get("status") in HEARD
    return {
        "clip_seconds": rec.get("duration_seconds"), "clip_width": rec.get("width"), "clip_height": rec.get("height"),
        "clip_has_audio": rec.get("has_audio"), "clip_poster": rec.get("poster") or None,
        "clip_text": clip_text(rec) if heard else None, "clip_words": clip_words(rec.get("words")) if heard else None,
    }  # fmt: skip


def take(rec: dict, entry: dict) -> dict:
    """A take's length and waveform (measure-audio's, make-waveform's) and what describe-audio heard, where the measure is the same one
    (tests/test_parity.py): its make, LUFS, loudest moment, noise floor, clipped, the silence before and after. Not `rms_db` (describe-audio's
    astats RMS, which a Recording does not keep: its `level_db` is volumedetect's mean, another measure) and not `heard` (time-words')."""
    return {
        "duration_seconds": rec.get("duration_seconds"), "waveform": rec.get("waveform"), "peak_db": rec.get("peak_db"), "format": rec.get("format"),
        "level_lufs": rec.get("level_lufs"), "noise_floor_db": rec.get("noise_floor_db"), "clipped": _yes_no(rec.get("clipped")),
        "lead_silence": rec.get("lead_silence"), "tail_silence": rec.get("tail_silence"),
    }  # fmt: skip


def sound(rec: dict, entry: dict) -> dict:
    """A sound's level, peak (measure-level's) and waveform; its length only for a sound of its own file (uploaded or recorded). A sound to
    generate keeps its length: that one is the length asked of ElevenLabs, a setting, not a fact of the file."""
    out = {"level_db": rec.get("level_db"), "peak_db": rec.get("peak_db"), "waveform": rec.get("waveform")}
    if str(entry.get("source") or "").strip() in AUDIO_SOURCES:
        out["duration_seconds"] = rec.get("duration_seconds")
    return out


def line(rec: dict, entry: dict) -> dict:
    """A speech line's length and waveform, from the file in its own audio field (the take in use's file)."""
    return {"duration_seconds": rec.get("duration_seconds"), "waveform": rec.get("waveform")}


MAPS = {"beat-clip": beat_clip, "take": take, "sound": sound, "line": line}
BY_TYPE = {"beat": "beat-clip", "take": "take", "sound": "sound", "speech-line": "line"}  # the map for an entry of this type, when none is named
FIELD = {"beat-clip": "clip"}  # the field holding the file, when none is named (`audio` otherwise)
OWNED = {name: tuple(fn({"status": "transcribed"}, {"source": "uploaded"})) for name, fn in MAPS.items()}  # every field a map can write


def field_of(name: "str | None") -> str:
    return FIELD.get(name or "", "audio")


def merged(before: dict, facts: dict, ref: dict, *, stale: bool) -> dict:
    """The entry's fields with the mirror applied: `ref` and every known fact written, an unknown one emptied when `stale`, the rest kept."""
    after = dict(before)
    after.update(ref)
    for key, value in facts.items():
        if value is not None:
            after[key] = value
        elif stale:
            after.pop(key, None)
    return after


def cleared(before: dict, name: "str | None", ref_field: str) -> dict:
    """The entry's fields once its file is gone: its Recording and every fact the map writes for this entry emptied (as v226 empties a beat's
    clip facts). A generated sound keeps its length: the map never writes it."""
    gone = {ref_field, *(MAPS[name]({}, before) if name in MAPS else ())}
    return {k: v for k, v in before.items() if k not in gone}


def _fits(value, spec: dict) -> bool:
    """Whether Marvin would take `value` in a field of this definition: a number within its min and max, a select's value among its options
    (its content validator refuses the whole write otherwise)."""
    if spec.get("type") == "number" and isinstance(value, int | float) and not isinstance(value, bool):
        low, high = spec.get("min"), spec.get("max")
        return (low is None or value >= low) and (high is None or value <= high)
    if spec.get("type") == "select" and isinstance(value, str) and isinstance(spec.get("options"), list):
        return value in [o.get("value") if isinstance(o, dict) else o for o in spec["options"]]
    return True


def within_limits(facts: dict, fields: list[dict]) -> tuple[dict, list[str]]:
    """(the facts the entry's type takes, the fields left out because the value is outside what the field allows). A fact left out is left as
    it is on the entry, never emptied: a Sound whose length was capped at 30 s in an older workspace keeps its own number."""
    specs = {f.get("key"): f for f in fields if isinstance(f, dict)}
    out = {k: v for k, v in facts.items() if v is None or k not in specs or _fits(v, specs[k])}
    return out, sorted(set(facts) - set(out))


def changed(before: dict, after: dict) -> list[str]:
    """The fields a write would change, in a fixed order: what the job says it wrote."""
    return sorted(k for k in {*before, *after} if before.get(k, _MISSING) != after.get(k, _MISSING))


_MISSING = object()
