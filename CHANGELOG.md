# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Project scaffold: `uv` package `medigraph` (Python 3.12), ruff, pytest, Makefile, MIT license.
- CI workflow (ruff and unit tests in offline mock mode) and pre-commit hooks.
- `scripts/bootstrap_tools.sh`: user-local Neo4j Community 2026.10.2 and Synthea v4.0.0 in `.tools/`.
- `scripts/neo4j.sh`: local Neo4j lifecycle on bolt :7688 / http :7475.
- `scripts/synthea.sh`: seeded Synthea population generation.
- `scripts/preflight.py`: environment checks.
- `medigraph data cohort`: deterministic selection of a 20-patient cohort, with medication episodes derived from Synthea renewals.
- `medigraph data notes`: deterministic note generator. Writes 297 progress, discharge and medication reconciliation notes, plus gold fact (character spans) and perturbation (P1–P8) manifests.
