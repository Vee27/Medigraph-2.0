"""Render deterministic clinical notes from the cohort's Synthea records.

Outputs (under ``data/synthetic``):

* ``notes/<MRN>/<date>_<doc_type>.txt`` - progress notes, discharge summaries and
  medication reconciliation notes.
* ``manifests/facts.jsonl`` - every clinical statement written into a note, with its
  exact character span, what the text asserts, and whether it agrees with the record.
* ``manifests/perturbations.jsonl`` - injected documentation defects (P1-P8) with the
  behaviour a correct system should show.
* ``manifests/documents.jsonl``, ``manifests/patients.jsonl``, ``manifests/summary.json``.

Facts describe what the *text* states (the extraction target). The ground truth for
question answering stays in the Synthea CSVs, so systems are never graded against the
notes they read.

Everything is derived from the inputs plus a seeded RNG per note, so re-running produces
byte-identical files.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from medigraph.data import perturb
from medigraph.data.synthea import (
    NOTE_CLASSES,
    Condition,
    MedEpisode,
    Row,
    Tables,
    clean,
    clinical_conditions,
    is_true_stop,
    load_tables,
    medication_episodes,
    to_date,
)
from medigraph.data.vocab import LABS, NEGATION_CONDITIONS, NEGATION_DRUGS, VITALS, LabSpec

MAX_PROGRESS_NOTES = 12
MAX_DISCHARGE_SUMMARIES = 4
STALE_LIST_MAX_DAYS = 540  # P1 only resurrects drugs stopped within this many days

TITLES = {
    "progress_note": "PROGRESS NOTE",
    "discharge_summary": "DISCHARGE SUMMARY",
    "med_reconciliation": "MEDICATION RECONCILIATION",
}
MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)  # fmt: skip
DATE_STYLES = ("iso", "us", "long")


# ---------------------------------------------------------------- output records


@dataclass
class Fact:
    kind: str  # medication | condition | lab | allergy | encounter
    statement: str  # active | started | stopped | changed | diagnosed | negated | result | no_known_allergies | admitted
    name: str  # canonical concept name
    code: str | None  # RxNorm / SNOMED / LOINC code when the concept is in the record
    surface: str  # exact text at [char_start, char_end)
    char_start: int
    char_end: int
    strength: str | None = None
    value: float | None = None
    unit: str | None = None
    valid_from: str | None = None  # ISO date the text asserts (resolved for relative dates)
    valid_to: str | None = None
    date_text: str | None = None  # the date as written
    date_granularity_days: int | None = None  # 0 exact ... 31 month, 365 year
    truth_consistent: bool = True
    perturbations: list[str] = field(default_factory=list)
    fact_id: str = ""
    doc_id: str = ""
    patient_id: str = ""


@dataclass
class Perturbation:
    type: str
    fact_indexes: list[int]
    detail: dict
    perturbation_id: str = ""
    doc_id: str = ""
    patient_id: str = ""


@dataclass
class Document:
    doc_id: str
    patient_id: str
    mrn: str
    doc_type: str
    authored_at: date
    encounter_id: str
    path: str
    text: str
    facts: list[Fact]
    perturbations: list[Perturbation]


class NoteBuilder:
    """Accumulates note text while recording the character span of every fact."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._pos = 0
        self.facts: list[Fact] = []
        self.perturbations: list[Perturbation] = []

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def add(self, text: str) -> None:
        self._parts.append(text)
        self._pos += len(text)

    def line(self, text: str = "") -> None:
        self.add(text + "\n")

    def mention(self, surface: str, **fields) -> int:
        start = self._pos
        self.add(surface)
        self.facts.append(Fact(surface=surface, char_start=start, char_end=self._pos, **fields))
        return len(self.facts) - 1

    def perturb(self, code: str, fact_indexes: list[int], **detail) -> None:
        self.perturbations.append(Perturbation(code, fact_indexes, detail))
        if not perturb.CATALOGUE[code].truth_consistent:
            for i in fact_indexes:
                self.facts[i].truth_consistent = False


# ---------------------------------------------------------------- patient records


@dataclass(frozen=True)
class Allergy:
    code: str
    name: str
    category: str
    start: date
    stop: date | None
    reaction: str | None

    def active_on(self, day: date) -> bool:
        return self.start <= day and (self.stop is None or day < self.stop)


