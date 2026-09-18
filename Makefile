PYTHON ?= .venv/bin/python
MOCK_PYTHON ?= python3
COMPOSE ?= docker compose
.DEFAULT_GOAL := run

.PHONY: run build build-cached down logs status restart local-run test lint format schema check mock mock-check mysql-up mock-db
check: lint test
	$(PYTHON) scripts/export_openapi.py --check
	$(PYTHON) scripts/validate_mock.py

mock:
	$(MOCK_PYTHON) scripts/generate_mock.py

mock-check:
	$(MOCK_PYTHON) scripts/validate_mock.py

mysql-up:
	$(PYTHON) scripts/prepare_local_mysql.py
	$(COMPOSE) --profile database up -d --wait --wait-timeout 120 mysql

mock-db: mysql-up
	$(PYTHON) scripts/load_mock_mysql.py

run:
	$(COMPOSE) up -d --wait --wait-timeout 60

build:
	$(COMPOSE) build api

build-cached:
	DOCKER_BUILDKIT=0 docker build --pull=false -t bank-project-api .

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail 100

status:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart api

local-run:
	$(PYTHON) -m bank_project serve --reload

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests scripts
	$(PYTHON) -m ruff format --check src tests scripts

format:
	$(PYTHON) -m ruff check --fix src tests scripts
	$(PYTHON) -m ruff format src tests scripts

schema:
	$(PYTHON) scripts/export_openapi.py
