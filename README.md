# MediGraph 2.0

Temporal clinical evidence reconciliation over a bitemporal patient knowledge graph.

> **Status: work in progress.** This is milestone 1 of 10: project scaffold, local
> Neo4j tooling and a seeded synthetic dataset with gold labels. Extraction, the graph,
> reconciliation, evaluation and the API land in the following milestones (see
> [Roadmap](#roadmap)).
>
> Research and educational software only. Not a medical device and not for clinical
> decision-making. All patient data in this repository is synthetic.

## Why

A patient's chart contradicts itself over time. Medication lists are copied forward
after a drug was stopped. Allergy sections say "no known allergies" next to a documented
penicillin allergy. A hospitalisation is restated with the wrong date. Chunk-based RAG
retrieves whichever passage looks most similar and has no notion of *when* a statement
was true, *when* it was recorded, or which source to trust.

MediGraph 2.0 asks:

> Does an explicit bitemporal assertion graph with rule-based reconciliation answer
> temporal clinical questions more accurately, and more honestly (correct abstentions,
> flagged conflicts), than hybrid chunk retrieval? And how much of the remaining error
> can a budget-capped LLM adjudicator fix per dollar?

Design stance: deterministic first. Extraction, graph queries and reconciliation run
locally. The only LLM is Claude Haiku 4.5 on Amazon Bedrock. It is used late, only for
cases the rules mark as uncertain, behind a hard spending cap enforced in code. The
default mode is a mock, so everything runs and is evaluated at $0. Tracing is local
(JSONL + SQLite); no hosted tracing service is used.

## What is here now

- **Synthetic cohort**: 20 patients from [Synthea](https://github.com/synthetichealth/synthea)
  v4.0.0 (seed 42, ages 45–85). The cohort keeps patients with 2+ years of history,
  3–15 chronic drugs and at least one real discontinuation in the 2021–2025 window.
- **297 generated notes** (240 progress notes, 40 medication reconciliations,
  17 discharge summaries) rendered deterministically from the structured records.
- **Gold manifests**: 4,413 facts with exact character spans, and 517 injected
  documentation defects across 8 types, each labelled with the behaviour a correct
  system should show. Details are in [data/synthetic/README.md](data/synthetic/README.md).
- **Local tooling**: user-local Neo4j Community and Synthea in `.tools/`, with no Docker
  and no system-wide installs.

## Quickstart

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), `make` and `curl` (Linux or
WSL). Java is only needed to run Neo4j or regenerate the cohort.

```bash
make setup        # core + dev dependencies, git hooks
make test         # unit tests: no Neo4j, network or credentials needed
make data         # re-render notes and manifests from the committed cohort CSVs
```

Local Neo4j (needed from milestone 3):

```bash
make tools                          # downloads Neo4j + Synthea into .tools/ (and a JDK unless MEDIGRAPH_JAVA_HOME is set)
scripts/neo4j.sh password <pw>      # once; or set MEDIGRAPH_NEO4J_PASSWORD in .env and omit <pw>
make neo4j-start                    # bolt://localhost:7688, browser http://localhost:7475
make preflight                      # checks tools, Neo4j, data and settings
```

Regenerating the cohort from scratch (optional; the result is committed):

```bash
make synthea      # runs Synthea with fixed seed/dates, then selects the 20-patient cohort
make data
```

## Configuration

Settings are read from environment variables or a `.env` file in the project root
(gitignored). Every setting has a safe default.

| Variable | Default | Purpose |
|---|---|---|
| `MEDIGRAPH_JAVA_HOME` | `.tools/jdk-21` | Reuse an existing JDK 21 instead of downloading one |
| `MEDIGRAPH_NEO4J_URI` | `bolt://localhost:7688` | Local Neo4j |
| `MEDIGRAPH_NEO4J_USER` | `neo4j` | |
| `MEDIGRAPH_NEO4J_PASSWORD` | *(empty)* | Required once Neo4j is used |
| `MEDIGRAPH_EMBEDDER` | `bge` | `bge` (BAAI/bge-small-en-v1.5) or `hash` (offline, used in CI) |
| `MEDIGRAPH_LLM_MODE` | `mock` | `mock` ($0) or `bedrock` (Claude Haiku 4.5) |
| `MEDIGRAPH_BUDGET_USD` | `1.10` | Hard project spending cap for LLM calls |
| `MEDIGRAPH_GLOBAL_CEILING_USD` | `5.00` | Absolute ceiling across all runs |
| `MEDIGRAPH_AWS_REGION` | `eu-central-1` | Bedrock region |
| `MEDIGRAPH_BEDROCK_MODEL_ID` | `eu.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock inference profile |

AWS credentials use the normal boto3 chain (`AWS_PROFILE`, environment variables and so on).

## Repository layout

```
scripts/            bootstrap_tools.sh, neo4j.sh, synthea.sh, preflight.py
src/medigraph/
  config.py         typed settings
  cli.py            `medigraph` CLI (data cohort | data notes)
  data/             Synthea loading, medication episodes, note generator, perturbations
data/synthetic/     committed cohort CSVs, notes and gold manifests
tests/              unit tests (the committed data is checked to be byte-identical to the generator)
```

## Roadmap

1. Scaffold, local Neo4j tooling, seeded synthetic data with gold manifests ← *this milestone*
2. Bitemporal assertion model and local clinical extractors
3. Neo4j loading and bitemporal query catalogue
4. Rule-based evidence reconciliation with conflict edges
5. v1-style and full-corpus hybrid retrieval baselines
6. Temporal question answering with citations (CLI + FastAPI)
7. Temporal benchmark and scored comparison of arms A/B/C
8. Budget-capped Haiku gateway and local tracing (mock only)
9. Haiku 4.5 adjudication of uncertain evidence (arm D)
10. Documentation, integration CI, v2.0.0 release

## Relation to v1

[MediGraph-RAG](https://github.com/Vee27/MediGraph-RAG) (v1) is a LangGraph +
Ollama + ChromaDB chatbot over a single chart. 2.0 is a fresh codebase built around
v1's main limitations:
- v1 has no knowledge graph or time model.
- A second upload for the same patient overwrites the first.
- BM25 only re-ranks the dense candidates.
- The evaluation had no temporal questions.

v1's retrieval logic will be re-implemented as an evaluation baseline, so the
improvement is measured rather than claimed.

## License

[MIT](LICENSE). The synthetic records are generated with Synthea (Apache-2.0) and
describe no real person.