@dataclass(frozen=True)
class LabResult:
    spec: LabSpec
    value: float
    day: date


@dataclass
class PatientRecord:
    patient_id: str
    mrn: str
    name: str
    birthdate: str
    episodes: list[MedEpisode]
    conditions: list[Condition]
    allergies: list[Allergy]
    labs: dict[str, list[LabResult]]  # by encounter id
    vitals: dict[str, dict[str, str]]  # by encounter id
    encounters: list[Row]  # note-worthy encounters inside the window, by start


def mrn_for(patient_id: str) -> str:
    return "MRN" + str(int(hashlib.sha256(patient_id.encode()).hexdigest()[:12], 16) % 10**7).zfill(7)


def build_records(tables: Tables, window_start: date, window_end: date) -> list[PatientRecord]:
    meds = tables.by_patient("medications")
    conditions = tables.by_patient("conditions")
    allergies = tables.by_patient("allergies")
    encounters = tables.by_patient("encounters")
    observations = tables.by_patient("observations")

    records = []
    for p in sorted(tables.patients, key=lambda r: r["Id"]):
        pid = p["Id"]
        algs = []
        for a in allergies.get(pid, []):
            if a["DESCRIPTION"].startswith("Allergic disposition"):
                continue
            start = to_date(a["START"])
            assert start is not None
            reaction = clean(a["DESCRIPTION1"]).lower() if a["DESCRIPTION1"] else None
            algs.append(Allergy(a["CODE"], clean(a["DESCRIPTION"]), a["CATEGORY"], start, to_date(a["STOP"]), reaction))
        labs: dict[str, list[LabResult]] = defaultdict(list)
        vitals: dict[str, dict[str, str]] = defaultdict(dict)
        for o in observations.get(pid, []):
            day = to_date(o["DATE"])
            assert day is not None
            if o["CODE"] in LABS and o["TYPE"] == "numeric":
                labs[o["ENCOUNTER"]].append(LabResult(LABS[o["CODE"]], float(o["VALUE"]), day))
            elif o["CODE"] in VITALS:
                vitals[o["ENCOUNTER"]][VITALS[o["CODE"]]] = o["VALUE"]
        encs = sorted(
            (
                e
                for e in encounters.get(pid, [])
                if e["ENCOUNTERCLASS"] in NOTE_CLASSES
                and window_start <= (to_date(e["START"]) or window_start) < window_end
            ),
            key=lambda e: (e["START"], e["Id"]),
        )
        records.append(
            PatientRecord(
                patient_id=pid,
                mrn=mrn_for(pid),
                name=f"{re.sub(r'\d+', '', p['FIRST'])} {re.sub(r'\d+', '', p['LAST'])}",
                birthdate=p["BIRTHDATE"],
                episodes=medication_episodes(meds.get(pid, [])),
                conditions=clinical_conditions(conditions.get(pid, [])),
                allergies=sorted(algs, key=lambda a: (a.start, a.name)),
                labs={k: sorted(v, key=lambda r: list(LABS).index(r.spec.code)) for k, v in labs.items()},
                vitals=dict(vitals),
                encounters=encs,
            )
        )
    return records


# ---------------------------------------------------------------- document planning


@dataclass(frozen=True)
class PlannedDoc:
    doc_type: str
    day: date
    encounter: Row
    order: int


_CLASS_PRIORITY = {"inpatient": 0, "wellness": 1, "ambulatory": 2, "outpatient": 3, "urgentcare": 4, "emergency": 5}


def _evenly(items: list, k: int) -> list:
    n = len(items)
    if n <= k:
        return items
    return [items[round(i * (n - 1) / (k - 1))] for i in range(k)]


