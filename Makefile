.PHONY: bootstrap install dev test test-verbose test-postgres-smoke coverage lint lint-fix format format-check type-check architecture-check pages-build pages-check package-check release-check hygiene-check audit clean docker-build docker-up docker-down docker-logs container-check demo-prepare check

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV ?= $(ROOT_DIR)/.venv
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
BANDIT := $(VENV)/bin/bandit
PIP_AUDIT := $(VENV)/bin/pip-audit
POSTGRES_TEST_URL := $(if $(TEST_POSTGRES_URL),$(TEST_POSTGRES_URL),$(DATABASE_URL))
RELEASE_TAG ?= v0.4.0-alpha.1
CHECK_PATHS := src tests migrations scripts

bootstrap:
	python3.14 -m venv "$(VENV)"
	$(PYTHON) -m pip install --upgrade pip

install: bootstrap
	$(PYTHON) -m pip install -e ".[dev]"

dev:
	$(PYTHON) -m uvicorn escalane.web.main:app --reload

test:
	$(PYTHON) -m pytest -q -p no:cacheprovider

test-verbose:
	$(PYTHON) -m pytest -v -p no:cacheprovider

test-postgres-smoke:
	@test -n "$(POSTGRES_TEST_URL)" || { echo "TEST_POSTGRES_URL or DATABASE_URL is required for the PostgreSQL smoke test." >&2; exit 2; }
	@test -n "$(YELK_IP_ALLOWLIST)" || { echo "YELK_IP_ALLOWLIST is required for the PostgreSQL smoke test." >&2; exit 2; }
	DATABASE_URL="$(POSTGRES_TEST_URL)" $(PYTHON) -m alembic upgrade head
	DATABASE_URL="$(POSTGRES_TEST_URL)" $(PYTHON) -m alembic current --check-heads
	DATABASE_URL="$(POSTGRES_TEST_URL)" $(PYTHON) -c 'import os; from psycopg import connect; url = os.environ["DATABASE_URL"].replace("+asyncpg", "").replace("+psycopg", ""); connection = connect(url); cursor = connection.cursor(); cursor.execute("SELECT to_regclass('"'"'public.alarm_event_outbox'"'"')"); assert cursor.fetchone()[0] == "alarm_event_outbox"; cursor.execute("INSERT INTO sites (id, name) VALUES ('"'"'postgres-smoke'"'"', '"'"'PostgreSQL smoke'"'"') RETURNING id"); assert cursor.fetchone()[0] == "postgres-smoke"; connection.rollback(); cursor.close(); connection.close()'
	TEST_POSTGRES_URL="$(POSTGRES_TEST_URL)" $(PYTHON) -m pytest -q tests/postgres/outbox_concurrency_postgres.py

coverage:
	@coverage_file="$$(mktemp)"; trap 'rm -f "$$coverage_file"' EXIT; \
		COVERAGE_FILE="$$coverage_file" $(PYTHON) -m coverage run -m pytest -q -p no:cacheprovider; \
		COVERAGE_FILE="$$coverage_file" $(PYTHON) -m coverage report

lint:
	$(RUFF) format --check $(CHECK_PATHS)
	$(RUFF) check $(CHECK_PATHS)

lint-fix:
	$(RUFF) format $(CHECK_PATHS)
	$(RUFF) check --fix $(CHECK_PATHS)

format:
	$(RUFF) format $(CHECK_PATHS)

format-check:
	$(RUFF) format --check $(CHECK_PATHS)

type-check:
	$(PYTHON) -m mypy src migrations scripts

architecture-check:
	$(PYTHON) scripts/check_architecture.py

pages-build:
	$(PYTHON) scripts/build_pages.py

pages-check:
	$(PYTHON) scripts/validate_pages.py build/pages

package-check:
	@package_dir="$$(mktemp -d)"; trap 'rm -rf "$$package_dir"' EXIT; \
		rm -rf "$(ROOT_DIR)/src/escalane.egg-info" "$(ROOT_DIR)/build/lib"; \
		if test -d "$(ROOT_DIR)/build"; then find "$(ROOT_DIR)/build" -maxdepth 1 -type d -name 'bdist.*' -exec rm -rf {} +; fi; \
		$(PYTHON) -m build --wheel --outdir "$$package_dir"; \
		$(PYTHON) scripts/validate_wheel.py "$$package_dir"/escalane-*.whl

release-check:
	$(PYTHON) scripts/validate_release.py --tag "$(RELEASE_TAG)"

hygiene-check:
	git ls-files --cached --others --exclude-standard -z | $(PYTHON) scripts/verify_public_hygiene.py --null

audit:
	$(BANDIT) -q -r -x src/escalane/web/i18n.py src/escalane
	$(BANDIT) -q -ll -r scripts
	$(PIP_AUDIT)

check: format-check lint type-check architecture-check coverage pages-build pages-check package-check hygiene-check audit

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov coverage reports scratch tmp build dist src/escalane.egg-info
	find . -path ./.git -prune -o -type f -name .DS_Store -delete
	find . -path ./.git -prune -o -type d -name .venv -prune -o -type d -name venv -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +

docker-build:
	docker compose -f deploy/docker-compose.yml build

docker-up:
	docker compose -f deploy/docker-compose.yml up -d

docker-down:
	docker compose -f deploy/docker-compose.yml down

docker-logs:
	docker compose -f deploy/docker-compose.yml logs -f

container-check:
	bash scripts/smoke_container.sh escalane:local

demo-prepare:
	$(PYTHON) scripts/demo_prepare.py
