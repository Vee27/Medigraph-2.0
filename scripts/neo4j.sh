#!/usr/bin/env bash
# scripts/neo4j.sh {start|stop|restart|status|password [pw]|console|logs}
#
# Drives the user-local Neo4j in .tools/neo4j (installed by scripts/bootstrap_tools.sh)
# with JDK 21 from MEDIGRAPH_JAVA_HOME or .tools/jdk-21. Bolt listens on :7688 and the
# browser on :7475, so it can run alongside another local Neo4j on the default ports.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEO4J_HOME="$ROOT/.tools/neo4j"
NEO4J="$NEO4J_HOME/bin/neo4j"
NEO4J_ADMIN="$NEO4J_HOME/bin/neo4j-admin"
BOLT_PORT="${NEO4J_BOLT_PORT:-7688}"
HTTP_PORT="${NEO4J_HTTP_PORT:-7475}"

env_value() {  # env_value KEY -> value from .env, or empty
  [[ -f "$ROOT/.env" ]] || return 0
  grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- || true
}

JAVA_HOME="${MEDIGRAPH_JAVA_HOME:-$(env_value MEDIGRAPH_JAVA_HOME)}"
JAVA_HOME="${JAVA_HOME:-$ROOT/.tools/jdk-21}"
export JAVA_HOME

if [[ ! -x "$JAVA_HOME/bin/java" ]]; then
  echo "No JDK at $JAVA_HOME. Run scripts/bootstrap_tools.sh or set MEDIGRAPH_JAVA_HOME." >&2
  exit 1
fi
if [[ ! -x "$NEO4J" ]]; then
  echo "Neo4j not installed at $NEO4J_HOME. Run scripts/bootstrap_tools.sh first." >&2
  exit 1
fi

bolt_up() { (exec 3<>"/dev/tcp/127.0.0.1/$BOLT_PORT") 2>/dev/null; }

case "${1:-status}" in
  start)
    "$NEO4J" start
    echo "Waiting for bolt on :$BOLT_PORT ..."
    for _ in $(seq 1 90); do
      if bolt_up; then
        echo "Neo4j is up.  Browser: http://localhost:$HTTP_PORT  Bolt: bolt://localhost:$BOLT_PORT"
        exit 0
      fi
      sleep 1
    done
    echo "Timed out waiting for bolt on :$BOLT_PORT. Check: $0 logs" >&2
    exit 1
    ;;
  stop)    "$NEO4J" stop ;;
  restart) "$NEO4J" restart ;;
  console) "$NEO4J" console ;;   # foreground, for debugging startup failures
  status)
    "$NEO4J" status || true
    if bolt_up; then echo "bolt :$BOLT_PORT reachable"; else echo "bolt :$BOLT_PORT NOT reachable"; fi
    ;;
  password)
    # Neo4j requires the initial password to be set before first use. Setting it offline
    # avoids the interactive browser prompt. Defaults to MEDIGRAPH_NEO4J_PASSWORD in .env.
    pw="${2:-$(env_value MEDIGRAPH_NEO4J_PASSWORD)}"
    if [[ -z "$pw" ]]; then
      echo "usage: $0 password <new-password>   (or set MEDIGRAPH_NEO4J_PASSWORD in .env)" >&2
      exit 1
    fi
    "$NEO4J" stop >/dev/null 2>&1 || true
    "$NEO4J_ADMIN" dbms set-initial-password "$pw"
    echo "Initial password set. Next: $0 start"
    ;;
  logs)    tail -n 60 "$NEO4J_HOME/logs/neo4j.log" 2>/dev/null || echo "no logs yet" ;;
  *)       echo "usage: $0 {start|stop|restart|status|password [pw]|console|logs}" >&2; exit 1 ;;
esac