def plan_documents(rec: PatientRecord) -> list[PlannedDoc]:
    by_day: dict[date, Row] = {}
    for e in rec.encounters:
        day = to_date(e["START"])
        assert day is not None
        best = by_day.get(day)
        if best is None or _CLASS_PRIORITY[e["ENCOUNTERCLASS"]] < _CLASS_PRIORITY[best["ENCOUNTERCLASS"]]:
            by_day[day] = e
    ordered = [by_day[d] for d in sorted(by_day)]
    inpatient = _evenly([e for e in ordered if e["ENCOUNTERCLASS"] == "inpatient"], MAX_DISCHARGE_SUMMARIES)
    visits = _evenly([e for e in ordered if e["ENCOUNTERCLASS"] != "inpatient"], MAX_PROGRESS_NOTES)

    docs = []
    for e in inpatient:
        discharge = to_date(e["STOP"]) or to_date(e["START"])
        assert discharge is not None
        docs.append(PlannedDoc("discharge_summary", discharge, e, 0))
    medrec_years: set[int] = set()
    for e in visits:
        day = to_date(e["START"])
        assert day is not None
        docs.append(PlannedDoc("progress_note", day, e, 0))
        if e["ENCOUNTERCLASS"] == "wellness" and day.year not in medrec_years:
            medrec_years.add(day.year)
            docs.append(PlannedDoc("med_reconciliation", day, e, 1))
    return sorted(docs, key=lambda d: (d.day, d.order, d.doc_type))


# ---------------------------------------------------------------- rendering


@dataclass
class History:
    """What earlier notes for this patient have documented."""

    prev_day: date | None = None
    last_medrec_day: date | None = None
    documented_stops: dict[str, tuple[str, date, str | None]] = field(default_factory=dict)
    pending_discharges: list[tuple[date, date, str, str, str]] = field(default_factory=list)


