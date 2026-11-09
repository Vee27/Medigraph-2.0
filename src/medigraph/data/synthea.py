"""Load Synthea CSV exports, derive medication episodes, and select the study cohort.

Synthea writes every renewal of a chronic prescription as its own row (stop + start of
the same code on the same day). Ground truth therefore works on *episodes*: renewals of
the same RxNorm code are merged, and a real discontinuation is an episode end that is
not followed by another renewal.
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Encounter classes that produce clinical notes.
NOTE_CLASSES = frozenset(
    {"ambulatory", "outpatient", "wellness", "urgentcare", "emergency", "inpatient"}
)

# Columns kept in the committed cohort CSVs (identifiers like SSN, address are dropped).
COHORT_COLUMNS: dict[str, list[str]] = {
    "patients": [
        "Id", "BIRTHDATE", "DEATHDATE", "PREFIX", "FIRST", "LAST", "GENDER",
        "RACE", "ETHNICITY", "CITY", "STATE",
    ],
    "encounters": [
        "Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS", "CODE", "DESCRIPTION",
        "REASONCODE", "REASONDESCRIPTION",
    ],
    "conditions": ["START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM", "CODE", "DESCRIPTION"],
    "medications": [
        "START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION",
        "REASONCODE", "REASONDESCRIPTION",
    ],
    "observations": [
        "DATE", "PATIENT", "ENCOUNTER", "CATEGORY", "CODE", "DESCRIPTION", "VALUE", "UNITS", "TYPE",
    ],
    "allergies": [
        "START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "SYSTEM", "DESCRIPTION", "TYPE",
        "CATEGORY", "REACTION1", "DESCRIPTION1", "SEVERITY1",
    ],
}  # fmt: skip

Row = dict[str, str]


@dataclass(frozen=True)
class Tables:
    patients: list[Row]
    encounters: list[Row]
    conditions: list[Row]
    medications: list[Row]
    observations: list[Row]
    allergies: list[Row]

    def by_patient(self, table: str) -> dict[str, list[Row]]:
        grouped: dict[str, list[Row]] = defaultdict(list)
        for row in getattr(self, table):
            grouped[row["PATIENT"]].append(row)
        return grouped


def read_table(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_tables(csv_dir: Path) -> Tables:
    return Tables(**{name: read_table(csv_dir / f"{name}.csv") for name in COHORT_COLUMNS})


def to_date(value: str) -> date | None:
    """Synthea uses ISO dates (conditions) and ISO datetimes (encounters, medications)."""
    return date.fromisoformat(value[:10]) if value else None


# ---------------------------------------------------------------- medication parsing

_PREFIX_NOISE = re.compile(
    r"^(?:NDA\d+\s+|Abuse-Deterrent\s+|\d+(?:\.\d+)?\s+(?:HR|ML|ACTUAT|Day)\s+)+",
    re.IGNORECASE,
)
_STRENGTH = re.compile(
    r"(\d+(?:\.\d+)?)\s*(MG|MCG|UNT|MEQ|ML)(?:/(ML|ACTUAT|HR))?", re.IGNORECASE
)


def parse_medication(description: str) -> tuple[str, str | None]:
    """Split an RxNorm display name into (canonical drug name, strength).

    >>> parse_medication("24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet")
    ('metformin hydrochloride', '500 mg')
    >>> parse_medication("Sacubitril 49 MG / Valsartan 51 MG Oral Tablet")
    ('sacubitril/valsartan', '49 mg/51 mg')
    """
    names, strengths = [], []
    for part in description.split(" / "):
        part = _PREFIX_NOISE.sub("", part.strip())
        name = re.split(r"\s\d|\s\[|\s\{", " " + part, maxsplit=1)[0].strip()
        name = re.sub(r"\s+", " ", name).lower()
        if name and name not in names:  # multi-phase packs repeat the same ingredient
            names.append(name)
        m = _STRENGTH.search(part)
        if m:
            unit = m.group(2).lower().replace("unt", "units")
            per = f"/{m.group(3).lower()}" if m.group(3) else ""
            strengths.append(f"{m.group(1)} {unit}{per}")
    return "/".join(names) or description.lower(), "/".join(strengths) or None


@dataclass(frozen=True)
class MedEpisode:
    """One continuous course of one RxNorm code (renewals merged)."""

    patient_id: str
    code: str
    drug: str
    strength: str | None
    description: str
    start: date
    stop: date | None  # None = still active at the end of the record
    start_encounter: str
    reason: str
    changed_from: str | None = field(default=None, compare=False)  # code of previous strength

    def active_on(self, day: date) -> bool:
        return self.start <= day and (self.stop is None or day < self.stop)


def medication_episodes(
    rows: Iterable[Row], *, merge_gap_days: int = 31, min_days: int = 14
) -> list[MedEpisode]:
    """Merge renewals into episodes, drop short procedural courses, link dose changes."""
    by_code: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        by_code[(r["PATIENT"], r["CODE"])].append(r)

    episodes: list[MedEpisode] = []
    gap = timedelta(days=merge_gap_days)
    for (patient_id, code), group in sorted(by_code.items()):
        group.sort(key=lambda r: (r["START"], r["STOP"] or "9999"))
        current: dict | None = None
        for r in group:
            start, stop = to_date(r["START"]), to_date(r["STOP"])
            assert start is not None
            if current and (current["stop"] is None or start <= current["stop"] + gap):
                if current["stop"] is not None:
                    current["stop"] = None if stop is None else max(current["stop"], stop)
                continue
            if current:
                episodes.append(_episode(patient_id, code, current))
            current = {"start": start, "stop": stop, "row": r}
        if current:
            episodes.append(_episode(patient_id, code, current))

    episodes = [
        e for e in episodes if e.stop is None or (e.stop - e.start).days >= min_days
    ]
    return _link_dose_changes(episodes, gap)


def _episode(patient_id: str, code: str, cur: dict) -> MedEpisode:
    r = cur["row"]
    drug, strength = parse_medication(r["DESCRIPTION"])
    return MedEpisode(
        patient_id=patient_id,
        code=code,
        drug=drug,
        strength=strength,
        description=r["DESCRIPTION"],
        start=cur["start"],
        stop=cur["stop"],
        start_encounter=r["ENCOUNTER"],
        reason=r["REASONDESCRIPTION"],
    )


def _link_dose_changes(episodes: list[MedEpisode], gap: timedelta) -> list[MedEpisode]:
    """Same drug, different strength, starting within `gap` of the previous stop."""
    out: list[MedEpisode] = []
    by_drug: dict[tuple[str, str], list[MedEpisode]] = defaultdict(list)
    for e in episodes:
        by_drug[(e.patient_id, e.drug)].append(e)
    for key in sorted(by_drug):
        seq = sorted(by_drug[key], key=lambda e: (e.start, e.code))
        for i, e in enumerate(seq):
            prev = next(
                (
                    p
                    for p in reversed(seq[:i])
                    if p.code != e.code and p.stop and abs((e.start - p.stop).days) <= gap.days
                ),
                None,
            )
            if prev is not None:
                e = MedEpisode(**{**e.__dict__, "changed_from": prev.code})
            out.append(e)
    return sorted(out, key=lambda e: (e.patient_id, e.start, e.drug, e.code))


def is_true_stop(episode: MedEpisode, episodes: list[MedEpisode]) -> bool:
    """A stop after which the patient is really off the drug.

    Not a true stop when another course of the same drug (e.g. a new strength) is active
    on the stop date or starts within 31 days after it.
    """
    stop = episode.stop
    if stop is None:
        return False
    return not any(
        o is not episode
        and o.patient_id == episode.patient_id
        and o.drug == episode.drug
        and (o.active_on(stop) or 0 <= (o.start - stop).days <= 31)
        for o in episodes
    )


# ---------------------------------------------------------------- conditions

_TAG = re.compile(r"\s*\([^()]*\)\s*$")
CONDITION_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "gingivitis", "dental", "tooth", "molar", "caries", "laceration", "sprain",
)  # fmt: skip
ACUTE_KEYWORDS: tuple[str, ...] = ("acute", "sepsis", "septic")
ACUTE_DEFAULT_DAYS = 30


def clean(description: str) -> str:
    """Strip trailing SNOMED semantic tags: 'Eggs (edible) (substance)' -> 'Eggs'."""
    while _TAG.search(description):
        description = _TAG.sub("", description)
    return re.sub(r"\s+", " ", description).strip()


@dataclass(frozen=True)
class Condition:
    patient_id: str
    code: str
    name: str
    start: date
    stop: date | None

    def active_on(self, day: date) -> bool:
        return self.start <= day and (self.stop is None or day < self.stop)


def clinical_conditions(rows: Iterable[Row]) -> list[Condition]:
    """Disorders that belong on a problem list, with Synthea's open-ended acute
    conditions closed after ACUTE_DEFAULT_DAYS.

    Synthea records some acute conditions (e.g. sepsis) without a stop date, which would
    keep them "active" for decades. Ground truth and generated notes both use this
    function, so they agree on the rule.
    """
    out = []
    for r in rows:
        desc = r["DESCRIPTION"]
        if "(disorder)" not in desc or any(k in desc.lower() for k in CONDITION_EXCLUDE_KEYWORDS):
            continue
        start = to_date(r["START"])
        assert start is not None
        stop = to_date(r["STOP"])
        name = clean(desc)
        if stop is None and any(k in name.lower() for k in ACUTE_KEYWORDS):
            stop = start + timedelta(days=ACUTE_DEFAULT_DAYS)
        out.append(Condition(r["PATIENT"], r["CODE"], name, start, stop))
    return sorted(out, key=lambda c: (c.patient_id, c.start, c.name))


# ---------------------------------------------------------------- cohort selection


def stable_rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def select_cohort(
    tables: Tables,
    *,
    size: int,
    seed: int,
    window_start: date,
    window_end: date,
    min_encounters: int = 8,
    max_encounters: int = 60,
    min_drugs: int = 3,
    max_drugs: int = 15,
    min_span_days: int = 730,
) -> list[str]:
    """Pick `size` patients whose window has enough, but not overwhelming, history.

    Criteria inside [window_start, window_end): a note-worthy encounter count between the
    bounds, at least two years between first and last encounter, between `min_drugs` and
    `max_drugs` distinct chronic drugs, and at least one real discontinuation. Eligible
    patients are ordered by a seeded hash so the choice is deterministic but not biased
    by Synthea's generation order.
    """
    encounters = tables.by_patient("encounters")
    meds = tables.by_patient("medications")
    eligible = []
    for p in tables.patients:
        pid = p["Id"]
        if p["DEATHDATE"]:
            continue
        days = sorted(
            d
            for e in encounters.get(pid, [])
            if e["ENCOUNTERCLASS"] in NOTE_CLASSES
            and (d := to_date(e["START"])) is not None
            and window_start <= d < window_end
        )
        if not (min_encounters <= len(days) <= max_encounters):
            continue
        if (days[-1] - days[0]).days < min_span_days:
            continue
        eps = [
            e
            for e in medication_episodes(meds.get(pid, []))
            if e.start < window_end and (e.stop is None or e.stop >= window_start)
        ]
        if not (min_drugs <= len({e.drug for e in eps}) <= max_drugs):
            continue
        if not any(
            e.stop and window_start <= e.stop < window_end and is_true_stop(e, eps) for e in eps
        ):
            continue
        eligible.append(pid)
    eligible.sort(key=lambda pid: stable_rank(seed, pid))
    if len(eligible) < size:
        raise ValueError(
            f"only {len(eligible)} eligible patients for a cohort of {size}; "
            "generate a larger Synthea population (SYNTHEA_POPULATION)"
        )
    return sorted(eligible[:size])


def write_cohort(
    tables: Tables,
    patient_ids: list[str],
    out_dir: Path,
    *,
    window_start: date,
    window_end: date,
    lab_codes: Iterable[str],
    vital_codes: Iterable[str],
) -> dict[str, int]:
    """Write the cohort's rows (selected columns, deterministic order) as CSVs."""
    keep = set(patient_ids)
    keep_obs = set(lab_codes) | set(vital_codes)

    def in_window(value: str) -> bool:
        d = to_date(value)
        return d is not None and window_start <= d < window_end

    selectors = {
        "patients": lambda r: r["Id"] in keep,
        "encounters": lambda r: r["PATIENT"] in keep and in_window(r["START"]),
        "conditions": lambda r: r["PATIENT"] in keep,
        "medications": lambda r: r["PATIENT"] in keep,
        "observations": lambda r: r["PATIENT"] in keep
        and r["CODE"] in keep_obs
        and in_window(r["DATE"]),
        "allergies": lambda r: r["PATIENT"] in keep,
    }
    sort_keys = {
        "patients": ("Id",),
        "encounters": ("PATIENT", "START", "Id"),
        "conditions": ("PATIENT", "START", "CODE", "ENCOUNTER"),
        "medications": ("PATIENT", "START", "CODE", "ENCOUNTER"),
        "observations": ("PATIENT", "DATE", "CODE", "ENCOUNTER"),
        "allergies": ("PATIENT", "START", "CODE"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, columns in COHORT_COLUMNS.items():
        rows = [r for r in getattr(tables, name) if selectors[name](r)]
        rows.sort(key=lambda r, k=sort_keys[name]: tuple(r[c] for c in k))
        with (out_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        counts[name] = len(rows)
    return counts
