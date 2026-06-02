#!/usr/bin/env bash
#
# Sportsball data backup.
#
# Snapshots the durable state into backups/<timestamp>/:
#   - Postgres (events/signals/trades) via an in-container pg_dump. The data
#     directory (./data/sportsball_postgres) is owned by the container's uid 70
#     and mode 0700, so a host-side `cp -r` gets "permission denied" — a
#     logical dump through the running container is the supported path.
#   - DuckDB stores (data/sportsball.duckdb + data/defi.duckdb) — host-owned, file copy.
#   - Model artifacts (models/) + optimized_params.json — file copy.
#
# Redis is intentionally skipped: it's the broker/queue and is rebuildable.
#
# Usage:
#   ./scripts/backup.sh            # -> backups/<UTC timestamp>/
#   ./scripts/backup.sh /mnt/nas   # -> /mnt/nas/<UTC timestamp>/
#
# Env overrides:
#   BACKUP_ROOT  local snapshot root (default: backups)
#   PG_SERVICE   compose service to dump (default: postgres)
#   KEEP         most-recent snapshots to retain per location (default: 14)
#   MIRROR       optional off-site dir (e.g. an SMB mount) to copy each snapshot
#                into. Pruned to KEEP just like the local root. A missing/
#                unwritable MIRROR warns but never fails the local backup.
set -euo pipefail

# Keep only the newest $KEEP timestamped snapshot dirs under $1.
prune_snapshots() {
  local root="$1" keep="${KEEP:-14}"
  [[ -d "$root" ]] || return 0
  local snaps
  mapfile -t snaps < <(find "$root" -maxdepth 1 -mindepth 1 -type d \
    -regextype posix-extended -regex '.*/[0-9]{8}T[0-9]{6}Z' -printf '%f\n' | sort)
  (( ${#snaps[@]} > keep )) || return 0
  local n=$(( ${#snaps[@]} - keep )) d
  echo ">> Pruning ${n} old snapshot(s) in ${root} (keeping newest ${keep}):"
  for d in "${snaps[@]:0:n}"; do
    echo "   rm ${root}/${d}"
    rm -rf "${root:?}/${d}"
  done
}

cd "$(dirname "$0")/.."

PG_SERVICE="${PG_SERVICE:-postgres}"
BACKUP_ROOT="${1:-${BACKUP_ROOT:-backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT}/${STAMP}"
mkdir -p "$DEST"

echo ">> Backing up to ${DEST}/"

# --- Postgres: logical dump via the running container (no host perms needed) ---
if docker compose ps --status running --services 2>/dev/null | grep -qx "$PG_SERVICE"; then
  echo ">> pg_dump ${PG_SERVICE} (market_history)..."
  # Read DB name/user from the container's own env so no secrets live here.
  docker compose exec -T "$PG_SERVICE" sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges --clean --if-exists' \
    | gzip > "${DEST}/postgres.sql.gz"
  echo "   wrote postgres.sql.gz ($(du -h "${DEST}/postgres.sql.gz" | cut -f1))"
else
  echo "!! Postgres service '${PG_SERVICE}' not running — skipping DB dump." >&2
  echo "   Start it (docker compose up -d ${PG_SERVICE}) and re-run for a full backup." >&2
fi

# --- DuckDB stores (host-owned files): sports research + DeFi time-series ---
for store in sportsball defi; do
  if [[ -f "data/${store}.duckdb" ]]; then
    echo ">> Copying DuckDB store ${store}.duckdb..."
    cp "data/${store}.duckdb" "${DEST}/${store}.duckdb"
    echo "   wrote ${store}.duckdb ($(du -h "${DEST}/${store}.duckdb" | cut -f1))"
  fi
done

# --- Model artifacts (regenerable, but cheap to snapshot) ---
if [[ -d models ]]; then
  echo ">> Copying model artifacts..."
  cp -r models "${DEST}/models"
fi
[[ -f optimized_params.json ]] && cp optimized_params.json "${DEST}/optimized_params.json"

# --- Portable closing-odds export (paid Odds API data; re-loadable insurance) ---
for f in data/closing_odds_*.json; do
  [[ -e "$f" ]] && { echo ">> Copying $(basename "$f") (paid odds export)..."; cp "$f" "${DEST}/"; }
done

# --- Manifest ---
{
  echo "created_utc=${STAMP}"
  echo "git_commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "host=$(hostname)"
  echo "contents:"
  ( cd "$DEST" && ls -lh )
} > "${DEST}/MANIFEST.txt"

echo ">> Done (local). ${DEST}/"
ls -lh "$DEST"

# --- Mirror off-site (e.g. SMB mount), then prune both locations ---
if [[ -n "${MIRROR:-}" ]]; then
  if mkdir -p "$MIRROR" 2>/dev/null && [[ -w "$MIRROR" ]]; then
    echo ">> Mirroring snapshot to ${MIRROR}/${STAMP}/ ..."
    # cp -r (not -a): SMB/GVFS can't store Unix perms/ownership, so -a errors.
    if cp -r "$DEST" "${MIRROR}/${STAMP}.partial" && \
       mv "${MIRROR}/${STAMP}.partial" "${MIRROR}/${STAMP}"; then
      echo "   mirrored OK"
      prune_snapshots "$MIRROR"
    else
      echo "!! Mirror copy failed — local backup is intact." >&2
      rm -rf "${MIRROR}/${STAMP}.partial" 2>/dev/null || true
    fi
  else
    echo "!! MIRROR '${MIRROR}' missing or not writable (NAS down?) — kept local only." >&2
  fi
fi

prune_snapshots "$BACKUP_ROOT"
