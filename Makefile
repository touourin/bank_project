PYTHON ?= .venv/bin/python
COMPOSE ?= docker compose
.DEFAULT_GOAL := run

.PHONY: run build build-cached down logs status restart local-run test lint format schema check
check: lint test
	$(PYTHON) scripts/export_openapi.py --check

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
