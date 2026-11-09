"""`medigraph` command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from medigraph.config import get_settings

app = typer.Typer(help="MediGraph 2.0: temporal clinical evidence reconciliation.", no_args_is_help=True)
data_app = typer.Typer(help="Build the synthetic dataset.", no_args_is_help=True)
app.add_typer(data_app, name="data")


@data_app.command("cohort")
def cohort(
    raw_dir: Annotated[Path | None, typer.Option(help="Raw Synthea CSV directory.")] = None,
    out_dir: Annotated[Path | None, typer.Option(help="Where to write the cohort CSVs.")] = None,
) -> None:
    """Select the study cohort from raw Synthea output and write the committed CSVs."""
    from medigraph.data.synthea import load_tables, select_cohort, write_cohort
    from medigraph.data.vocab import LABS, VITALS

    s = get_settings()
    raw_dir = raw_dir or s.raw_synthea_csv_dir
    out_dir = out_dir or s.cohort_csv_dir
    if not (raw_dir / "patients.csv").exists():
        raise typer.BadParameter(f"no Synthea CSVs in {raw_dir}; run scripts/synthea.sh first")
    start, end = s.window
    tables = load_tables(raw_dir)
    ids = select_cohort(tables, size=s.cohort_size, seed=s.seed, window_start=start, window_end=end)
    counts = write_cohort(
        tables, ids, out_dir, window_start=start, window_end=end, lab_codes=LABS, vital_codes=VITALS
    )
    typer.echo(f"cohort of {len(ids)} patients written to {out_dir}")
    typer.echo(json.dumps(counts, indent=2))


@data_app.command("notes")
def notes(
    csv_dir: Annotated[Path | None, typer.Option(help="Cohort CSV directory.")] = None,
    out_dir: Annotated[Path | None, typer.Option(help="Output directory for notes and manifests.")] = None,
) -> None:
    """Render clinical notes plus gold fact and perturbation manifests (deterministic)."""
    from medigraph.data.notegen import generate_notes

    s = get_settings()
    start, end = s.window
    summary = generate_notes(
        csv_dir or s.cohort_csv_dir,
        out_dir or s.synthetic_dir,
        seed=s.seed,
        window_start=start,
        window_end=end,
    )
    typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
