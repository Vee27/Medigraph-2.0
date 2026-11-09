"""Typed settings, read from environment variables (prefix ``MEDIGRAPH_``) and ``.env``.

Every setting has a safe default: mock LLM mode, local Neo4j, offline-capable embedder.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEDIGRAPH_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    data_dir: Path = PROJECT_ROOT / "data"
    tools_dir: Path = PROJECT_ROOT / ".tools"
    java_home: Path | None = None

    # Synthetic data generation
    seed: int = 42
    cohort_size: int = 20
    reference_date: date = date(2026, 1, 1)  # must match scripts/synthea.sh
    window_years: int = 5  # notes cover this many years before reference_date

    # Neo4j (local, scripts/neo4j.sh)
    neo4j_uri: str = "bolt://localhost:7688"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")

    # Retrieval and LLM (used from later milestones on)
    embedder: Literal["bge", "hash"] = "bge"
    llm_mode: Literal["mock", "bedrock"] = "mock"
    budget_usd: float = 1.10
    global_ceiling_usd: float = 5.00
    aws_region: str = "eu-central-1"
    bedrock_model_id: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

    @property
    def window(self) -> tuple[date, date]:
        """[start, end) of the period the generated notes cover."""
        end = self.reference_date
        return end.replace(year=end.year - self.window_years), end

    @property
    def synthetic_dir(self) -> Path:
        return self.data_dir / "synthetic"

    @property
    def cohort_csv_dir(self) -> Path:
        return self.synthetic_dir / "csv"

    @property
    def raw_synthea_csv_dir(self) -> Path:
        return self.tools_dir / "synthea" / "output" / "csv"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
