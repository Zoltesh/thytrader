.DEFAULT_GOAL := help

.PHONY: run stop logs status help

run: ## Configure and start the full local stack: .env, Postgres, migrations, API, workers, web
	uv run python scripts/setup_local_stack.py

stop: ## Stop the stack (preserves database and market-data volumes)
	docker compose down

logs: ## Follow API, worker, market-data worker, and web logs
	docker compose logs -f api worker market-data-worker web

status: ## Show service health
	docker compose ps

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36mmake %-8s\033[0m %s\n", $$1, $$2}'
