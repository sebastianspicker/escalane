.PHONY: test e2e browser-e2e test-postgres-smoke package-check hygiene-check lint lint-fix format format-check audit clean install dev demo-prepare demo-screens

# Development
install:
	cd services/alarm_broker && pip install -e ".[dev]"

dev: install
	cd services/alarm_broker && python -m pytest tests/ -v

# Testing
test:
	cd services/alarm_broker && python -m pytest -q -m "not e2e" --cov=alarm_broker --cov-report=term-missing

e2e:
	cd services/alarm_broker && python -m pytest -q tests/e2e --tb=short

browser-e2e:
	cd services/alarm_broker && python -m pytest -q tests/e2e/test_browser_ui.py --tb=short

test-postgres-smoke:
	cd services/alarm_broker && alembic upgrade head && pytest -q tests/test_postgres_smoke.py --tb=short

package-check:
	cd services/alarm_broker && python -m build --wheel

hygiene-check:
	git ls-files --cached --others --exclude-standard -z | python scripts/verify_public_hygiene.py --null

test-verbose:
	cd services/alarm_broker && python -m pytest -v -m "not e2e" --cov=alarm_broker --cov-report=term-missing

# Linting & Formatting
lint:
	ruff format --check services/alarm_broker
	ruff check services/alarm_broker

lint-fix:
	ruff format services/alarm_broker
	ruff check --fix services/alarm_broker

format:
	ruff format services/alarm_broker

format-check:
	ruff format --check services/alarm_broker

# Security & Dependency Audit
audit:
	ruff check services/alarm_broker
	bandit -q -r services/alarm_broker/alarm_broker
	cd services/alarm_broker && pip-audit . --ignore-vuln CVE-2026-4539

# Pre-commit hooks
pre-commit:
	pre-commit install

# Cleanup
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov coverage reports scratch tmp
	rm -rf services/alarm_broker/.pytest_cache services/alarm_broker/.ruff_cache services/alarm_broker/.mypy_cache services/alarm_broker/.coverage
	rm -rf services/alarm_broker/build services/alarm_broker/dist services/alarm_broker/*.egg-info
	find . -path ./.git -prune -o -path ./.venv -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +

# Docker
docker-build:
	docker compose -f deploy/docker-compose.yml build

docker-up:
	docker compose -f deploy/docker-compose.yml up -d

docker-down:
	docker compose -f deploy/docker-compose.yml down

docker-logs:
	docker compose -f deploy/docker-compose.yml logs -f

# Local demo workflow
demo-prepare:
	python scripts/demo_prepare.py

demo-screens:
	python scripts/demo_capture.py
