.PHONY: help install run test clean docker-build docker-up docker-down docker-logs lint format

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	uv sync

run: ## Run the bot
	uv run main.py

run-dry: ## Run the bot in dry-run mode
	DRY_RUN=true uv run main.py

test: ## Run tests (placeholder)
	@echo "Tests not yet implemented"

clean: ## Clean cache and temporary files
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage htmlcov dist build

docker-build: ## Build Docker image
	docker build -t tele-assistant-bot .

docker-up: ## Start Docker containers
	docker-compose up -d

docker-down: ## Stop Docker containers
	docker-compose down

docker-logs: ## View Docker logs
	docker-compose logs -f

docker-restart: ## Restart Docker containers
	docker-compose restart

lint: ## Run linter (placeholder)
	@echo "Linting not yet configured"

format: ## Format code (placeholder)
	@echo "Formatting not yet configured"

