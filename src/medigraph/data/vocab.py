"""Controlled vocabularies shared by the note generator (and, later, the extractors).

Kept deliberately small and explicit: every surface form the generator can write is
listed here, so the extraction evaluation can report exactly which variants it handles.
"""

from __future__ import annotations

from dataclasses import dataclass

# Brand names and common clinical abbreviations, keyed by canonical drug name as
# produced by synthea.parse_medication. Used by perturbation P3.
DRUG_SYNONYMS: dict[str, tuple[str, ...]] = {
    "acetaminophen": ("Tylenol", "APAP"),
    "albuterol": ("ProAir", "Ventolin"),
    "alendronic acid": ("Fosamax", "alendronate"),
    "amlodipine": ("Norvasc",),
    "amoxicillin": ("Amoxil",),
    "aspirin": ("ASA",),
    "atorvastatin": ("Lipitor",),
    "budesonide": ("Pulmicort",),
    "carvedilol": ("Coreg",),
    "clopidogrel": ("Plavix",),
    "epoetin alfa": ("Epogen",),
    "fluticasone propionate": ("Flovent",),
    "furosemide": ("Lasix",),
    "hydrochlorothiazide": ("HCTZ", "Microzide"),
    "ibuprofen": ("Advil", "Motrin"),
    "insulin isophane human": ("NPH insulin", "Humulin N"),
    "lisinopril": ("Zestril", "Prinivil"),
    "losartan potassium": ("Cozaar",),
    "metformin hydrochloride": ("Glucophage",),
    "metoprolol succinate": ("Toprol XL",),
    "naproxen sodium": ("Aleve",),
    "nitroglycerin": ("NTG", "Nitrostat"),
    "prasugrel": ("Effient",),
    "sacubitril/valsartan": ("Entresto",),
    "simvastatin": ("Zocor",),
    "tramadol hydrochloride": ("Ultram",),
    "warfarin": ("Coumadin",),
}

# Drugs and conditions used for negated statements (P5). The generator only negates
# ones the patient has never had, so every negation is true.
NEGATION_DRUGS: tuple[str, ...] = (
    "aspirin", "warfarin", "clopidogrel", "atorvastatin", "metformin hydrochloride",
    "lisinopril", "amlodipine", "furosemide", "albuterol", "ibuprofen",
)  # fmt: skip
NEGATION_CONDITIONS: tuple[str, ...] = (
    "asthma", "atrial fibrillation", "heart failure", "stroke", "epilepsy",
    "chronic obstructive pulmonary disease", "diabetes mellitus type 2", "hypertension",
)  # fmt: skip

@dataclass(frozen=True)
class LabSpec:
    code: str  # LOINC
    name: str  # display name used in notes
    unit: str  # canonical unit (Synthea's)
    alt_unit: str | None = None  # unit used by perturbation P8
    alt_factor: float | None = None  # value_in_alt_unit = value * alt_factor
    decimals: int = 1


LABS: dict[str, LabSpec] = {
    s.code: s
    for s in (
        LabSpec("2339-0", "Glucose", "mg/dL", "mmol/L", 1 / 18.016),
        LabSpec("4548-4", "Hemoglobin A1c", "%"),
        LabSpec("38483-4", "Creatinine", "mg/dL", "umol/L", 88.42, decimals=2),
        LabSpec("33914-3", "eGFR", "mL/min/1.73m2", decimals=0),
        LabSpec("2947-0", "Sodium", "mmol/L", decimals=0),
        LabSpec("6298-4", "Potassium", "mmol/L"),
        LabSpec("2093-3", "Total cholesterol", "mg/dL", "mmol/L", 1 / 38.67, decimals=0),
        LabSpec("18262-6", "LDL cholesterol", "mg/dL", "mmol/L", 1 / 38.67, decimals=0),
        LabSpec("2085-9", "HDL cholesterol", "mg/dL", "mmol/L", 1 / 38.67, decimals=0),
        LabSpec("2571-8", "Triglycerides", "mg/dL", "mmol/L", 1 / 88.57, decimals=0),
    )
}

# Vital signs are rendered for realism only; they are not part of the gold facts.
VITALS: dict[str, str] = {
    "8480-6": "systolic",
    "8462-4": "diastolic",
    "8867-4": "heart_rate",
    "29463-7": "weight",
}
