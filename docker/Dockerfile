# The Media worker: the generic Marvin worker (`marvin-worker-base`, built from InnerOpen/marvin-worker) plus ffmpeg and the `mediajobs`
# kinds the Media package (docs/media.marvin-package.json) hands to a worker. m1: media-describe. The image is tagged with the package
# version it goes with (m1, m2...). Build with docker/build.sh (the build context is the repository root).
#
# The chain (design §6): marvin-worker-base -> marvin-media-worker:mN -> (from P6) screenplay-studio-worker:vNNN, which builds FROM this one.
# BASE names the base image, so a check can build against a base of its own without retagging the shared `marvin-worker-base`.
ARG BASE=marvin-worker-base
FROM ${BASE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY mediajobs ./mediajobs

ARG MEDIA_VERSION=dev
ARG MEDIA_REVISION=unknown
LABEL org.opencontainers.image.title="marvin-media-worker" org.opencontainers.image.version="${MEDIA_VERSION}"
USER 1001

# The base reads MARVIN_API_URL and MARVIN_USER_TOKEN (a token pinned to ONE workspace), and WORKER_NAME, WORKER_KINDS, WORKER_POLL_SECONDS,
# from the environment (docker/compose.yml passes them from an env file outside the repository). WORKER_IMAGE, WORKER_VERSION and
# WORKER_REVISION say which image this is, for the heartbeat Marvin's Workers page shows.
ENV WORKER_JOB_MODULES=mediajobs.jobs \
    MEDIA_VERSION=${MEDIA_VERSION} \
    WORKER_IMAGE=marvin-media-worker WORKER_VERSION=${MEDIA_VERSION} WORKER_REVISION=${MEDIA_REVISION}
