"""Perturbation catalogue: realistic documentation defects injected into generated notes.

Each perturbation is logged in ``manifests/perturbations.jsonl`` with the gold behaviour a
correct system should show, so later milestones can score conflict detection, date
resolution and normalisation against known answers instead of judging free text.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from medigraph.data.vocab import DRUG_SYNONYMS, LabSpec


@dataclass(frozen=True)
class PerturbationType:
    code: str
    name: str
    expected: str  # gold behaviour for a correct system
    truth_consistent: bool  # does the perturbed statement still agree with the record?
    description: str


CATALOGUE: dict[str, PerturbationType] = {
    p.code: p
    for p in (
        PerturbationType(
            "P1", "stale_med_list", "flag_conflict", False,
            "Copy-forward medication list keeps a drug that an earlier note discontinued.",
        ),
        PerturbationType(
            "P2", "relative_date", "resolve_relative_date", True,
            "Event date written relative to the note date ('3 weeks ago').",
        ),
        PerturbationType(
            "P3", "drug_synonym", "map_synonym", True,
            "Drug written as a brand name or abbreviation instead of the generic name.",
        ),
        PerturbationType(
            "P4", "dose_discrepancy", "flag_conflict", False,
            "Medication listed with a strength that differs from the prescribed one.",
        ),
        PerturbationType(
            "P5", "negation", "no_positive_assertion", True,
            "Negated statement about a drug or condition the patient never had.",
        ),
        PerturbationType(
            "P6", "nkda_conflict", "flag_conflict", False,
            "Allergy section says NKDA although allergies are documented.",
        ),
        PerturbationType(
            "P7", "date_inconsistency", "flag_conflict", False,
            "Hospitalisation date restated with a shifted date in a later note.",
        ),
        PerturbationType(
            "P8", "lab_unit_swap", "normalize_units", True,
            "Lab value reported in an alternative unit (e.g. mmol/L instead of mg/dL).",
        ),
    )
}  # fmt: skip

# Per-opportunity probabilities. Each is applied where the perturbation is possible
# (e.g. P6 only for patients with documented allergies), with a seeded RNG per note.
RATES: dict[str, float] = {
    "P1": 0.35,  # per progress note with a previously documented discontinuation
    "P2": 0.30,  # per dated start/stop/diagnosis statement
    "P3": 0.20,  # per medication mention that has a known synonym
    "P4": 0.03,  # per medication-list entry with a strength
    "P5": 0.20,  # per progress note
    "P6": 0.25,  # per note of a patient with active allergies
    "P7": 0.60,  # per restated hospitalisation (rare: one per discharge)
    "P8": 0.15,  # per lab result whose test has an alternative unit
}


def chance(rng: random.Random, code: str) -> bool:
    return rng.random() < RATES[code]


def relative_date(event: date, anchor: date) -> tuple[str, int]:
    """Phrase `event` relative to `anchor`; returns (text, granularity in days)."""
    days = (anchor - event).days
    if days <= 0:
        return "today", 0
    if days < 14:
        return (f"{days} day{'s' if days != 1 else ''} ago", 1)
    if days < 70:
        weeks = round(days / 7)
        return f"{weeks} weeks ago", 7
    months = round(days / 30.44)
    if months < 24:
        return f"{months} months ago", 31
    years = round(days / 365.25)
    return f"{years} years ago", 365


def synonym(rng: random.Random, drug: str) -> str | None:
    options = DRUG_SYNONYMS.get(drug)
    return rng.choice(options) if options else None


def wrong_strength(rng: random.Random, strength: str) -> str | None:
    """Double or halve the first number of a strength such as '10 mg'."""
    number, _, unit = strength.partition(" ")
    if "/" in number or not unit:
        return None
    try:
        value = float(number)
    except ValueError:
        return None
    new = value * 2 if rng.random() < 0.5 else value / 2
    text = f"{new:g}"
    return None if text == number else f"{text} {unit}"


def shifted_date(rng: random.Random, day: date) -> tuple[date, int]:
    shift = rng.randint(5, 20) * rng.choice((-1, 1))
    return day + timedelta(days=shift), shift


def to_alt_unit(spec: LabSpec, value: float) -> tuple[float, str] | None:
    if spec.alt_unit is None or spec.alt_factor is None:
        return None
    return round(value * spec.alt_factor, 2), spec.alt_unit
