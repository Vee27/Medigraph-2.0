"""Check that the local environment is ready: tools, Neo4j, data, model settings.

Usage:  uv run python scripts/preflight.py
Exit code 1 when a required check fails. Secrets are never printed.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

from medigraph.config import PROJECT_ROOT, get_settings

OK, WARN, FAIL = "ok", "warn", "FAIL"


def check_python() -> tuple[str, str]:
    v = sys.version_info
    return (OK if v >= (3, 12) else FAIL), f"Python {v.major}.{v.minor}.{v.micro}"


def check_env_file() -> tuple[str, str]:
    if (PROJECT_ROOT / ".env").exists():
        return OK, ".env present"
    return WARN, ".env missing: defaults are used (see README, Configuration)"


def check_jdk() -> tuple[str, str]:
    s = get_settings()
    java_home = s.java_home or s.tools_dir / "jdk-21"
    if (Path(java_home) / "bin" / "java").exists():
        return OK, f"JDK at {java_home}"
    return WARN, "no JDK: needed only for Neo4j and Synthea (scripts/bootstrap_tools.sh)"


def check_tools() -> tuple[str, str]:
    s = get_settings()
    neo4j = (s.tools_dir / "neo4j" / "bin" / "neo4j").exists()
    synthea = any((s.tools_dir / "synthea").glob("synthea-*.jar"))
    if neo4j and synthea:
        return OK, "Neo4j and Synthea installed in .tools/"
    missing = [n for n, ok in (("Neo4j", neo4j), ("Synthea", synthea)) if not ok]
    return WARN, f"missing in .tools/: {', '.join(missing)} (scripts/bootstrap_tools.sh)"


def check_neo4j() -> tuple[str, str]:
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ServiceUnavailable

    s = get_settings()
    password = s.neo4j_password.get_secret_value()
    if not password:
        return FAIL, "MEDIGRAPH_NEO4J_PASSWORD is not set"
    try:
        with GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, password)) as driver:
            driver.verify_connectivity()
            agent = driver.get_server_info().agent
    except ServiceUnavailable:
        return FAIL, f"Neo4j not reachable at {s.neo4j_uri} (scripts/neo4j.sh start)"
    except AuthError:
        return FAIL, "Neo4j rejected the credentials in .env"
    return OK, f"{agent} at {s.neo4j_uri}"


def check_data() -> tuple[str, str]:
    s = get_settings()
    csvs = all((s.cohort_csv_dir / f"{t}.csv").exists() for t in ("patients", "medications", "encounters"))
    notes = (s.synthetic_dir / "manifests" / "documents.jsonl").exists()
    if csvs and notes:
        n = sum(1 for _ in (s.synthetic_dir / "manifests" / "documents.jsonl").open())
        return OK, f"cohort CSVs and {n} generated notes in {s.synthetic_dir.relative_to(PROJECT_ROOT)}"
    return FAIL, "synthetic data missing: run `make data` (or `make synthea` to regenerate the cohort)"


def check_embedder() -> tuple[str, str]:
    s = get_settings()
    if s.embedder == "hash":
        return OK, "embedder=hash (offline test embedder)"
    if importlib.util.find_spec("sentence_transformers") is None:
        return WARN, "embedder=bge but sentence-transformers not installed (`uv sync --extra embed`)"
    return OK, "embedder=bge (sentence-transformers installed)"


def check_llm() -> tuple[str, str]:
    s = get_settings()
    if s.llm_mode == "mock":
        return OK, f"llm_mode=mock ($0); budget cap ${s.budget_usd:.2f}"
    return WARN, f"llm_mode=bedrock ({s.bedrock_model_id}, {s.aws_region}); cap ${s.budget_usd:.2f}"


CHECKS: list[tuple[str, Callable[[], tuple[str, str]]]] = [
    ("python", check_python),
    ("env file", check_env_file),
    ("jdk", check_jdk),
    ("tools", check_tools),
    ("neo4j", check_neo4j),
    ("data", check_data),
    ("embedder", check_embedder),
    ("llm", check_llm),
]


def main() -> int:
    failed = False
    for name, check in CHECKS:
        try:
            status, detail = check()
        except Exception as exc:  # a crashing check is a failed check, not a crashed preflight
            status, detail = FAIL, f"{type(exc).__name__}: {exc}"
        failed |= status == FAIL
        print(f"[{status:>4}] {name:<9} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
