# Build dependencies separately so compilers and headers never enter the runtime image.
FROM python:3.14.6-slim-trixie@sha256:44dd04494ee8f3b538294360e7c4b3acb87c8268e4d0a4828a6500b1eff50061 AS builder

ARG BUILD_ESSENTIAL_VERSION=12.*
ARG LIBPQ_DEV_VERSION=17.*

WORKDIR /build

# Version ranges follow Debian patch updates while keeping the major ABI stable.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential="${BUILD_ESSENTIAL_VERSION}" \
    libpq-dev="${LIBPQ_DEV_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Build from the repository root so setuptools discovers the src package.
COPY pyproject.toml README.md LICENSE alembic.ini /build/
COPY src /build/src
COPY migrations /build/migrations
RUN pip install --no-cache-dir /build

# Start again from the minimal pinned base to reduce runtime attack surface.
FROM python:3.14.6-slim-trixie@sha256:44dd04494ee8f3b538294360e7c4b3acb87c8268e4d0a4828a6500b1eff50061 AS production

ARG LIBPQ5_VERSION=17.*

LABEL org.opencontainers.image.title="escalane" \
    org.opencontainers.image.description="Escalane public-alpha alarm intake, acknowledgement, notification, and escalation" \
    org.opencontainers.image.source="https://github.com/sebastianspicker/escalane" \
    org.opencontainers.image.licenses="MIT"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5="${LIBPQ5_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r alarm && useradd -r -g alarm -s /usr/sbin/nologin alarm

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Alembic runs from /app, while application modules come only from site-packages.
COPY --chown=alarm:alarm alembic.ini /app/
COPY --chown=alarm:alarm migrations /app/migrations

USER alarm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fail the build if a future Dockerfile change shadows the installed distribution.
RUN python -c "from pathlib import Path; import escalane; origin = Path(escalane.__file__).resolve(); assert origin.is_relative_to(Path('/opt/venv')), origin"

EXPOSE 8080

# Liveness intentionally avoids database/Redis dependencies; readiness is separate.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

CMD ["uvicorn", "escalane.web.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--no-access-log"]
