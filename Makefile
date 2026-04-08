# -------------------------------------------------------
# Incident Triage Agent — Makefile
# -------------------------------------------------------

.PHONY: help install lint test test-cov demo run clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python dependencies into local venv
	python -m venv .venv && \
	.venv/bin/pip install --upgrade pip && \
	.venv/bin/pip install -r requirements.txt

lint:  ## Run ruff linter
	.venv/bin/ruff check agent/ api/ tests/

test:  ## Run unit tests
	.venv/bin/pytest tests/unit/ -v

test-cov:  ## Run tests with coverage report
	.venv/bin/pytest tests/ -v --cov=agent --cov-report=term-missing

demo:  ## Run all 5 demo scenarios via CLI
	@echo "\n=== Running all demo scenarios ===\n"
	@for scenario in bad_deploy oom_kill cascade_failure cert_expiry noisy_neighbor; do \
		echo "\n--- Scenario: $$scenario ---\n"; \
		.venv/bin/python -m agent.main triage scenarios/$$scenario --pretty; \
	done

run:  ## Start the API server locally (no Docker)
	.venv/bin/uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

up:  ## Start full stack with Docker Compose
	docker compose up --build

down:  ## Stop Docker Compose stack
	docker compose down

clean:  ## Remove reports and __pycache__
	rm -rf reports/*.json reports/*.md
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete