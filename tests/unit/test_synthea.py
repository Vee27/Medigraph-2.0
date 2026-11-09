from __future__ import annotations

from datetime import date

import pytest

from medigraph.data.synthea import (
    clinical_conditions,
    is_true_stop,
    medication_episodes,
    parse_medication,
)


def med(start: str, stop: str, code: str = "314076", desc: str = "lisinopril 10 MG Oral Tablet") -> dict:
    return {
        "PATIENT": "p1",
        "START": f"{start}T08:00:00Z",
        "STOP": f"{stop}T08:00:00Z" if stop else "",
        "CODE": code,
        "DESCRIPTION": desc,
        "ENCOUNTER": "e1",
        "REASONDESCRIPTION": "",
    }


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("lisinopril 10 MG Oral Tablet", ("lisinopril", "10 mg")),
        (
            "24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet",
            ("metformin hydrochloride", "500 mg"),
        ),
        (
            "NDA021457 200 ACTUAT Albuterol 0.09 MG/ACTUAT Metered Dose Inhaler",
            ("albuterol", "0.09 mg/actuat"),
        ),
        ("Sacubitril 49 MG / Valsartan 51 MG Oral Tablet", ("sacubitril/valsartan", "49 mg/51 mg")),
        ("1 ML Epoetin Alfa 4000 UNT/ML Injection [Epogen]", ("epoetin alfa", "4000 units/ml")),
    ],
)
def test_parse_medication(description: str, expected: tuple[str, str]) -> None:
    assert parse_medication(description) == expected


def test_renewals_merge_into_one_episode() -> None:
    rows = [med("2021-01-01", "2022-01-01"), med("2022-01-01", "2023-01-01"), med("2023-01-10", "2024-01-01")]
    (episode,) = medication_episodes(rows)
    assert (episode.start, episode.stop) == (date(2021, 1, 1), date(2024, 1, 1))


def test_open_ended_renewal_stays_active() -> None:
    (episode,) = medication_episodes([med("2021-01-01", "2022-01-01"), med("2022-01-01", "")])
    assert episode.stop is None
    assert episode.active_on(date(2030, 1, 1))


def test_short_procedural_courses_are_dropped() -> None:
    assert medication_episodes([med("2021-01-01", "2021-01-02")]) == []


def test_true_stop_vs_strength_change() -> None:
    old = med("2021-01-01", "2022-01-01")
    new = med("2022-01-05", "", code="314077", desc="lisinopril 20 MG Oral Tablet")
    episodes = medication_episodes([old, new])
    first, second = episodes
    assert second.changed_from == first.code
    assert not is_true_stop(first, episodes)  # dose change, the patient is still on lisinopril

    (alone,) = medication_episodes([old])
    assert is_true_stop(alone, [alone])


def test_open_acute_conditions_are_closed() -> None:
    rows = [
        {"PATIENT": "p1", "START": "2020-03-01", "STOP": "", "CODE": "1", "DESCRIPTION": "Sepsis (disorder)"},
        {
            "PATIENT": "p1",
            "START": "2019-01-01",
            "STOP": "",
            "CODE": "2",
            "DESCRIPTION": "Essential hypertension (disorder)",
        },
        {"PATIENT": "p1", "START": "2019-01-01", "STOP": "", "CODE": "3", "DESCRIPTION": "Stress (finding)"},
    ]
    conditions = {c.name: c for c in clinical_conditions(rows)}
    assert set(conditions) == {"Sepsis", "Essential hypertension"}
    assert conditions["Sepsis"].stop == date(2020, 3, 31)
    assert conditions["Essential hypertension"].stop is None
