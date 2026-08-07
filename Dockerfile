# Build dependencies separately so compilers and headers never enter the runtime image.
FROM python:3.15.0b3-slim-trixie@sha256:a13b3ba393211ef88d09ca75de22e9b8412a98713f7cf8ca7274fc61d34799a6 AS builder

ARG BUILD_ESSENTIAL_VERSION=12.*
ARG LIBPQ_DEV_VERSION=17.*

WORKDIR /build

# Version ranges follow Debian patch updates while keeping the major ABI stable.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential="${BUILD_ESSENTIAL_VERSION}" \
    libpq-dev="${LIBPQ_DEV_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# A self-contained virtual environment can be copied unchanged into production.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package normally so templates and browser assets become wheel data.
COPY services/escalane/ /build/
RUN pip install --no-cache-dir /build

# Start again from the minimal pinned base to reduce runtime attack surface.
FROM python:3.15.0b3-slim-trixie@sha256:a13b3ba393211ef88d09ca75de22e9b8412a98713f7cf8ca7274fc61d34799a6 AS production

ARG LIBPQ5_VERSION=17.*

LABEL org.opencontainers.image.title="escalane" \
    org.opencontainers.image.description="Escalane public-alpha alarm intake, acknowledgement, notification, and escalation" \
    org.opencontainers.image.source="https://github.com/sebastianspicker/escalane" \
    org.opencontainers.image.licenses="MIT"

# PostgreSQL needs only the client library at runtime, not compiler headers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5="${LIBPQ5_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# The service has no reason to mutate the image or run with host-level privileges.
RUN groupadd -r alarm && useradd -r -g alarm -s /usr/sbin/nologin alarm

WORKDIR /app

# Reuse the exact dependency environment produced by the build stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Keep source metadata available for Alembic and operational introspection.
COPY --chown=alarm:alarm services/escalane /app/services/escalane
COPY --chown=alarm:alarm LICENSE /app/LICENSE

# Set ownership
RUN chown -R alarm:alarm /app

# Switch to non-root user
USER alarm

# Avoid container-local bytecode writes and flush logs directly to the runtime.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/services/escalane

WORKDIR /app/services/escalane

# Expose ports
EXPOSE 8080

# Liveness intentionally avoids database/Redis dependencies; readiness is separate.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Run command
CMD ["uvicorn", "escalane.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--no-access-log"]
