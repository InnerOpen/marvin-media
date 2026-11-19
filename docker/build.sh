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
# The kinds need marvin-worker main: a create safe to try again (#15) and the metadata.worker stamp on uploads (#16). An older base fails here,
# not on the first job.
docker run --rm --entrypoint python "marvin-media-worker:$version" -c "from marvin_worker import marvin_api as m; m.create_entry; m.working" 2>/dev/null || {
  echo "marvin-media-worker:$version is built on a marvin-worker without create_entry or the upload stamp: check out main in ../MarvinWorker (git -C ../MarvinWorker switch main && git -C ../MarvinWorker pull) and build again" >&2
  exit 1
}
echo "built marvin-media-worker:$version at $revision (also :latest)"
