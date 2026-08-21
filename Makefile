.PHONY: bootstrap install dev test package-check release-check container-check hygiene-check lint lint-fix format format-check audit clean demo-prepare

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SERVICE_DIR := $(ROOT_DIR)/services/escalane
SCRIPT_DIR := $(ROOT_DIR)/scripts
VENV ?= $(ROOT_DIR)/.venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
BANDIT := $(VENV)/bin/bandit
PIP_AUDIT := $(VENV)/bin/pip-audit
PRE_COMMIT := $(VENV)/bin/pre-commit
RELEASE_TAG ?= v0.4.0-alpha.1
RUFF_PATHS := "$(SERVICE_DIR)" "$(SCRIPT_DIR)"

# Development
bootstrap:
	python3.14 -m venv "$(VENV)"
	$(PYTHON) -m pip install --upgrade pip

install: bootstrap
	$(PIP) install -e "$(SERVICE_DIR)[dev]"

dev: install
	cd "$(SERVICE_DIR)" && $(PYTHON) -m pytest tests/ -v

# Testing
test:
	cd "$(SERVICE_DIR)" && $(PYTHON) -m pytest -q

package-check:
	cd "$(SERVICE_DIR)" && $(PYTHON) -m build --wheel

release-check:
	$(PYTHON) scripts/validate_release.py --tag "$(RELEASE_TAG)"

container-check: docker-build
	bash scripts/smoke_container.sh escalane:local

hygiene-check:
	git ls-files --cached --others --exclude-standard -z | $(PYTHON) scripts/verify_public_hygiene.py --null

test-verbose:
	cd "$(SERVICE_DIR)" && $(PYTHON) -m pytest -v

# Linting & Formatting
lint:
	$(RUFF) format --check $(RUFF_PATHS)
	$(RUFF) check $(RUFF_PATHS)

lint-fix:
	$(RUFF) format $(RUFF_PATHS)
	$(RUFF) check --fix $(RUFF_PATHS)

format:
	$(RUFF) format $(RUFF_PATHS)

format-check:
	$(RUFF) format --check $(RUFF_PATHS)

# Security & Dependency Audit
audit:
	$(RUFF) check $(RUFF_PATHS)
	# api/i18n.py is a static translation catalogue whose device-token labels trigger B105.
	$(BANDIT) -q -r -x "$(SERVICE_DIR)/escalane/api/i18n.py" "$(SERVICE_DIR)/escalane"
	# Script fixtures and fixed-argv Git inspection have reviewed low findings; enforce medium+.
	$(BANDIT) -q -ll -r "$(SCRIPT_DIR)"
	cd "$(SERVICE_DIR)" && $(PIP_AUDIT)

# Pre-commit hooks
pre-commit:
	$(PRE_COMMIT) install

# Cleanup
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov coverage reports scratch tmp
	rm -rf services/escalane/.pytest_cache services/escalane/.ruff_cache services/escalane/.mypy_cache services/escalane/.coverage
	rm -rf services/escalane/.venv
	rm -rf services/escalane/build services/escalane/dist services/escalane/*.egg-info
	find . -path ./.git -prune -o -type f -name .DS_Store -delete
	find . -path ./.git -prune -o -type d -name .venv -prune -o -type d -name venv -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +

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
	$(PYTHON) scripts/demo_prepare.py
