UV ?= uv

.DEFAULT_GOAL := help
.PHONY: help setup setup-all lint fmt test tools neo4j-start neo4j-stop neo4j-status synthea data preflight

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install core + dev dependencies and the git hooks
	$(UV) sync
	-$(UV) run pre-commit install

setup-all: ## Install every optional extra too (embeddings, API, Bedrock)
	$(UV) sync --all-extras
	-$(UV) run pre-commit install

lint: ## Ruff lint and format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-fix lint issues and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test: ## Unit tests (no Neo4j or network needed)
	$(UV) run pytest -m "not neo4j"

tools: ## Install user-local Neo4j and Synthea (and a JDK unless MEDIGRAPH_JAVA_HOME is set)
	scripts/bootstrap_tools.sh

neo4j-start: ## Start the local Neo4j (bolt :7688, browser :7475)
	scripts/neo4j.sh start

neo4j-stop: ## Stop the local Neo4j
	scripts/neo4j.sh stop

neo4j-status: ## Show local Neo4j status
	scripts/neo4j.sh status

synthea: ## Regenerate the raw Synthea population and reselect the cohort (needs Java)
	scripts/synthea.sh
	$(UV) run medigraph data cohort

data: ## Render notes and gold manifests from the committed cohort CSVs (deterministic)
	$(UV) run medigraph data notes

preflight: ## Check tools, Neo4j, data and settings
	$(UV) run python scripts/preflight.py