class Renderer:
    def __init__(self, rec: PatientRecord, plan: PlannedDoc, history: History, rng: random.Random):
        self.rec, self.plan, self.history, self.rng = rec, plan, history, rng
        self.day = plan.day
        self.b = NoteBuilder()
        self.date_style = rng.choice(DATE_STYLES)
        self.events: list[tuple] = []  # state updates applied once the doc_id is known

    # -- small helpers
    def fmt(self, day: date) -> str:
        if self.date_style == "iso":
            return day.isoformat()
        if self.date_style == "us":
            return f"{day.month:02d}/{day.day:02d}/{day.year}"
        return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"

    def when(self, event_day: date, allow_relative: bool = True) -> tuple[str, str, int, bool]:
        """Returns (phrase, date_text, granularity_days, is_relative)."""
        if allow_relative and perturb.chance(self.rng, "P2"):
            text, granularity = perturb.relative_date(event_day, self.day)
            return text, text, granularity, True
        text = self.fmt(event_day)
        return f"on {text}", text, 0, False

    def header(self) -> None:
        b, rec = self.b, self.rec
        b.line(TITLES[self.plan.doc_type])
        b.line(f"Patient: {rec.name}    MRN: {rec.mrn}    DOB: {rec.birthdate}")
        b.line(f"Date: {self.fmt(self.day)}")
        b.line("Author: attending clinician (synthetic record)")
        b.line()

    def med(self, ep: MedEpisode, statement: str, *, synonyms: bool = True, wrong_dose: bool = False, **fields) -> int:
        surface = ep.drug
        alt = perturb.synonym(self.rng, ep.drug) if synonyms and perturb.chance(self.rng, "P3") else None
        if alt:
            surface = alt
        idx = self.b.mention(surface, kind="medication", statement=statement, name=ep.drug, code=ep.code, **fields)
        if alt:
            self.b.perturb("P3", [idx], generic=ep.drug, surface=alt)
        strength = ep.strength
        if wrong_dose and strength and perturb.chance(self.rng, "P4"):
            wrong = perturb.wrong_strength(self.rng, strength)
            if wrong:
                self.b.perturb("P4", [idx], stated=wrong, prescribed=strength)
                strength = wrong
        self.b.facts[idx].strength = strength
        if strength:
            self.b.add(f" {strength}")
        return idx

    def dated(self, idx: int, event_day: date, field_name: str, *, allow_relative: bool = True) -> str:
        phrase, date_text, granularity, relative = self.when(event_day, allow_relative)
        fact = self.b.facts[idx]
        setattr(fact, field_name, event_day.isoformat())
        fact.date_text, fact.date_granularity_days = date_text, granularity
        if relative:
            self.b.perturb(
                "P2", [idx], date_text=date_text, anchor=self.day.isoformat(),
                true_date=event_day.isoformat(), granularity_days=granularity,
            )  # fmt: skip
        return phrase

    def active_episodes(self, day: date) -> list[MedEpisode]:
        return sorted((e for e in self.rec.episodes if e.active_on(day)), key=lambda e: (e.drug, e.code))

    def true_stops_between(self, after: date | None, until: date) -> list[MedEpisode]:
        return [
            e
            for e in self.rec.episodes
            if e.stop and (after is None or after < e.stop) and e.stop <= until and is_true_stop(e, self.rec.episodes)
        ]

    # -- sections
    def problem_list(self, title: str) -> None:
        b = self.b
        b.line(title)
        active = [c for c in self.rec.conditions if c.active_on(self.day)]
        if not active:
            b.line("- No active problems documented.")
        for c in active:
            b.add("- ")
            idx = b.mention(c.name, kind="condition", statement="active", name=c.name, code=c.code)
            since = f"{MONTHS[c.start.month - 1]} {c.start.year}"
            fact = b.facts[idx]
            fact.valid_from, fact.date_text, fact.date_granularity_days = c.start.isoformat(), since, 31
            b.line(f" (since {since})")
        b.line()

    def med_list(self, title: str, day: date, *, stale: bool, wrong_dose: bool) -> None:
        b = self.b
        entries: list[tuple[str, MedEpisode, bool]] = [(e.drug, e, False) for e in self.active_episodes(day)]
        stale_source: str | None = None  # doc_id of the note that documented the stop
        if stale:
            active_drugs = {e.drug for _, e, _ in entries}
            candidates = sorted(
                (drug, info)
                for drug, info in self.history.documented_stops.items()
                if drug not in active_drugs and info[1] < day and (day - info[1]).days <= STALE_LIST_MAX_DAYS
            )
            if candidates and perturb.chance(self.rng, "P1"):
                drug, (stale_source, stopped_on, _) = self.rng.choice(candidates)
                ep = next(e for e in self.rec.episodes if e.drug == drug and e.stop == stopped_on)
                entries.append((drug, ep, True))
        b.line(title)
        if not entries:
            b.line("- None")
        for _, ep, is_stale in sorted(entries, key=lambda t: (t[0], t[1].code)):
            b.add("- ")
            idx = self.med(ep, "active", wrong_dose=wrong_dose and not is_stale)
            if is_stale:
                assert ep.stop is not None
                b.perturb(
                    "P1", [idx], drug=ep.drug, stopped_on=ep.stop.isoformat(),
                    stop_documented_in=stale_source,
                )  # fmt: skip
            b.line()
        b.line()

    def allergies(self, title: str) -> None:
        b = self.b
        active = [a for a in self.rec.allergies if a.active_on(self.day)]
        b.add(f"{title} ")
        nka = self.rng.choice(("No known allergies", "NKA"))
        if not active:
            b.mention(nka, kind="allergy", statement="no_known_allergies", name="no known allergies", code=None)
        elif perturb.chance(self.rng, "P6"):
            idx = b.mention(nka, kind="allergy", statement="no_known_allergies", name="no known allergies", code=None)
            b.perturb("P6", [idx], documented=[a.name for a in active])
        else:
            for i, a in enumerate(active):
                if i:
                    b.add(", ")
                b.mention(a.name, kind="allergy", statement="active", name=a.name, code=a.code)
                if a.reaction:
                    b.add(f" ({a.reaction})")
        b.line()
        b.line()

    def labs(self) -> None:
        results = self.rec.labs.get(self.plan.encounter["Id"], [])
        if not results:
            return
        b = self.b
        b.line(f"{self.rng.choice(('Labs', 'Results', 'Laboratory'))} ({self.fmt(self.day)}):")
        for r in results:
            b.add("- ")
            idx = b.mention(r.spec.name, kind="lab", statement="result", name=r.spec.name, code=r.spec.code)
            value, unit, decimals = r.value, r.spec.unit, r.spec.decimals
            alt = perturb.to_alt_unit(r.spec, r.value) if perturb.chance(self.rng, "P8") else None
            if alt:
                value, unit, decimals = alt[0], alt[1], 1
            shown = f"{value:.{decimals}f}"
            fact = b.facts[idx]
            fact.value, fact.unit, fact.valid_from = float(shown), unit, self.day.isoformat()
            if alt:
                b.perturb(
                    "P8", [idx], value=float(shown), unit=unit,
                    canonical_value=round(r.value, 4), canonical_unit=r.spec.unit,
                )  # fmt: skip
            b.line(f": {shown} {unit}")
        b.line()

    def vitals(self) -> None:
        v = self.rec.vitals.get(self.plan.encounter["Id"], {})
        parts = []
        if "systolic" in v and "diastolic" in v:
            parts.append(f"BP {float(v['systolic']):.0f}/{float(v['diastolic']):.0f} mmHg")
        if "heart_rate" in v:
            parts.append(f"HR {float(v['heart_rate']):.0f} bpm")
        if "weight" in v:
            parts.append(f"weight {float(v['weight']):.1f} kg")
        if parts:
            self.b.line("Vitals: " + ", ".join(parts))
            self.b.line()

    def changes(self, title: str) -> None:
        """Medication and diagnosis changes since the previous note."""
        b, prev = self.b, self.history.prev_day
        b.line(title)
        if prev is None:
            b.line("- First note in this record; see lists below.")
            b.line()
            return
        wrote = False
        for ep in sorted(self.rec.episodes, key=lambda e: (e.start, e.drug)):
            if not (prev < ep.start <= self.day):
                continue
            b.add("- ")
            if ep.changed_from:
                old = next((e for e in self.rec.episodes if e.code == ep.changed_from), None)
                b.add("Changed ")
                idx = self.med(ep, "changed")
                phrase = self.dated(idx, ep.start, "valid_from")
                b.line(f" (previously {old.strength if old else 'another strength'}) {phrase}.")
            else:
                b.add(self.rng.choice(("Started ", "Initiated ")))
                idx = self.med(ep, "started")
                b.line(f" {self.dated(idx, ep.start, 'valid_from')}.")
            wrote = True
        for ep in self.true_stops_between(prev, self.day):
            assert ep.stop is not None
            b.add("- " + self.rng.choice(("Discontinued ", "Stopped ")))
            idx = self.med(ep, "stopped")
            b.line(f" {self.dated(idx, ep.stop, 'valid_to')}.")
            self.events.append(("stop", ep.drug, ep.stop, ep.code))
            wrote = True
        for c in self.rec.conditions:
            if prev < c.start <= self.day:
                b.add("- New diagnosis: ")
                idx = b.mention(c.name, kind="condition", statement="diagnosed", name=c.name, code=c.code)
                b.line(f" {self.dated(idx, c.start, 'valid_from')}.")
                wrote = True
        if not wrote:
            b.line("- No medication or diagnosis changes since the last note.")
        b.line()

    def hospital_follow_up(self) -> None:
        pending = [d for d in self.history.pending_discharges if d[1] < self.day]
        if not pending:
            return
        b = self.b
        for admit, discharge, reason, code, doc_id in pending:
            stated = admit
            shift = 0
            if perturb.chance(self.rng, "P7"):
                stated, shift = perturb.shifted_date(self.rng, admit)
                if stated >= discharge:  # keep the error plausible: admission before discharge
                    stated, shift = admit - (stated - admit), -shift
            b.add("Hospital follow-up: admitted for ")
            idx = b.mention(reason, kind="encounter", statement="admitted", name=reason, code=code)
            phrase = self.dated(idx, stated, "valid_from", allow_relative=False)
            b.line(f" {phrase}, discharged {self.fmt(discharge)}.")
            fact = b.facts[idx]
            fact.valid_to = discharge.isoformat()
            if shift:
                b.perturb(
                    "P7", [idx], stated=stated.isoformat(), true_date=admit.isoformat(),
                    shift_days=shift, discharge_summary=doc_id,
                )  # fmt: skip
            self.events.append(("restated", admit))
        b.line()

    def negatives(self) -> None:
        if not perturb.chance(self.rng, "P5"):
            return
        taken = {e.drug for e in self.rec.episodes}
        known = " ".join(c.name.lower() for c in self.rec.conditions)
        drugs = [d for d in NEGATION_DRUGS if d not in taken]
        conds = [c for c in NEGATION_CONDITIONS if c not in known]
        options = [("medication", d) for d in drugs] + [("condition", c) for c in conds]
        if not options:
            return
        kind, name = self.rng.choice(options)
        b = self.b
        if kind == "medication":
            b.add(self.rng.choice(("Patient denies taking ", "Not currently taking ")))
        else:
            b.add(self.rng.choice(("No history of ", "Denies any history of ")))
        idx = b.mention(name, kind=kind, statement="negated", name=name, code=None)
        b.line(".")
        b.line()
        b.perturb("P5", [idx], negated=name, kind=kind)

    # -- documents
    def progress_note(self) -> None:
        e, rng, b = self.plan.encounter, self.rng, self.b
        self.header()
        b.line(f"Visit: {clean(e['DESCRIPTION'])}")
        if e["REASONDESCRIPTION"]:
            b.line(f"Reason for visit: {clean(e['REASONDESCRIPTION'])}")
        b.line()
        self.vitals()
        self.hospital_follow_up()
        self.changes(rng.choice(("Interval changes:", "Changes since last visit:", "Plan / changes:")))
        self.problem_list(rng.choice(("Assessment:", "Problem list:", "Active problems:")))
        self.med_list(rng.choice(("Current medications:", "Medications:", "Med list:")), self.day, stale=True, wrong_dose=True)
        self.allergies(rng.choice(("Allergies:", "ALLERGIES:")))
        self.labs()
        self.negatives()

    def discharge_summary(self) -> None:
        e, rng, b = self.plan.encounter, self.rng, self.b
        admit, discharge = to_date(e["START"]), self.day
        assert admit is not None
        reason = clean(e["REASONDESCRIPTION"]) if e["REASONDESCRIPTION"] else "inpatient evaluation"
        code = e["REASONCODE"] or e["CODE"]
        self.header()
        b.line(f"Admission date: {self.fmt(admit)}")
        b.line(f"Discharge date: {self.fmt(discharge)}")
        b.add("Reason for admission: ")
        idx = b.mention(reason, kind="encounter", statement="admitted", name=reason, code=code)
        fact = b.facts[idx]
        fact.valid_from, fact.valid_to = admit.isoformat(), discharge.isoformat()
        fact.date_text, fact.date_granularity_days = self.fmt(admit), 0
        b.line()
        b.line()
        mid = reason[:1].lower() + reason[1:]  # keep acronyms such as "ST" intact
        b.line("Hospital course: " + rng.choice((
            f"Admitted for {mid}. Treated per protocol and discharged in stable condition.",
            f"The patient was managed for {mid} and improved; discharged home.",
            f"Hospital stay for {mid} was uncomplicated. Discharged in good condition.",
        )))  # fmt: skip
        b.line()
        self.problem_list("Discharge diagnoses:")
        started = [ep for ep in self.rec.episodes if admit <= ep.start <= discharge]
        if started:
            b.line("Medications started during admission:")
            for ep in sorted(started, key=lambda x: (x.drug, x.code)):
                b.add("- ")
                self.med(ep, "started")  # date implied by the admission window, not stated
                b.line()
            b.line()
        self.med_list("Discharge medications:", discharge, stale=False, wrong_dose=True)
        self.allergies("Allergies:")
        b.line("Follow-up: primary care within 2 weeks.")
        self.events.append(("discharge", admit, discharge, reason, code))

    def med_reconciliation(self) -> None:
        b = self.b
        self.header()
        b.line(f"Medication list reviewed with the patient on {self.fmt(self.day)}.")
        b.line()
        b.line("Continue:")
        active = self.active_episodes(self.day)
        if not active:
            b.line("- None")
        for ep in active:
            b.add("- ")
            self.med(ep, "active")
            b.line(" - taking as prescribed")
        b.line()
        stops = self.true_stops_between(self.history.last_medrec_day, self.day)
        b.line("Stopped since last review:")
        if not stops:
            b.line("- None")
        for ep in stops:
            assert ep.stop is not None
            b.add("- ")
            idx = self.med(ep, "stopped")
            b.line(f" - discontinued {self.dated(idx, ep.stop, 'valid_to')}")
            self.events.append(("stop", ep.drug, ep.stop, ep.code))
        b.line()
        self.allergies("Allergies reviewed:")
        self.events.append(("medrec",))

    def render(self) -> NoteBuilder:
        getattr(self, self.plan.doc_type)()
        return self.b


