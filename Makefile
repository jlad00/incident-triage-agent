.PHONY: help run dev dev-api test lint format demo demo-fast clean

help:         ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?##"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

run:          ## Start the full stack via Docker Compose
	docker compose up --build

dev:          ## Run CLI against bad_deploy scenario (no LLM)
	python -m agent.main scenarios/bad_deploy --no-llm

dev-api:      ## Run FastAPI server locally (hot reload)
	uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

test:         ## Run all tests with coverage
	pytest tests/ -v --cov=agent --cov-report=term-missing

lint:         ## Check linting
	ruff check agent/ api/ tests/

format:       ## Auto-fix lint issues
	ruff format agent/ api/ tests/

demo:         ## Run all 5 scenarios with LLM (writes reports/)
	@bash scripts/run_demo.sh

demo-fast:    ## Run all 5 scenarios without LLM (deterministic only)
	@bash scripts/run_demo.sh --no-llm

clean:        ## Remove generated reports
	rm -f reports/incident-*.json reports/incident-*.md