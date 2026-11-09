from __future__ import annotations

import json
from pathlib import Path

import pytest

from medigraph.config import Settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_env_file=None)  # defaults only: tests must not depend on a local .env


@pytest.fixture(scope="session")
def synthetic_dir(settings: Settings) -> Path:
    path = settings.synthetic_dir
    if not (path / "manifests" / "documents.jsonl").exists():
        pytest.skip("committed synthetic data missing; run `make data`")
    return path


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.fixture(scope="session")
def documents(synthetic_dir: Path) -> dict[str, dict]:
    return {d["doc_id"]: d for d in read_jsonl(synthetic_dir / "manifests" / "documents.jsonl")}


@pytest.fixture(scope="session")
def facts(synthetic_dir: Path) -> list[dict]:
    return read_jsonl(synthetic_dir / "manifests" / "facts.jsonl")


@pytest.fixture(scope="session")
def perturbations(synthetic_dir: Path) -> list[dict]:
    return read_jsonl(synthetic_dir / "manifests" / "perturbations.jsonl")