def _rng(seed: int, *parts: str) -> random.Random:
    return random.Random(":".join([str(seed), *parts]))


def generate_patient(rec: PatientRecord, seed: int) -> list[Document]:
    history = History()
    docs: list[Document] = []
    paths: set[str] = set()
    for plan in plan_documents(rec):
        key = f"{plan.day.isoformat()}_{plan.doc_type}"
        renderer = Renderer(rec, plan, history, _rng(seed, rec.patient_id, key))
        builder = renderer.render()
        text = builder.text.rstrip("\n") + "\n"  # one trailing newline; fact spans are unaffected
        doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]
        path = f"notes/{rec.mrn}/{key}.txt"
        n = 2
        while path in paths:
            path = f"notes/{rec.mrn}/{key}_{n}.txt"
            n += 1
        paths.add(path)
        for i, p in enumerate(builder.perturbations):
            p.perturbation_id, p.doc_id, p.patient_id = f"{doc_id}-p{i:02d}", doc_id, rec.patient_id
            for fi in p.fact_indexes:
                builder.facts[fi].perturbations.append(p.perturbation_id)
        for i, f in enumerate(builder.facts):
            f.fact_id, f.doc_id, f.patient_id = f"{doc_id}-f{i:03d}", doc_id, rec.patient_id
        docs.append(
            Document(
                doc_id, rec.patient_id, rec.mrn, plan.doc_type, plan.day, plan.encounter["Id"],
                path, text, builder.facts, builder.perturbations,
            )
        )  # fmt: skip

        # Update what later notes "know" from this one.
        history.prev_day = plan.day
        for event in renderer.events:
            if event[0] == "stop":
                _, drug, stopped_on, code = event
                history.documented_stops[drug] = (doc_id, stopped_on, code)
            elif event[0] == "discharge":
                _, admit, discharge, reason, code = event
                history.pending_discharges.append((admit, discharge, reason, code, doc_id))
            elif event[0] == "restated":
                history.pending_discharges = [d for d in history.pending_discharges if d[0] != event[1]]
            elif event[0] == "medrec":
                history.last_medrec_day = plan.day
    return docs


