#!/usr/bin/env bash
#
# Sportsball data restore — inverse of scripts/backup.sh.
#
# Usage:
#   ./scripts/restore.sh backups/<timestamp>
#
# Restores Postgres (drops/recreates objects via the dump's --clean), the DuckDB
# file, and model artifacts. DESTRUCTIVE for Postgres: it overwrites current
# rows. The DuckDB file and models/ are overwritten in place. Asks before doing
# anything irreversible.
#
# Env overrides: PG_SERVICE (default: postgres).
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="${1:?usage: restore.sh <backup-dir>}"
PG_SERVICE="${PG_SERVICE:-postgres}"
[[ -d "$SRC" ]] || { echo "No such backup dir: $SRC" >&2; exit 1; }

echo ">> Restoring from ${SRC}/"
[[ -f "${SRC}/MANIFEST.txt" ]] && grep -E '^(created_utc|git_commit)=' "${SRC}/MANIFEST.txt"

read -r -p "This overwrites current Postgres + DuckDB + models. Continue? [y/N] " ans
[[ "${ans:-}" == [yY] ]] || { echo "Aborted."; exit 1; }

# --- Postgres ---
if [[ -f "${SRC}/postgres.sql.gz" ]]; then
  if docker compose ps --status running --services 2>/dev/null | grep -qx "$PG_SERVICE"; then
    echo ">> Restoring Postgres (psql via container)..."
    gunzip -c "${SRC}/postgres.sql.gz" \
      | docker compose exec -T "$PG_SERVICE" sh -c \
          'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1'
    echo "   Postgres restored."
  else
    echo "!! Postgres service '${PG_SERVICE}' not running — start it and re-run." >&2
    exit 1
  fi
fi

# --- DuckDB ---
if [[ -f "${SRC}/sportsball.duckdb" ]]; then
  echo ">> Restoring DuckDB research store..."
  cp "${SRC}/sportsball.duckdb" data/sportsball.duckdb
fi

# --- Models ---
if [[ -d "${SRC}/models" ]]; then
  echo ">> Restoring model artifacts..."
  cp -r "${SRC}/models/." models/
fi
[[ -f "${SRC}/optimized_params.json" ]] && cp "${SRC}/optimized_params.json" optimized_params.json

echo ">> Restore complete."
