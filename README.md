# Marvin Media

A package for [Marvin](https://github.com/InnerOpen/marvin) that gives every audio and video file a **Recording** entry: what the file says
about itself (its length, its make, for a video its picture's size, frame rate and a poster frame, how loud it is, its waveform) measured by a
worker running on the generic [Marvin worker](https://github.com/InnerOpen/marvin-worker). Being built: the package file and the worker kinds
arrive in the next changes.

## Development

```bash
uv run --python 3.12 --extra dev --with-editable ../MarvinWorker python -m pytest -q    # a checkout of marvin-worker next to this one
uv run --python 3.12 --extra dev ruff check .
```

Changes go through pull requests with CI green.
