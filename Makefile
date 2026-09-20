PYTHON ?= .venv/bin/python
COMPOSE ?= docker compose
.DEFAULT_GOAL := run

.PHONY: run build down logs status restart local-run local-worker staging-up test lint format schema check mock mock-check mysql-up mock-db frontend-dev frontend-check ontology-up ontology-down

run:
	$(COMPOSE) up -d --wait --wait-timeout 120 api intake-worker frontend

build:
	$(COMPOSE) build api intake-worker frontend

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail 100 api intake-worker frontend

status:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart api intake-worker frontend

local-run:
	$(PYTHON) -m bank_project serve --reload

local-worker:
	$(PYTHON) -m bank_project.intake.worker

check: lint test
	$(PYTHON) scripts/export_openapi.py --check
	$(PYTHON) scripts/validate_mock.py

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

mock:
	$(PYTHON) scripts/generate_mock.py

mock-check:
	$(PYTHON) scripts/validate_mock.py

mysql-up:
	$(PYTHON) scripts/prepare_local_mysql.py
	$(COMPOSE) up -d --wait --wait-timeout 120 mysql

staging-up: mysql-up
	$(PYTHON) scripts/setup_staging.py

mock-db: mysql-up
	$(PYTHON) scripts/load_mock_mysql.py

frontend-dev:
	npm --prefix frontend run dev

frontend-check:
	npm --prefix frontend run format:check
	npm --prefix frontend run build
	npm --prefix frontend test

ONTOLOGY_COMPOSE = $(COMPOSE) --env-file .env.ontology -f compose.ontology.yaml
ontology-up:
	$(PYTHON) scripts/prepare_local_ontology.py
	$(ONTOLOGY_COMPOSE) up -d --wait --wait-timeout 120

ontology-down:
	$(ONTOLOGY_COMPOSE) down
