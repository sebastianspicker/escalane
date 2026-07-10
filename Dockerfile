# Build stage
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ARG BUILD_ESSENTIAL_VERSION=12.*
ARG LIBPQ_DEV_VERSION=17.*

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential="${BUILD_ESSENTIAL_VERSION}" \
    libpq-dev="${LIBPQ_DEV_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies (copy source to resolve editable install)
COPY services/alarm_broker/ /build/
RUN pip install --no-cache-dir /build

# Production stage
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS production

ARG LIBPQ5_VERSION=17.*

# Install runtime library for PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5="${LIBPQ5_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r alarm && useradd -r -g alarm -s /usr/sbin/nologin alarm

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application
COPY --chown=alarm:alarm services/alarm_broker /app/services/alarm_broker

# Set ownership
RUN chown -R alarm:alarm /app

# Switch to non-root user
USER alarm

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/services/alarm_broker

WORKDIR /app/services/alarm_broker

# Expose ports
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Run command
CMD ["uvicorn", "alarm_broker.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
