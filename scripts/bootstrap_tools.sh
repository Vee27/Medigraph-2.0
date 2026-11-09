#!/usr/bin/env bash
# scripts/bootstrap_tools.sh
#
# Installs the project's user-local tools into .tools/ (gitignored). Nothing is
# installed system-wide; deleting .tools/ removes every trace, including the database.
#
#   .tools/jdk-21/      Temurin JDK 21 (skipped when MEDIGRAPH_JAVA_HOME points at one)
#   .tools/neo4j/       Neo4j Community, configured for bolt :7688 / http :7475
#   .tools/synthea/     Synthea synthetic patient generator (pinned release jar)
#
# Re-running is safe: each step is skipped when its target already exists.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/.tools"
DOWNLOADS="$TOOLS/downloads"

NEO4J_VERSION="${NEO4J_VERSION:-2026.10.2}"
SYNTHEA_VERSION="${SYNTHEA_VERSION:-v4.0.0}"
NEO4J_BOLT_PORT="${NEO4J_BOLT_PORT:-7688}"
NEO4J_HTTP_PORT="${NEO4J_HTTP_PORT:-7475}"

# Pick up MEDIGRAPH_JAVA_HOME (and friends) from .env without exporting secrets.
if [[ -f "$ROOT/.env" && -z "${MEDIGRAPH_JAVA_HOME:-}" ]]; then
  MEDIGRAPH_JAVA_HOME="$(grep -E '^MEDIGRAPH_JAVA_HOME=' "$ROOT/.env" | tail -1 | cut -d= -f2- || true)"
fi

mkdir -p "$DOWNLOADS"

fetch() {  # fetch <url> <dest>
  local url="$1" dest="$2"
  if [[ -s "$dest" ]]; then
    echo "  already downloaded: $(basename "$dest")"
    return
  fi
  echo "  downloading $(basename "$dest") ..."
  curl -fL --retry 3 --progress-bar -o "$dest.part" "$url"
  mv "$dest.part" "$dest"
}

# ---------------------------------------------------------------- JDK 21
echo "[1/3] JDK 21"
if [[ -n "${MEDIGRAPH_JAVA_HOME:-}" && -x "${MEDIGRAPH_JAVA_HOME}/bin/java" ]]; then
  echo "  using MEDIGRAPH_JAVA_HOME=$MEDIGRAPH_JAVA_HOME"
elif [[ -x "$TOOLS/jdk-21/bin/java" ]]; then
  echo "  already installed: .tools/jdk-21"
else
  fetch "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse" \
        "$DOWNLOADS/jdk-21.tar.gz"
  mkdir -p "$TOOLS/jdk-21"
  tar -xzf "$DOWNLOADS/jdk-21.tar.gz" -C "$TOOLS/jdk-21" --strip-components=1
  echo "  installed .tools/jdk-21"
fi

# ---------------------------------------------------------------- Neo4j
echo "[2/3] Neo4j Community $NEO4J_VERSION"
if [[ -x "$TOOLS/neo4j/bin/neo4j" ]]; then
  echo "  already installed: .tools/neo4j"
else
  tarball="$DOWNLOADS/neo4j-community-$NEO4J_VERSION-unix.tar.gz"
  fetch "https://dist.neo4j.org/neo4j-community-$NEO4J_VERSION-unix.tar.gz" "$tarball"
  fetch "https://dist.neo4j.org/neo4j-community-$NEO4J_VERSION-unix.tar.gz.sha256" "$tarball.sha256"
  expected="$(awk '{print $1}' "$tarball.sha256")"
  actual="$(sha256sum "$tarball" | awk '{print $1}')"
  if [[ "$expected" != "$actual" ]]; then
    echo "  checksum mismatch for $tarball (expected $expected, got $actual)" >&2
    exit 1
  fi
  mkdir -p "$TOOLS/neo4j"
  tar -xzf "$tarball" -C "$TOOLS/neo4j" --strip-components=1
  # Own ports so this instance can run next to another local Neo4j on the defaults.
  cat >> "$TOOLS/neo4j/conf/neo4j.conf" <<EOF

# ---- medigraph overrides (scripts/bootstrap_tools.sh) ----
server.default_listen_address=127.0.0.1
server.bolt.listen_address=:$NEO4J_BOLT_PORT
server.bolt.advertised_address=:$NEO4J_BOLT_PORT
server.http.listen_address=:$NEO4J_HTTP_PORT
server.http.advertised_address=:$NEO4J_HTTP_PORT
server.memory.heap.initial_size=512m
server.memory.heap.max_size=1g
server.memory.pagecache.size=512m
EOF
  echo "  installed .tools/neo4j (bolt :$NEO4J_BOLT_PORT, http :$NEO4J_HTTP_PORT)"
fi

# ---------------------------------------------------------------- Synthea
echo "[3/3] Synthea $SYNTHEA_VERSION"
mkdir -p "$TOOLS/synthea"
fetch "https://github.com/synthetichealth/synthea/releases/download/$SYNTHEA_VERSION/synthea-with-dependencies.jar" \
      "$TOOLS/synthea/synthea-$SYNTHEA_VERSION.jar"

echo
echo "Done. Next: scripts/neo4j.sh password <pw>  (once), then scripts/neo4j.sh start"
