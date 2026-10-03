.PHONY: bootstrap install constraints-check constraints-refresh dev test test-verbose test-postgres-smoke coverage lint lint-fix format format-check type-check architecture-check pages-build pages-check package-check release-check hygiene-check audit clean docker-build docker-up docker-down docker-logs container-check demo-prepare check

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV ?= $(ROOT_DIR)/.venv
PYTHON_BOOTSTRAP ?= python3.14
PYTHON_PATCH := 3.14.7
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
BANDIT := $(VENV)/bin/bandit
PIP_AUDIT := $(VENV)/bin/pip-audit
BUILD_CONSTRAINTS := $(ROOT_DIR)/constraints/python314-build.txt
RUNTIME_CONSTRAINTS := $(ROOT_DIR)/constraints/python314-runtime.txt
DEV_CONSTRAINTS := $(ROOT_DIR)/constraints/python314-dev.txt
POSTGRES_TEST_URL := $(if $(TEST_POSTGRES_URL),$(TEST_POSTGRES_URL),$(DATABASE_URL))
RELEASE_TAG ?= v0.4.0-alpha.1
CHECK_PATHS := src tests migrations scripts

bootstrap:
	$(PYTHON_BOOTSTRAP) -c 'import sys; expected = (3, 14, 7); assert sys.version_info[:3] == expected, f"CPython {expected[0]}.{expected[1]}.{expected[2]} is required, found {sys.version.split()[0]}"'
	$(PYTHON_BOOTSTRAP) -m venv "$(VENV)"
	$(PYTHON) -m pip install --constraint "$(BUILD_CONSTRAINTS)" --upgrade pip

install: bootstrap
	PIP_BUILD_CONSTRAINT="$(BUILD_CONSTRAINTS)" $(PYTHON) -m pip install --constraint "$(DEV_CONSTRAINTS)" -e ".[dev]"

constraints-check:
	$(PYTHON) scripts/check_constraints.py

constraints-refresh:
	$(PYTHON) scripts/refresh_constraints.py
	$(PYTHON) scripts/check_constraints.py

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
	TEST_POSTGRES_URL="$(POSTGRES_TEST_URL)" $(PYTHON) -m pytest -q tests/postgres/outbox_concurrency_postgres.py tests/postgres/optimization_postgres.py

coverage:
	@coverage_file="$$(mktemp)"; trap 'rm -f "$$coverage_file"' EXIT; \
		test_status=0; report_status=0; \
		COVERAGE_FILE="$$coverage_file" $(PYTHON) -m coverage run -m pytest -q -p no:cacheprovider || test_status=$$?; \
		COVERAGE_FILE="$$coverage_file" $(PYTHON) -m coverage report || report_status=$$?; \
		if test "$$test_status" -ne 0; then exit "$$test_status"; fi; \
		exit "$$report_status"

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
		PIP_BUILD_CONSTRAINT="$(BUILD_CONSTRAINTS)" $(PYTHON) -m build --wheel --outdir "$$package_dir"; \
		$(PYTHON) scripts/validate_wheel.py "$$package_dir"/escalane-*.whl; \
		$(PYTHON) -m venv "$$package_dir/venv"; \
		"$$package_dir/venv/bin/python" -m pip install --constraint "$(BUILD_CONSTRAINTS)" --upgrade pip; \
		"$$package_dir/venv/bin/python" -m pip install --constraint "$(RUNTIME_CONSTRAINTS)" "$$package_dir"/escalane-*.whl; \
		env -i HOME="$$HOME" PATH="$$package_dir/venv/bin:/usr/bin:/bin" PYTHONPATH="" "$$package_dir/venv/bin/python" "$(ROOT_DIR)/scripts/validate_wheel.py" --installed

release-check:
	$(PYTHON) scripts/validate_release.py --tag "$(RELEASE_TAG)"

hygiene-check:
	git ls-files --cached --others --exclude-standard -z | $(PYTHON) scripts/verify_public_hygiene.py --null

audit:
	$(BANDIT) -q -r -x src/escalane/web/i18n.py src/escalane
	$(BANDIT) -q -ll -r scripts
	$(PIP_AUDIT)

check: constraints-check format-check lint type-check architecture-check coverage pages-build pages-check package-check hygiene-check audit

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
