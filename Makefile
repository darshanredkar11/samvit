SHELL := /usr/bin/env bash
.ONESHELL:

.PHONY: install dev test lint clean db-up db-down migrate admin-ui docker-build docker-up

install:
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@echo ""
	@echo "  Run:  source .venv/bin/activate"
	@echo "  Dev:  make dev"

dev:
	SAMVIT_ADMIN_DEV_MODE=true .venv/bin/samvit serve --reload --host 127.0.0.1 --port 8765

test:
	.venv/bin/pytest -q --tb=short $(ARGS)

db-up:
	docker compose up -d postgres redpanda

db-down:
	docker compose down

migrate:
	.venv/bin/samvit migrate

admin-ui:
	cd admin-ui && npm ci && npm run build

docker-build:
	docker compose build samvit

docker-up:
	docker compose up -d

clean:
	rm -rf .venv dist __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
