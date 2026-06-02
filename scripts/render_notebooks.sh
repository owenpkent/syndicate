#!/usr/bin/env bash
#
# Render all notebooks to dated HTML — a passive dashboard you can open without
# launching Jupyter. Executes each notebook FRESH against current data (so the
# DeFi/series notebooks reflect the latest cron snapshots), writes HTML into
# notebooks/rendered/<UTC date>/, refreshes a `latest` symlink, and prunes old days.
#
# Source .ipynb files are NOT modified (output goes to HTML only; they stay
# output-stripped in git). Read-only DuckDB opens can collide with a capture
# cron's brief write lock, so each notebook is retried a few times.
#
# Usage:  ./scripts/render_notebooks.sh
# Env:    KEEP (dated dirs to retain, default 14); TIMEOUT (per-cell secs, default 600)
set -uo pipefail
cd "$(dirname "$0")/.."

JUPYTER="./venv/bin/jupyter"
KERNEL="sportsball"
KEEP="${KEEP:-14}"
TIMEOUT="${TIMEOUT:-600}"
DATE="$(date -u +%Y-%m-%d)"
OUT="notebooks/rendered/${DATE}"
mkdir -p "$OUT"

echo ">> Rendering notebooks -> ${OUT}/ ($(date -u))"
ok=0; fail=0; rendered=()
for nb in notebooks/0*.ipynb; do
  name="$(basename "${nb%.ipynb}")"
  for attempt in 1 2 3; do
    if "$JUPYTER" nbconvert --to html --execute \
        --ExecutePreprocessor.kernel_name="$KERNEL" \
        --ExecutePreprocessor.timeout="$TIMEOUT" \
        --output-dir "$OUT" "$nb" >/dev/null 2>&1; then
      echo "   ok   ${name} (attempt ${attempt})"; ok=$((ok+1)); rendered+=("$name"); break
    fi
    if [[ $attempt -lt 3 ]]; then sleep 20; else
      echo "   FAIL ${name} (after 3 attempts)"; fail=$((fail+1)); fi
  done
done

# Index page linking the day's renders.
{
  echo "<!doctype html><meta charset=utf-8><title>sportsball notebooks ${DATE}</title>"
  echo "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:3rem auto;padding:0 1rem}"
  echo "a{display:block;padding:.4rem 0}h1{font-size:1.4rem}</style>"
  echo "<h1>sportsball notebooks — ${DATE} (UTC)</h1>"
  for name in "${rendered[@]}"; do echo "<a href=\"./${name}.html\">${name}</a>"; done
  echo "<p style=color:#888>rendered $(date -u '+%Y-%m-%d %H:%M UTC') · ${ok} ok, ${fail} failed</p>"
} > "${OUT}/index.html"

ln -sfn "$DATE" notebooks/rendered/latest

# Prune to the newest $KEEP dated dirs.
mapfile -t days < <(find notebooks/rendered -maxdepth 1 -mindepth 1 -type d \
  -regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}' -printf '%f\n' | sort)
if (( ${#days[@]} > KEEP )); then
  for d in "${days[@]:0:${#days[@]}-KEEP}"; do echo "   prune ${d}"; rm -rf "notebooks/rendered/${d:?}"; done
fi

echo ">> Done: ${ok} ok, ${fail} failed -> notebooks/rendered/latest/index.html"
[[ $fail -eq 0 ]]
