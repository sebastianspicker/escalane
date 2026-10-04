# Performance validation

This page records the performance checks for changes to recovery, delivery,
the dashboard, pagination, and observability that arrived with migration
`0008`. Maintainers can use it to decide whether a release candidate is ready
for tests against real services.

The measurements were taken on 7 September 2026 on macOS arm64. The test
environment used CPython 3.14.7, SQLAlchemy 2.0.52, SQLite 3.53.4, temporary
databases, synthetic providers, and a synthetic queue that accepted jobs. The
numbers describe those fixtures only. They say nothing about PostgreSQL query
plans, Redis behavior, deployment capacity, or general latency improvements.

## What changed

- Recovery removes terminal acknowledgement deliveries before it applies the
  candidate limit, so old completed work cannot displace pending work.
- Notification delivery records use an indexed logical delivery key. A
  compatibility lookup still handles rows written before migration `0008`.
- Once a provider permanently rejects a request, retries keep that result
  terminal.
- Outbox recovery commits configurable short batches while preserving stable
  ARQ job identities.
- A pool reuses webhook clients for as many as 64 origins. DNS validation and
  address pinning still run for every delivery.
- Dashboard counts use a durable database revision and cache each revision in
  Redis for 60 seconds.
- Configuration lists and alarm history use bounded keyset pagination, with 50
  records on a page by default.
- Metrics restrict route and method labels, use fixed latency buckets, report
  current outbox gauges, and expire observations from the latest reporting
  worker.
- Local development, CI, wheel builds, and container builds install
  dependencies from the checked-in CPython 3.14 constraint files.

Migration `0008` did not add optional production indexes or a search extension.
The PostgreSQL index benchmark creates each candidate temporarily and leaves the
migration history unchanged.

## Measurement method

Each benchmark ran twice to warm up and then recorded five repetitions. The
tables report the median and nearest-rank p95 in milliseconds. With five
samples, that p95 is the largest measured value.

`benchmark_optimizations.py` runs the previous aggregate dashboard revision
query and the durable singleton query against the same synthetic datasets. Its
"five reads" workload performs five polling reads one after another. It does
not simulate five operators working concurrently.

`benchmark_delivery.py` sends notifications serially to 1, 5, and 20 synthetic
targets. The provider waits either 0 or 5 milliseconds. Every run also checks
the delivery records, retry deduplication, SQL query count, and highest number
of simultaneous deliveries.

`benchmark_recovery.py` drains 1,000 synthetic outbox events. It records elapsed
time, SQL queries, commits, accepted job identities, and published rows. The
short-transaction version deliberately performs more queries and commits so it
can hold database locks for less time.

## Recorded results

| SQLite dashboard revision workload | Aggregate query median / p95 ms | Durable revision median / p95 ms |
|---|---:|---:|
| 1,000 alarms, 10,000 notifications, five reads | 7.36 / 8.59 | 8.98 / 56.70 |
| 10,000 alarms, 100,000 notifications, five reads | 187.26 / 222.63 | 3.67 / 3.73 |

| Serial targets | Provider delay ms | Baseline median / p95 ms | Candidate median / p95 ms | SQL queries in each version |
|---:|---:|---:|---:|---:|
| 1 | 0 | 138.61 / 553.64 | 225.92 / 598.42 | 4 |
| 5 | 0 | 860.56 / 1430.24 | 242.61 / 301.90 | 12 |
| 20 | 0 | 1077.41 / 1435.97 | 624.79 / 1821.02 | 42 |
| 1 | 5 | 58.31 / 378.20 | 22.81 / 79.75 | 4 |
| 5 | 5 | 218.59 / 272.68 | 381.81 / 405.77 | 12 |
| 20 | 5 | 819.39 / 1108.44 | 750.60 / 1457.56 | 42 |

Every fan-out run delivered to each target once and wrote one delivery record
per target. Calling the service again produced no additional delivery.
Provider calls remained serial, with at most one active at any time.

| 1,000-event synthetic recovery | Median / p95 ms | SQL queries | Commits |
|---|---:|---:|---:|
| Previous single transaction | 318.94 / 433.67 | 3 | 1 |
| Short batches | 863.27 / 1238.40 | 81 | 40 |

Both recovery variants accepted 1,000 stable job identities and marked 1,000
outbox rows as published. Short batches required more local queries and commits.
They are meant to shorten the time PostgreSQL locks are held, but this benchmark
did not measure lock retention.

Scheduling and I/O varied substantially between runs. Some delivery runs with
no provider delay were even slower than runs with a delay. These results support
the correctness and query-count claims above; they do not show a general speedup
or regression.

## Tests against real services

Before making a deployment decision from these results, repeat the checks with
disposable PostgreSQL and Redis services:

- Apply migration `0008` to a populated PostgreSQL 16 copy, and test both
  trigger rollback and concurrent writers.
- Run concurrent outbox publishers and delayed Redis publication through real
  PostgreSQL and Redis instances.
- Confirm that worker snapshots appear and expire in real Redis, then recover a
  1,000-event backlog through ARQ.
- Run the built container smoke test on Linux.
- Measure concurrent dashboard polling against PostgreSQL and Redis.
- Measure buffer use, sorting, and write cost for each proposed PostgreSQL index
  before adding one to a migration.

```bash
TEST_POSTGRES_URL='<disposable PostgreSQL URL>' \
YELK_IP_ALLOWLIST='127.0.0.1/32' \
  make test-postgres-smoke

make container-check

.venv/bin/python scripts/benchmark_indexes.py \
  --disposable-database-url '<empty disposable PostgreSQL URL>' \
  --confirm-empty-disposable-schema \
  --output /tmp/escalane-index-plans.json
```

The index benchmark requires PostgreSQL 16 and an empty disposable schema. It
applies the real migrations and evaluates four candidate indexes one at a time.
For each candidate, it records `EXPLAIN (ANALYZE, BUFFERS)` plans, runs five
100-alarm write trials inside savepoints that are rolled back, and drops the
index. The benchmark then rolls back its outer transaction. A candidate must
either reduce median buffer use or remove a sort, and fewer than three of the
five write trials may regress by more than 10 percent. Substring search remains
diagnostic only.

## Raw measurements

[optimization-measurements.json](optimization-measurements.json) contains the
raw samples, query plans, source hashes, environment versions, and container
base-image registry metadata behind these tables. The pinned Docker base image
was checked for Linux amd64 and arm64 metadata. Registry metadata alone cannot
confirm that dependencies install, the image starts, or the worker passes its
smoke test.
