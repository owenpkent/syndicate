"""Render all notebooks to dated HTML — a passive dashboard, no Jupyter needed.

Executes each notebook FRESH against current data (so the DeFi/series notebooks
reflect the latest cron snapshots), writes HTML into
``notebooks/rendered/<UTC date>/`` with an ``index.html`` + a ``latest`` symlink,
and prunes to the newest ``KEEP`` days. Source ``.ipynb`` files are not modified
(they stay output-stripped in git; only HTML is produced).

Read-only DuckDB opens can collide with a capture cron's brief write lock, so each
notebook is retried a few times. Runs in-process via nbconvert's API.

    python scripts/render_notebooks.py
    KEEP=30 TIMEOUT=900 python scripts/render_notebooks.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import nbformat
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
from sportsball.logging_conf import get_logger  # noqa: E402

log = get_logger("render_notebooks")

KERNEL = "sportsball"
KEEP = int(os.getenv("KEEP", "14"))
TIMEOUT = int(os.getenv("TIMEOUT", "600"))
ATTEMPTS = 3
RETRY_WAIT = 20


def render_one(nb_path: Path, out_dir: Path) -> bool:
    """Execute + export one notebook to HTML, retrying on transient failure."""
    name = nb_path.stem
    for attempt in range(1, ATTEMPTS + 1):
        try:
            nb = nbformat.read(nb_path, as_version=4)
            ep = ExecutePreprocessor(timeout=TIMEOUT, kernel_name=KERNEL)
            ep.preprocess(nb, {"metadata": {"path": str(REPO)}})  # cwd = repo root
            body, _ = HTMLExporter().from_notebook_node(nb)
            (out_dir / f"{name}.html").write_text(body, encoding="utf-8")
            log.info("ok   %s (attempt %d)", name, attempt)
            return True
        except Exception as exc:  # noqa: BLE001
            if attempt < ATTEMPTS:
                log.warning("retry %s (attempt %d): %s", name, attempt, str(exc)[:120])
                time.sleep(RETRY_WAIT)
            else:
                log.error("FAIL %s after %d attempts: %s", name, ATTEMPTS, str(exc)[:200])
    return False


def write_index(out_dir: Path, date: str, rendered: list[str], ok: int, fail: int) -> None:
    links = "\n".join(f'<a href="./{n}.html">{n}</a>' for n in rendered)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_dir.joinpath("index.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>sportsball notebooks {date}</title>"
        "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:3rem auto;"
        "padding:0 1rem}a{display:block;padding:.4rem 0}h1{font-size:1.4rem}</style>"
        f"<h1>sportsball notebooks — {date} (UTC)</h1>\n{links}\n"
        f"<p style=color:#888>rendered {stamp} · {ok} ok, {fail} failed</p>", encoding="utf-8")


def prune(root: Path) -> None:
    days = sorted(d for d in root.iterdir()
                  if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name))
    for d in days[:-KEEP] if len(days) > KEEP else []:
        log.info("prune %s", d.name)
        shutil.rmtree(d)


def main() -> int:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    root = REPO / "notebooks" / "rendered"
    out_dir = root / date
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Rendering notebooks -> %s/", out_dir)

    rendered, ok, fail = [], 0, 0
    for nb_path in sorted((REPO / "notebooks").glob("0*.ipynb")):
        if render_one(nb_path, out_dir):
            ok += 1; rendered.append(nb_path.stem)
        else:
            fail += 1

    write_index(out_dir, date, rendered, ok, fail)
    latest = root / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(date)  # relative target
    prune(root)

    log.info("Done: %d ok, %d failed -> notebooks/rendered/latest/index.html", ok, fail)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
