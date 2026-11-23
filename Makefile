.PHONY: help install db seed dev dev-backend dev-frontend test test-backend test-frontend lint eval ollama-setup

PSQL ?= /opt/homebrew/opt/postgresql@18/bin/psql
DB   ?= platform_copilot

help: ## List the available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npx -y npm@11 ci

db: ## (Re)create the local database and schema. Deletes all its data
	$(PSQL) -d postgres -c "DROP DATABASE IF EXISTS $(DB) WITH (FORCE)" -c "CREATE DATABASE $(DB)"
	$(PSQL) -d $(DB) -f db/init.sql

seed: ## Load fake users, data products and documents (embeds the docs with Ollama)
	cd backend && uv run python -m platform_copilot.seed

dev: ## Run backend (:8000) and frontend (:5173) together
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend: ## Run the API on http://localhost:8000 (docs at /docs)
	cd backend && uv run uvicorn platform_copilot.main:create_app --factory --reload --port 8000

dev-frontend: ## Run the UI on http://localhost:5173 (proxies /api to :8000)
	cd frontend && npm run dev

test: test-backend test-frontend ## Run all tests

test-backend: ## Lint, type-check and test the backend (needs a local PostgreSQL with pgvector)
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest

test-frontend: ## Lint, type-check, test and build the frontend
	cd frontend && npm run lint && npm run typecheck && npm test && npm run build

eval: ## Run the evals against the local model and write evals/results/<date>-<model>.md
	cd backend && uv run python ../evals/run.py

ollama-setup: ## Start Ollama and download the chat and embedding models
	brew services start ollama
	ollama pull qwen2.5:3b
	ollama pull nomic-embed-text
