#!/usr/bin/env bash
# scripts/synthea.sh
#
# Generates the raw synthetic population with the pinned Synthea jar into
# .tools/synthea/output (gitignored). `medigraph data cohort` then selects the study
# cohort from it and writes the small, committed CSVs under data/synthetic/csv/.
#
# Every input that affects the output is fixed here (seed, clinician seed, reference
# and end dates, age band, population size, location), so a re-run regenerates the same
# population with the same jar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYNTHEA_VERSION="${SYNTHEA_VERSION:-v4.0.0}"
JAR="$ROOT/.tools/synthea/synthea-$SYNTHEA_VERSION.jar"
OUT="$ROOT/.tools/synthea/output"

SEED="${SYNTHEA_SEED:-42}"
POPULATION="${SYNTHEA_POPULATION:-80}"      # cohort selection keeps 20 of these
AGES="${SYNTHEA_AGES:-45-85}"
REFERENCE_DATE="${SYNTHEA_REFERENCE_DATE:-20260101}"
STATE="${SYNTHEA_STATE:-Massachusetts}"

env_value() {
  [[ -f "$ROOT/.env" ]] || return 0
  grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- || true
}
JAVA_HOME="${MEDIGRAPH_JAVA_HOME:-$(env_value MEDIGRAPH_JAVA_HOME)}"
JAVA_HOME="${JAVA_HOME:-$ROOT/.tools/jdk-21}"
JAVA="$JAVA_HOME/bin/java"

[[ -x "$JAVA" ]] || { echo "No JDK at $JAVA_HOME (run scripts/bootstrap_tools.sh)" >&2; exit 1; }
[[ -f "$JAR" ]]  || { echo "Synthea jar missing: $JAR (run scripts/bootstrap_tools.sh)" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"

# Synthea writes ./output relative to its working directory unless told otherwise, and
# may create other scratch files there, so run it from the output folder.
cd "$OUT"
"$JAVA" -jar "$JAR" \
  -s "$SEED" -cs "$SEED" -p "$POPULATION" -a "$AGES" \
  -r "$REFERENCE_DATE" -e "$REFERENCE_DATE" \
  --exporter.baseDirectory="$OUT/" \
  --exporter.csv.export=true \
  --exporter.fhir.export=false \
  --exporter.hospital.fhir.export=false \
  --exporter.practitioner.fhir.export=false \
  --exporter.years_of_history=10 \
  --generate.only_alive_patients=true \
  "$STATE"

echo
echo "Raw Synthea CSVs: $OUT/csv"
echo "Next: uv run medigraph data cohort"
