.PHONY: up down logs test lint fmt migrate revision shell superadmin web web-down

COMPOSE ?= docker compose

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100 api worker

TESTRUN = $(COMPOSE) --profile test run --rm --build test

test:
	$(TESTRUN) pytest -q

lint:
	$(TESTRUN) sh -c "ruff check . && ruff format --check . && mypy app tests"

fmt:
	$(TESTRUN) sh -c "ruff check --fix . && ruff format ."

migrate:
	$(COMPOSE) run --rm api alembic upgrade head

# usage: make revision m="add something"
revision:
	$(COMPOSE) run --rm -v ./backend/alembic/versions:/app/alembic/versions api \
		alembic revision --autogenerate -m "$(m)"

shell:
	$(COMPOSE) run --rm api bash

# usage: make superadmin u=admin   (prompts for the password; or ADMIN_PASSWORD=... make superadmin u=admin)
superadmin:
	$(COMPOSE) run --rm -e ADMIN_PASSWORD api python -m app.cli create-superadmin --username "$(u)"

# Frontend (nginx + SPA) on http://127.0.0.1:8080
web:
	$(COMPOSE) --profile web up -d --build web

web-down:
	$(COMPOSE) --profile web stop web
