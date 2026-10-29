# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS base
RUN python -m compileall -q /usr/local/lib/python3.12

FROM base AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv
COPY requirements.txt /build/requirements.txt
RUN uv pip install --target=/opt/packages --only-binary=:all: --compile-bytecode --no-cache -r /build/requirements.txt

FROM base AS runtime
ARG BUILD_VERSION=0.1.1
ARG BUILD_REVISION
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="aarch64" \
      org.opencontainers.image.source="https://github.com/coandaeffect/letterboxd-sync" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${BUILD_REVISION}"
ENV PYTHONPATH=/opt/packages \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=dependencies /opt/packages /opt/packages
COPY scrape.py /app/scrape.py
COPY tools/green_smoke.py tools/green_exact_smoke.py /app/tools/
COPY tests/ /app/tests/
COPY --chmod=755 apps/letterboxd-green-smoke/run.sh /app/run.sh
RUN python -m compileall -q /app
ENTRYPOINT ["/app/run.sh"]
