#!/usr/bin/env bash

# Exercise migrations and readiness against the exact image that would be published.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <image>" >&2
  exit 2
fi

image="$1"
run_suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$"
network="escalane-smoke-${run_suffix}"
postgres_container="${network}-postgres"
redis_container="${network}-redis"
api_container="${network}-api"
worker_container="${network}-worker"
worker_job_id="container-smoke-recovery-${run_suffix}"
postgres_image="${POSTGRES_IMAGE:-postgres:16}"
redis_image="${REDIS_IMAGE:-redis:7}"
admin_api_key="$(openssl rand -hex 32)"
postgres_password="$(openssl rand -hex 32)"
database_url="postgresql+asyncpg://alarm:${postgres_password}@${postgres_container}:5432/alarm"

# Always remove ephemeral resources; retain logs only when a gate fails.
cleanup() {
  status=$?
  trap - EXIT

  if [[ $status -ne 0 ]]; then
    docker logs "$api_container" 2>/dev/null || true
    docker logs "$worker_container" 2>/dev/null || true
    docker logs "$postgres_container" 2>/dev/null || true
    docker logs "$redis_container" 2>/dev/null || true
  fi

  docker rm --force \
    "$api_container" \
    "$worker_container" \
    "$postgres_container" \
    "$redis_container" \
    >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

# Isolate dependencies on a unique network so concurrent CI runs cannot collide.
docker image inspect "$image" >/dev/null
docker network create "$network" >/dev/null

docker run --detach \
  --name "$postgres_container" \
  --network "$network" \
  --env POSTGRES_DB=alarm \
  --env POSTGRES_USER=alarm \
  --env POSTGRES_PASSWORD="$postgres_password" \
  "$postgres_image" \
  >/dev/null

docker run --detach \
  --name "$redis_container" \
  --network "$network" \
  "$redis_image" \
  redis-server \
  --appendonly yes \
  --appendfsync everysec \
  --maxmemory 128mb \
  --maxmemory-policy noeviction \
  >/dev/null

postgres_ready=false
# Bound readiness waits so infrastructure failures terminate deterministically.
for _attempt in $(seq 1 30); do
  if docker exec "$postgres_container" pg_isready -U alarm -d alarm >/dev/null; then
    postgres_ready=true
    break
  fi
  sleep 2
done
if [[ "$postgres_ready" != true ]]; then
  echo "PostgreSQL did not become ready for the container smoke test." >&2
  exit 1
fi

redis_ready=false
for _attempt in $(seq 1 30); do
  if docker exec "$redis_container" redis-cli ping 2>/dev/null | grep -qx PONG; then
    redis_ready=true
    break
  fi
  sleep 2
done
if [[ "$redis_ready" != true ]]; then
  echo "Redis did not become ready for the container smoke test." >&2
  exit 1
fi

docker run --rm \
  --network "$network" \
  --env DATABASE_URL="$database_url" \
  --env REDIS_URL="redis://${redis_container}:6379/0" \
  --env ADMIN_API_KEY="$admin_api_key" \
  --env YELK_IP_ALLOWLIST="127.0.0.1/32" \
  "$image" \
  alembic upgrade head

# Start the real ARQ entrypoint in simulation mode, then enqueue its harmless
# recovery task and wait for the persisted ARQ result. This proves startup,
# Redis consumption, database access, task registration, and completion.
docker run --detach \
  --name "$worker_container" \
  --network "$network" \
  --env DATABASE_URL="$database_url" \
  --env REDIS_URL="redis://${redis_container}:6379/0" \
  --env ADMIN_API_KEY="$admin_api_key" \
  --env BASE_URL="http://127.0.0.1:8080" \
  --env SIMULATION_ENABLED=true \
  "$image" \
  arq escalane.worker.settings.WorkerSettings \
  >/dev/null

docker run --rm --interactive \
  --network "$network" \
  --env REDIS_URL="redis://${redis_container}:6379/0" \
  --env WORKER_JOB_ID="$worker_job_id" \
  "$image" \
  python - <<'PY'
import asyncio
import os

from arq.connections import RedisSettings, create_pool


async def main() -> None:
    redis = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    try:
        job = await redis.enqueue_job(
            "recover_incomplete_alarm_events",
            _job_id=os.environ["WORKER_JOB_ID"],
        )
        if job is None:
            raise RuntimeError("ARQ recovery smoke job was not enqueued")
        await job.result(timeout=30)
        print(f"ARQ recovery job completed: {job.job_id}")
    finally:
        await redis.aclose()


asyncio.run(main())
PY

if [[ "$(docker inspect --format '{{.State.Running}}' "$worker_container")" != true ]]; then
  echo "The ARQ worker exited after processing the recovery smoke job." >&2
  exit 1
fi

# Run the same image after migration and bind an ephemeral loopback port for probing.
docker run --detach \
  --name "$api_container" \
  --network "$network" \
  --publish "127.0.0.1::8080" \
  --env DATABASE_URL="$database_url" \
  --env REDIS_URL="redis://${redis_container}:6379/0" \
  --env ADMIN_API_KEY="$admin_api_key" \
  --env BASE_URL="http://127.0.0.1:8080" \
  --env YELK_IP_ALLOWLIST="127.0.0.1/32" \
  "$image" \
  >/dev/null

api_ready=false
for _attempt in $(seq 1 30); do
  binding="$(docker port "$api_container" 8080/tcp 2>/dev/null | awk 'NR == 1 {print $1}')"
  host_port="${binding##*:}"
  if [[ -n "$binding" ]] && curl --fail --silent \
    "http://127.0.0.1:${host_port}/readyz" \
    >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
if [[ "$api_ready" != true ]]; then
  echo "The migrated application image did not become ready." >&2
  exit 1
fi

echo "Container smoke passed for ${image}."
