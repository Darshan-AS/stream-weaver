# syntax=docker/dockerfile:1
# ==============================================================================
# Stream Weaver (`sw`) — Multi-Stage Python 3.13 Container
#
# Default mode: Stateless Browser Studio on port 8787 (zero server persistence;
# tenant bundle & secrets stay in browser localStorage).
#
# Local GitOps mode: mount a tenant directory to `/workspace` and override CMD:
#   docker run --rm -p 8787:8787 \
#     -v "$(pwd)/tenants/main:/workspace" \
#     stream-weaver studio --dir /workspace --host 0.0.0.0 --port 8787 --no-open
# ==============================================================================

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY library ./library
COPY examples/home ./examples/home

RUN uv build --no-config --wheel --out-dir /dist


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 sw \
    && useradd --uid 1000 --gid sw --create-home --shell /bin/sh sw \
    && mkdir -p /workspace \
    && chown -R sw:sw /workspace

COPY --from=builder /dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

WORKDIR /workspace
USER sw

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/', timeout=3)"]

ENTRYPOINT ["sw"]
CMD ["studio", "--stateless", "--host", "0.0.0.0", "--port", "8787", "--no-open"]
