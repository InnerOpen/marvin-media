#!/usr/bin/env bash
# Build the Media worker image, tagged with the package version it goes with (docs/media.marvin-package.json: version 1 -> m1).
# The generic base is built first from ../MarvinWorker (a checkout of InnerOpen/marvin-worker next to this one), as Studio's build does;
# MEDIA_SKIP_BASE=1 uses the marvin-worker-base already on this machine instead.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
version="m$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$here/docs/media.marvin-package.json")"
if [ "${MEDIA_SKIP_BASE:-}" != "1" ]; then
  docker build -q -f "$here/../MarvinWorker/docker/Dockerfile" -t marvin-worker-base "$here/../MarvinWorker" >/dev/null
fi
revision="$(git -C "$here" rev-parse --short HEAD)$(git -C "$here" diff --quiet HEAD -- . 2>/dev/null || echo -dirty)"
docker build -f "$here/docker/Dockerfile" --build-arg MEDIA_VERSION="$version" --build-arg MEDIA_REVISION="$revision" -t "marvin-media-worker:$version" -t marvin-media-worker:latest "$here"
echo "built marvin-media-worker:$version at $revision (also :latest)"
