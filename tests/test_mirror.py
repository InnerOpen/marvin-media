"""The mirror (design §2.3, P7): media-link copying a Recording's facts into the fields Screenplay Studio's beat, take, sound and speech line
have always held them in, so today's readers (Studio's pages, Marvin's Timeline, the script export) keep working.

The maps are pure functions, checked against Studio's own field definitions (tests/data/studio_fields_v232.json, from v232's package file):
each writes only fields its type has, of the type it has, and never the field holding the file. Then the job, against the stand-in Marvin:
only what differs is written, a job for a file since replaced writes nothing, an emptied field empties the facts, a value a field would refuse
is left out instead of refusing the whole write, and the words follow once the transcriber has heard them."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from marvin_worker import marvin_api
from marvin_worker.jobs import Context

from mediajobs import jobs, mirror, recording, transcribe
from tests.fake_marvin import FakeMarvin

STUDIO = json.loads((Path(__file__).parent / "data" / "studio_fields_v232.json").read_text(encoding="utf-8"))["types"]
FIELDS = {t: {f["key"]: f for f in fields} for t, fields in STUDIO.items()}
TYPE_OF = {"beat-clip": "beat", "take": "take", "sound": "sound", "line": "speech-line"}
JSON_TYPE = {"number": (int, float), "json": (list, dict), "boolean": (bool,), "asset": (str,), "textarea": (str,), "text": (str,), "select": (str,)}
CLIP, NEW_CLIP, AUDIO = "0c11b0a1-0000-4000-8000-000000000001", "0c22b0a2-0000-4000-8000-000000000002", "0a0d10a1-0000-4000-8000-000000000003"
WAVE = {"version": 2, "channels": 1, "sample_rate": 48000, "samples_per_pixel": 100, "bits": 8, "length": 2, "data": [-3, 4, -2, 2]}
WORDS = [{"text": "Hello,", "start": 0.5204, "end": 0.9}, {"text": "I'm", "start": 1.0, "end": 1.2}, {"text": "Jared.", "start": 1.2, "end": 1.61}]


def _video(**more) -> dict:
    return {"asset": CLIP, "kind": "video", "status": "described", "source": "recorded", "duration_seconds": 4.2, "width": 1080, "height": 1920,
            "fps": 30, "has_audio": True, "poster": "poster-rec", **more}  # fmt: skip


def _sound(**more) -> dict:
    return {"asset": AUDIO, "kind": "audio", "status": "described", "source": "uploaded", "duration_seconds": 3.25, "format": "opus · 48 kHz · mono",
            "level_lufs": -19.5, "level_db": -24.1, "peak_db": -3.2, "noise_floor_db": -58.0, "clipped": False, "lead_silence": 0.2,
            "tail_silence": 0.4, "waveform": WAVE, "has_audio": True, **more}  # fmt: skip


# ── the maps ─────────────────────────────────────────────────────────────────
def test_clip_words_are_studios_word_start_end_shape_not_the_recordings_text_key():
    """Pinned to what reads and writes `clip_words` today: Studio's transcribe-clip writes [{word, start, end}] to a millisecond (v226,
    transcribe/tests/test_transcribe_clip.py), test_unchanged_pull's beat holds that shape, the render never reads it, and Marvin's Timeline
    names it in ClipSpec without drawing it yet."""
    assert mirror.clip_words(WORDS) == [{"word": "Hello,", "start": 0.52, "end": 0.9}, {"word": "I'm", "start": 1.0, "end": 1.2}, {"word": "Jared.", "start": 1.2, "end": 1.61}]
    assert all(list(w) == ["word", "start", "end"] for w in mirror.clip_words(WORDS))
    assert mirror.clip_words([{"text": "  ", "start": 0, "end": 1}, "x", None]) == [] and mirror.clip_words(None) == []
    assert mirror.clip_text({"words": WORDS, "text": "Hello, I'm Jared."}) == "Hello, I'm Jared."  # joined as transcribe-clip joins them
    assert mirror.clip_text({"words": [], "text": " a long talk "}) == "a long talk"  # over 5,000 words: no words kept on the Recording


def test_a_beat_gets_its_clip_facts_and_what_was_said_only_once_heard():
    described = mirror.beat_clip(_video(), {})
    assert described == {"clip_seconds": 4.2, "clip_width": 1080, "clip_height": 1920, "clip_has_audio": True, "clip_poster": "poster-rec", "clip_text": None, "clip_words": None}
    heard = mirror.beat_clip(_video(status="transcribed", text="Hello, I'm Jared.", words=WORDS), {})
    assert heard["clip_text"] == "Hello, I'm Jared." and heard["clip_words"][0] == {"word": "Hello,", "start": 0.52, "end": 0.9}
    assert {k: v for k, v in mirror.beat_clip(_video(status="no-speech", text="", words=[]), {}).items() if k.startswith("clip_t") or k.endswith("words")} == {"clip_text": "", "clip_words": []}
    assert mirror.beat_clip(_video(poster=""), {})["clip_poster"] is None


def test_a_take_gets_its_length_waveform_and_what_describe_audio_measures_the_same_way():
    out = mirror.take(_sound(clipped=True), {})
    assert out == {"duration_seconds": 3.25, "waveform": WAVE, "peak_db": -3.2, "format": "opus · 48 kHz · mono", "level_lufs": -19.5,
                   "noise_floor_db": -58.0, "clipped": "yes", "lead_silence": 0.2, "tail_silence": 0.4}  # fmt: skip
    assert mirror.take(_sound(), {})["clipped"] == "no" and mirror.take({}, {})["clipped"] is None
    assert "rms_db" not in out and "heard" not in out and "level_db" not in out  # another measure (astats RMS), and time-words' own


def test_a_sound_gets_its_length_only_when_it_is_its_own_file():
    assert mirror.sound(_sound(), {"source": "uploaded"}) == {"level_db": -24.1, "peak_db": -3.2, "waveform": WAVE, "duration_seconds": 3.25}
    assert mirror.sound(_sound(), {"source": "recorded"})["duration_seconds"] == 3.25
    for entry in ({"source": "generated"}, {}, {"source": ""}):  # the length asked of ElevenLabs stays the sound's own (`ready.py` reads it)
        assert "duration_seconds" not in mirror.sound(_sound(), entry)


def test_a_line_gets_its_length_and_waveform():
    assert mirror.line(_sound(), {}) == {"duration_seconds": 3.25, "waveform": WAVE}


@pytest.mark.parametrize("name", list(mirror.MAPS))
def test_every_map_writes_only_fields_its_studio_type_has_of_the_type_it_has_and_never_the_file(name):
    fields = FIELDS[TYPE_OF[name]]
    full = {**_video(), **_sound(), "status": "transcribed", "text": "hi", "words": WORDS, "clipped": True, "asset": CLIP}
    out = mirror.MAPS[name](full, {"source": "uploaded"})
    assert set(out) == set(mirror.OWNED[name]) and out
    for key, value in out.items():
        assert key in fields, f"{name}: {key} is not a {TYPE_OF[name]} field"
        assert isinstance(value, JSON_TYPE[fields[key]["type"]]), f"{name}: {key}={value!r} for a {fields[key]['type']} field"
        if fields[key]["type"] == "select":
            assert value in fields[key]["options"]
    assert mirror.field_of(name) in fields and mirror.field_of(name) not in out and fields[mirror.field_of(name)]["type"] == "asset"
    assert not {"clip", "audio", "status", "source", "recording", "line", "story"} & set(out)  # what workflows listen to is never written


def test_studios_field_names_for_each_map_are_the_designs():
    assert mirror.OWNED["beat-clip"] == ("clip_seconds", "clip_width", "clip_height", "clip_has_audio", "clip_poster", "clip_text", "clip_words")
    assert set(mirror.OWNED["line"]) == {"duration_seconds", "waveform"} and {"duration_seconds", "waveform", "peak_db"} <= set(mirror.OWNED["take"])
    assert set(mirror.OWNED["sound"]) == {"duration_seconds", "level_db", "peak_db", "waveform"}
    assert mirror.BY_TYPE == {"beat": "beat-clip", "take": "take", "sound": "sound", "speech-line": "line"}
    assert [mirror.field_of(n) for n in mirror.MAPS] == ["clip", "audio", "audio", "audio"]


def test_trust_only_a_recording_of_the_very_file_the_field_holds():
    assert mirror.trusted({"asset": CLIP}, CLIP) and mirror.trusted({"asset": CLIP}, f" {CLIP} ")
    assert not mirror.trusted({"asset": CLIP}, NEW_CLIP) and not mirror.trusted({"asset": ""}, "") and not mirror.trusted({}, CLIP)


def test_merging_writes_known_facts_empties_unknown_ones_only_when_they_came_from_another_recording():
    before = {"clip": CLIP, "clip_text": "old words", "clip_seconds": 9.0, "clip_look": "pip"}
    facts = {"clip_seconds": 4.2, "clip_text": None}
    kept = mirror.merged(before, facts, {"recording": "rec-a"}, stale=False)
    assert kept == {"clip": CLIP, "clip_text": "old words", "clip_seconds": 4.2, "clip_look": "pip", "recording": "rec-a"}
    assert "clip_text" not in mirror.merged(before, facts, {"recording": "rec-a"}, stale=True)
    assert mirror.changed(before, kept) == ["clip_seconds", "recording"] and mirror.changed(kept, kept) == []


def test_clearing_empties_the_maps_facts_and_the_recording_and_keeps_settings_and_a_generated_sounds_length():
    beat = {"clip": "", "clip_look": "pip", "clip_start": 1.0, "recording": "rec-a", "clip_seconds": 3.0, "clip_words": [], "clip_text": "hi"}
    assert mirror.cleared(beat, "beat-clip", "recording") == {"clip": "", "clip_look": "pip", "clip_start": 1.0}
    made = {"audio": "", "source": "generated", "duration_seconds": 12, "level_db": -20.0, "waveform": WAVE, "recording": "rec-b"}
    assert mirror.cleared(made, "sound", "recording") == {"audio": "", "source": "generated", "duration_seconds": 12}
    assert mirror.cleared(made, None, "recording") == {k: v for k, v in made.items() if k != "recording"}


def test_a_value_a_field_would_refuse_is_left_out_not_the_whole_write():
    facts = {"level_db": -70.0, "peak_db": -3.0, "duration_seconds": 72.0, "waveform": WAVE, "clipped": "maybe", "clip_text": None}
    fields = [*STUDIO["sound"], {"key": "duration_seconds", "type": "number", "min": 0, "max": 30}, {"key": "clipped", "type": "select", "options": ["no", "yes"]}]
    fields = [f for f in fields if not (f["key"] == "duration_seconds" and f.get("max") == 3600)]  # an older workspace's 30 s cap
    kept, out = mirror.within_limits(facts, fields)
    assert out == ["clipped", "duration_seconds", "level_db"] and kept == {"peak_db": -3.0, "waveform": WAVE, "clip_text": None}


@pytest.mark.parametrize(("value", "entry_type", "name"), [
    (None, "beat", "beat-clip"), (True, "take", "take"), ("yes", "sound", "sound"), ("", "speech-line", "line"), ("true", "story", None),
    ("beat-clip", "story", "beat-clip"), ("Line", "beat", "line"), (False, "beat", None), ("no", "beat", None), ("none", "take", None), ("off", "sound", None),
])  # fmt: skip
def test_the_mirror_argument_is_a_maps_name_yes_by_the_entrys_type_or_no(value, entry_type, name):
    assert jobs.mirror_name(value, entry_type) == name


def test_an_unknown_mirror_is_refused():
    with pytest.raises(jobs.JobError, match="beat-clip, take, sound or line|is not one of"):
        jobs.mirror_name("beats", "beat")


# ── the job ──────────────────────────────────────────────────────────────────
@pytest.fixture
def studio(monkeypatch, tmp_path):
    """The stand-in Marvin with Studio's four types (their v232 fields) and files: a beat's clip, a new clip, a sound's file; media-describe
    stood in for (it writes `facts[asset]` and `described`, as the real one's last write does) and the calls it got."""
    types = {"job": "type-job", "recording": "type-recording", "transcript": "type-transcript", "beat": "type-beat", "take": "type-take", "sound": "type-sound", "speech-line": "type-line"}
    fake = FakeMarvin(types=types, fields=STUDIO).wire(monkeypatch)
    for asset_id, mime, name in ((CLIP, "video/webm", "Clip one"), (NEW_CLIP, "video/mp4", "Clip two"), (AUDIO, "audio/webm", "Door slam")):
        (tmp_path / name).write_bytes(b"x")
        fake.add_asset(tmp_path / name, mime=mime, asset_id=asset_id, name=name)
    fake.add_asset(None, mime="image/jpeg", asset_id="poster-v226", meta={"kind": "clip-poster", "beat": "b04", "clip": CLIP})
    fake.add_asset(None, mime="image/jpeg", asset_id="poster-rec", meta={"kind": recording.POSTER_KIND, "video": CLIP})
    fake.facts = {CLIP: _video(), NEW_CLIP: {**_video(asset=NEW_CLIP, poster="", duration_seconds=2.0, width=640, height=480)}, AUDIO: _sound()}
    fake.described = []

    def fake_describe(job: dict, ctx: Context) -> dict:
        slug = job["args"]["recording"]
        fake.described.append(slug)
        data = fake.entry(slug)["dataJson"]
        jobs.write_entry(ctx, slug, {k: v for k, v in fake.facts[data["asset"]].items() if k != "asset"})
        return {"result": {"recording": slug}}

    monkeypatch.setattr(jobs, "media_describe", fake_describe)
    return fake


def link(args: dict, tmp_path, job: str = "link-1") -> dict:
    (tmp_path / job).mkdir(exist_ok=True)
    return jobs.media_link({"job": job, "kind": jobs.LINK, "args": args}, Context(creds=("http://marvin.test", "t"), work=tmp_path / job, name=job))


def _beat(fake, **data) -> dict:
    return fake.add_entry("beat", "b04", {"story": "s", "number": "04", "clip": CLIP, "clip_look": "pip", **data})


def _patched(fake) -> list[str]:
    return [p for m, p in fake.writes if m == "PATCH" and "/entries/" in p]


def test_a_beats_clip_gets_its_recording_made_described_named_and_mirrored_and_again_writes_nothing(studio, tmp_path):
    beat = _beat(studio)
    out = link({"entry": "b04", "field": "clip", "asset": CLIP, "mirror": "beat-clip"}, tmp_path)["result"]
    rec = recording.slug_for(CLIP)
    assert out["recording"] == rec and out["created"] is True and studio.described == [rec]
    assert studio.entry("b04")["dataJson"] == {"story": "s", "number": "04", "clip": CLIP, "clip_look": "pip", "recording": rec, "clip_seconds": 4.2,
                                               "clip_width": 1080, "clip_height": 1920, "clip_has_audio": True, "clip_poster": "poster-rec"}  # fmt: skip
    assert out["wrote"] == ["clip_has_audio", "clip_height", "clip_poster", "clip_seconds", "clip_width", "recording"]
    assert len([p for p in _patched(studio) if beat["id"] in p]) == 1  # one write to the beat, never the clip itself
    studio.writes.clear()
    again = link({"entry": "b04", "field": "clip", "asset": CLIP, "mirror": "beat-clip"}, tmp_path, job="link-2")["result"]
    assert again["wrote"] == [] and studio.writes == [] and studio.described == [rec]  # described already: not again, and nothing written


def test_by_default_the_map_and_the_field_come_from_the_entrys_type(studio, tmp_path):
    _beat(studio)
    out = link({"entry": "b04"}, tmp_path)["result"]
    assert out["recording"] == recording.slug_for(CLIP) and studio.entry("b04")["dataJson"]["clip_seconds"] == 4.2


def test_a_job_for_a_clip_since_replaced_writes_nothing(studio, tmp_path):
    _beat(studio, clip=NEW_CLIP)
    out = link({"entry": "b04", "field": "clip", "asset": CLIP, "mirror": "beat-clip"}, tmp_path)["result"]
    assert out["skipped"] is True and studio.writes == [] and studio.live() == []


def test_a_new_clip_replaces_the_facts_and_empties_what_the_last_one_left_and_studios_old_poster_goes(studio, tmp_path):
    """The beat was mirrored from clip one, heard (with its words), then the clip changed: clip two has no poster and is not heard yet."""
    studio.facts[CLIP] = _video(status="transcribed", text="Hello, I'm Jared.", words=WORDS)
    _beat(studio, clip_poster="poster-v226")
    link({"entry": "b04", "asset": CLIP}, tmp_path)
    beat = studio.entry("b04")["dataJson"]
    assert beat["clip_text"] == "Hello, I'm Jared." and beat["clip_words"][2] == {"word": "Jared.", "start": 1.2, "end": 1.61}
    assert studio.assets["poster-v226"]["trashedAt"] and not studio.assets["poster-rec"]["trashedAt"]  # v226's poster for this beat goes
    beat["clip"] = NEW_CLIP
    out = link({"entry": "b04", "asset": NEW_CLIP}, tmp_path, job="link-2")["result"]
    beat = studio.entry("b04")["dataJson"]
    assert out["recording"] == recording.slug_for(NEW_CLIP) and beat["recording"] == recording.slug_for(NEW_CLIP)
    assert (beat["clip_seconds"], beat["clip_width"], beat["clip_height"]) == (2.0, 640, 480)
    assert not {"clip_poster", "clip_text", "clip_words"} & set(beat)  # clip one's, emptied
    assert not studio.assets["poster-rec"]["trashedAt"]  # clip one's Recording keeps its poster


def test_a_beat_from_before_media_keeps_what_studio_heard_until_the_recording_is_heard(studio, tmp_path):
    _beat(studio, clip_text="Hello, I'm Jared.", clip_words=[{"word": "Hello,", "start": 0.52, "end": 0.9}], clip_seconds=4.2)
    link({"entry": "b04", "asset": CLIP}, tmp_path)
    beat = studio.entry("b04")["dataJson"]
    assert beat["clip_text"] == "Hello, I'm Jared." and beat["clip_words"] == [{"word": "Hello,", "start": 0.52, "end": 0.9}]


def test_an_emptied_clip_empties_the_facts_and_the_recording_and_keeps_the_settings(studio, tmp_path):
    rec = recording.slug_for(CLIP)
    _beat(studio, clip="", recording=rec, clip_seconds=4.2, clip_poster="poster-v226", clip_text="hi", clip_words=[], clip_start=1.0)
    out = link({"entry": "b04", "field": "clip", "asset": None, "mirror": "beat-clip"}, tmp_path)["result"]
    assert studio.entry("b04")["dataJson"] == {"story": "s", "number": "04", "clip": "", "clip_look": "pip", "clip_start": 1.0}
    assert out["recording"] is None and "recording" in out["wrote"] and studio.assets["poster-v226"]["trashedAt"]
    studio.writes.clear()
    link({"entry": "b04", "field": "clip", "asset": "", "mirror": "beat-clip"}, tmp_path, job="link-2")
    assert studio.writes == []


def test_with_the_mirror_off_only_the_recording_is_named(studio, tmp_path):
    _beat(studio)
    out = link({"entry": "b04", "asset": CLIP, "mirror": False}, tmp_path)["result"]
    assert out["wrote"] == ["recording"] and "clip_seconds" not in studio.entry("b04")["dataJson"]
    assert link({"entry": "b04", "asset": CLIP, "mirror": "none"}, tmp_path, job="link-2")["result"]["wrote"] == []


def test_a_sound_gets_levels_and_its_own_files_length_and_a_level_its_field_refuses_is_left_out(studio, tmp_path):
    studio.facts[AUDIO] = _sound(level_db=-70.0)
    studio.add_entry("sound", "door", {"audio": AUDIO, "source": "uploaded", "level_db": -30.0})
    out = link({"entry": "door", "field": "audio", "asset": AUDIO, "mirror": "sound"}, tmp_path)
    data = studio.entry("door")["dataJson"]
    assert data == {"audio": AUDIO, "source": "uploaded", "level_db": -30.0, "peak_db": -3.2, "waveform": WAVE, "duration_seconds": 3.25, "recording": recording.slug_for(AUDIO)}
    assert out["result"]["left_out"] == ["level_db"]  # -70 is under the field's -60: the sound keeps its own, the rest is written


def test_a_generated_sound_keeps_the_length_asked_of_elevenlabs(studio, tmp_path):
    studio.add_entry("sound", "rain", {"audio": AUDIO, "source": "generated", "duration_seconds": 12})
    link({"entry": "rain", "asset": AUDIO}, tmp_path)
    assert studio.entry("rain")["dataJson"]["duration_seconds"] == 12 and studio.entry("rain")["dataJson"]["peak_db"] == -3.2


def test_a_take_and_a_line_get_their_facts_in_studios_shapes(studio, tmp_path):
    studio.facts[AUDIO] = _sound(clipped=True)
    studio.add_entry("take", "t1", {"line": "l1", "audio": AUDIO, "status": "in_use", "rms_db": -22.0, "heard": "hello"})
    studio.add_entry("speech-line", "l1", {"story": "s", "audio": AUDIO, "duration_seconds": 3.0})
    link({"entry": "t1", "asset": AUDIO}, tmp_path)
    link({"entry": "l1", "asset": AUDIO}, tmp_path, job="link-2")
    take, line = studio.entry("t1")["dataJson"], studio.entry("l1")["dataJson"]
    assert take["clipped"] == "yes" and take["duration_seconds"] == 3.25 and take["rms_db"] == -22.0 and take["heard"] == "hello"
    assert line == {"story": "s", "audio": AUDIO, "duration_seconds": 3.25, "waveform": WAVE, "recording": recording.slug_for(AUDIO)}
    assert studio.described == [recording.slug_for(AUDIO)]  # one Recording for the one file, described once


def test_a_field_holding_a_slug_not_an_id_is_refused(studio, tmp_path):
    studio.add_entry("sound", "door", {"audio": "door-slam"})
    with pytest.raises(jobs.JobError, match="not a file's id|no file"):
        link({"entry": "door", "asset": "door-slam"}, tmp_path)


# ── the words follow the transcriber ─────────────────────────────────────────
def _whisper(monkeypatch):
    words = [NS(word=" Hello,", start=0.5204, end=0.9), NS(word=" Jared.", start=1.0, end=1.61)]
    model = NS(transcribe=lambda path, **kw: (iter([NS(text="Hello, Jared.", start=0.5, end=1.61, words=words)]), NS(language="en", language_probability=0.9, duration=4.2)))
    monkeypatch.setattr(transcribe, "load_model", lambda name: model)
    monkeypatch.setattr(transcribe, "has_sound", lambda path: True)


def _transcribe(args: dict, tmp_path, job: str) -> dict:
    (tmp_path / job).mkdir(exist_ok=True)
    with marvin_api.working(jobs.TRANSCRIBE, job):
        return jobs.media_transcribe({"job": job, "kind": jobs.TRANSCRIBE, "args": args}, Context(creds=("http://marvin.test", "t"), work=tmp_path / job, name=job))


def test_with_transcribe_the_words_reach_the_beat_once_heard_and_again_when_heard_already(studio, monkeypatch, tmp_path):
    _whisper(monkeypatch)
    _beat(studio)
    out = link({"entry": "b04", "field": "clip", "asset": CLIP, "mirror": "beat-clip", "transcribe": True}, tmp_path)["result"]
    (queued,) = studio.live("job")
    assert out["transcribe_job"] == queued["slug"] == "link-1-hear"
    args = queued["dataJson"]["args"]
    assert queued["dataJson"]["kind"] == "media-transcribe" and args == {"recording": recording.slug_for(CLIP), "entry": "b04", "field": "clip", "mirror": "beat-clip", "ref_field": "recording"}
    heard = _transcribe(args, tmp_path, "link-1-hear")
    beat = studio.entry("b04")["dataJson"]
    assert beat["clip_text"] == "Hello, Jared." and beat["clip_words"] == [{"word": "Hello,", "start": 0.52, "end": 0.9}, {"word": "Jared.", "start": 1.0, "end": 1.61}]
    assert heard["result"]["entry"]["wrote"] == ["clip_text", "clip_words"]
    beat["clip_text"], beat["clip_words"] = "edited", []
    again = _transcribe(args, tmp_path, "hear-2")  # heard already (or by Media's own workflow first): skipped, and the beat still gets the words
    assert again["result"]["skipped"] is True and studio.entry("b04")["dataJson"]["clip_text"] == "Hello, Jared."
    studio.writes.clear()
    link({"entry": "b04", "asset": CLIP, "mirror": "beat-clip", "transcribe": True}, tmp_path, job="link-3")
    assert studio.writes == [] and len(studio.live("job")) == 1  # heard: nothing to queue, nothing to write


def test_the_words_never_reach_a_beat_whose_clip_changed_while_they_were_heard(studio, monkeypatch, tmp_path):
    _whisper(monkeypatch)
    _beat(studio)
    link({"entry": "b04", "asset": CLIP, "transcribe": "yes"}, tmp_path)
    (queued,) = studio.live("job")
    studio.entry("b04")["dataJson"]["clip"] = NEW_CLIP
    out = _transcribe(queued["dataJson"]["args"], tmp_path, "hear")
    assert out["result"]["entry"]["skipped"] is True and "clip_text" not in studio.entry("b04")["dataJson"]


def test_nothing_is_queued_for_a_clip_with_no_sound_or_a_generated_file(studio, tmp_path):
    studio.facts[CLIP] = _video(has_audio=False)
    _beat(studio)
    out = link({"entry": "b04", "asset": CLIP, "transcribe": True}, tmp_path)["result"]
    assert "no sound" in out["words"] and studio.live("job") == []
    studio.add_entry("sound", "rain", {"audio": AUDIO, "source": "generated"})
    studio.facts[AUDIO] = _sound(source="generated")
    assert "made by a service" in link({"entry": "rain", "asset": AUDIO, "transcribe": True}, tmp_path, job="link-2")["result"]["words"]
    assert studio.live("job") == []