# ---------------------------------------------------------------- writing


def _jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def generate_notes(
    csv_dir: Path, out_dir: Path, *, seed: int, window_start: date, window_end: date
) -> dict:
    """Render all notes and manifests into `out_dir`; returns the summary."""
    records = build_records(load_tables(csv_dir), window_start, window_end)
    docs = [d for rec in records for d in generate_patient(rec, seed)]

    for sub in ("notes", "manifests"):
        shutil.rmtree(out_dir / sub, ignore_errors=True)
    (out_dir / "manifests").mkdir(parents=True)
    for d in docs:
        path = out_dir / d.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(d.text, encoding="utf-8", newline="\n")

    manifests = out_dir / "manifests"
    _jsonl(manifests / "patients.jsonl", [
        {
            "patient_id": r.patient_id, "mrn": r.mrn, "name": r.name, "birthdate": r.birthdate,
            "n_documents": sum(1 for d in docs if d.patient_id == r.patient_id),
        }
        for r in records
    ])  # fmt: skip
    _jsonl(manifests / "documents.jsonl", [
        {
            "doc_id": d.doc_id, "patient_id": d.patient_id, "mrn": d.mrn, "doc_type": d.doc_type,
            "authored_at": d.authored_at.isoformat(), "encounter_id": d.encounter_id,
            "path": d.path, "sha256": hashlib.sha256(d.text.encode()).hexdigest(),
            "n_chars": len(d.text),
        }
        for d in docs
    ])  # fmt: skip
    _jsonl(manifests / "facts.jsonl", [asdict(f) for d in docs for f in d.facts])
    _jsonl(
        manifests / "perturbations.jsonl",
        [
            {**asdict(p), "name": perturb.CATALOGUE[p.type].name, "expected": perturb.CATALOGUE[p.type].expected}
            for d in docs
            for p in d.perturbations
        ],
    )

    summary = {
        "seed": seed,
        "window": [window_start.isoformat(), window_end.isoformat()],
        "patients": len(records),
        "documents": dict(sorted(Counter(d.doc_type for d in docs).items())),
        "facts": dict(sorted(Counter(f.kind for d in docs for f in d.facts).items())),
        "perturbations": dict(sorted(Counter(p.type for d in docs for p in d.perturbations).items())),
        "documents_with_perturbations": sum(1 for d in docs if d.perturbations),
        "documents_with_conflicts": sum(
            1 for d in docs if any(not perturb.CATALOGUE[p.type].truth_consistent for p in d.perturbations)
        ),
    }
    (manifests / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
