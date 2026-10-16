# syntax=docker/dockerfile:1.7
# Backend images. Targets: api (1.2b; also runs worker-egress), worker-media (2.5), worker-core (3.2).
# Build: `make build` (docker buildx bake, see docker-bake.hcl).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS python-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app

FROM python-base AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12
# `--no-default-groups`: neither dev tools nor the `media` group (PyTorch …) in the api image.
# 1) dependencies only (cached until pyproject.toml / uv.lock change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-default-groups --no-install-project
# 2) the project itself, installed non-editable into the venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable

# The same venv plus the `core` group (Presidio, spaCy + the locked pl_core_news_lg wheel,
# the prompt-injection classifier runtime).
FROM builder AS builder-core
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group core --no-editable

# The same venv plus the `media` group (faster-whisper, Docling, CPU PyTorch on Linux).
FROM builder AS builder-media
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group media --no-editable

FROM python-base AS api
COPY --from=builder --chown=root:root /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["tini", "--", "spoldzielnia"]
CMD ["api"]

# Media worker: no HTTP port, no healthcheck endpoint (Celery). Models come from the read-only
# `models` volume. Network: `backend` only (no internet). libgl/libglib: OpenCV (RapidOCR).
FROM python-base AS worker-media
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=builder-media --chown=root:root /app/.venv /app/.venv
# A new `models` volume takes this ownership, so models-init (uid 10001) can fill it.
RUN mkdir -p /models && chown 10001:10001 /models
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    HF_HOME=/tmp/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=4
USER 10001:10001
ENTRYPOINT ["tini", "--", "spoldzielnia"]
CMD ["worker", "--queues", "media.transcribe,media.vision,media.parse"]

# Core worker: PII scrubbing, prompt-injection screening, promotion (Stage 3). No HTTP port.
# Network: `backend` only. The spaCy model is part of the image (locked wheel), the classifier
# comes from the read-only `models` volume.
FROM python-base AS worker-core
COPY --from=builder-core --chown=root:root /app/.venv /app/.venv
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    HF_HOME=/tmp/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=4
USER 10001:10001
ENTRYPOINT ["tini", "--", "spoldzielnia"]
CMD ["worker", "--queues", "core.scan,promotion"]
