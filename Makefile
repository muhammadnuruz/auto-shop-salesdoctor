.PHONY: up down logs test lint fmt migrate revision shell superadmin web web-down \
	e2e e2e-up e2e-seed e2e-down load

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
	$(TESTRUN) sh -c "ruff check . && ruff format --check . && mypy app tests e2e"

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

# ---- E2E / load tests: separate stack + fake Sales Doctor (docker-compose.e2e.yml) ----
E2E_ADMIN_PASSWORD ?= e2e-Admin-Passw0rd!
E2E = POSTGRES_PASSWORD=e2e-only-password $(COMPOSE) -p autoshop-e2e \
	-f docker-compose.yml -f docker-compose.e2e.yml

e2e-up:
	$(E2E) --profile web up -d --build --wait

# usage: make e2e-seed [LOAD=1]
e2e-seed:
	$(E2E) run --rm api python -m e2e.seed --admin-password "$(E2E_ADMIN_PASSWORD)" $(if $(LOAD),--load,)

e2e: e2e-up e2e-seed
	cd frontend && E2E_ADMIN_PASSWORD="$(E2E_ADMIN_PASSWORD)" npx playwright test

e2e-down:
	$(E2E) --profile web down -v

# usage: make load [ARGS="--shops 50 --duration 120"]   (needs: make e2e-up e2e-seed LOAD=1)
load:
	$(E2E) run --rm api python -m e2e.load --think-min 1 --think-max 4 --sd-latency 300 $(ARGS)
