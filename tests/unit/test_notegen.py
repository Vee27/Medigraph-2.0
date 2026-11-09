"""The committed synthetic dataset must be exactly what the code generates, and its gold
manifests must be internally consistent with the notes and with the Synthea record."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from medigraph.config import Settings
from medigraph.data import perturb
from medigraph.data.notegen import generate_notes
from medigraph.data.synthea import load_tables, medication_episodes


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for sub in ("notes", "manifests")
        for p in sorted((root / sub).rglob("*"))
        if p.is_file()
    }


def test_committed_data_is_byte_identical_to_generator(
    settings: Settings, synthetic_dir: Path, tmp_path: Path
) -> None:
    start, end = settings.window
    generate_notes(settings.cohort_csv_dir, tmp_path, seed=settings.seed, window_start=start, window_end=end)
    expected, actual = _files(synthetic_dir), _files(tmp_path)
    assert sorted(actual) == sorted(expected), "generated file set differs from data/synthetic"
    changed = [name for name in expected if expected[name] != actual[name]]
    assert not changed, f"regenerate with `make data`; differing files: {changed[:5]}"


def test_fact_spans_match_note_text(synthetic_dir: Path, documents: dict, facts: list[dict]) -> None:
    texts = {
        doc_id: (synthetic_dir / d["path"]).read_text(encoding="utf-8") for doc_id, d in documents.items()
    }
    for f in facts:
        assert texts[f["doc_id"]][f["char_start"] : f["char_end"]] == f["surface"], f["fact_id"]


def test_every_perturbation_type_is_present(perturbations: list[dict]) -> None:
    counts = Counter(p["type"] for p in perturbations)
    assert set(counts) == set(perturb.CATALOGUE)
    assert min(counts.values()) >= 5, counts


def test_conflicting_perturbations_mark_facts_inconsistent(
    facts: list[dict], perturbations: list[dict]
) -> None:
    conflict_ids = {
        p["perturbation_id"] for p in perturbations if not perturb.CATALOGUE[p["type"]].truth_consistent
    }
    for f in facts:
        assert f["truth_consistent"] == (not conflict_ids & set(f["perturbations"])), f["fact_id"]


def test_consistent_active_medications_match_the_record(
    settings: Settings, documents: dict, facts: list[dict]
) -> None:
    """Every truthful 'active' medication mention is backed by an episode active that day."""
    tables = load_tables(settings.cohort_csv_dir)
    active_by_patient: dict[str, list] = defaultdict(list)
    for e in medication_episodes(tables.medications):
        active_by_patient[e.patient_id].append(e)
    for f in facts:
        if f["kind"] != "medication" or f["statement"] != "active" or not f["truth_consistent"]:
            continue
        day = date.fromisoformat(documents[f["doc_id"]]["authored_at"])
        assert any(e.code == f["code"] and e.active_on(day) for e in active_by_patient[f["patient_id"]]), f[
            "fact_id"
        ]


def test_stale_list_points_to_documented_stop(facts: list[dict], perturbations: list[dict]) -> None:
    stopped = {
        (f["doc_id"], f["name"]) for f in facts if f["kind"] == "medication" and f["statement"] == "stopped"
    }
    for p in perturbations:
        if p["type"] == "P1":
            assert (p["detail"]["stop_documented_in"], p["detail"]["drug"]) in stopped, p["perturbation_id"]
