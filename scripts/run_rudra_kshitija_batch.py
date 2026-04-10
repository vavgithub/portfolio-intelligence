#!/usr/bin/env python3
"""
Run the full portfolio pipeline for every row in a portfolio list JSON
and merge all outputs into ONE JSON file (resumable overnight).

Usage (from repo root):
  ./venv/bin/python -u scripts/run_rudra_kshitija_batch.py
  ./venv/bin/python -u scripts/run_rudra_kshitija_batch.py \\
      --input scripts/my_10_portfolios.json --output scripts/my_10_reports.json

Defaults:
  --input  scripts/rudra_kshitija_portfolios.json
  --output scripts/rudra_kshitija_all_reports.json

Re-run the same command to resume: completed URLs are skipped (per output file).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_INPUT = ROOT / "scripts" / "rudra_kshitija_portfolios.json"
DEFAULT_OUTPUT = ROOT / "scripts" / "rudra_kshitija_all_reports.json"
ROLE = "Brand Designer"


def resolve_repo_path(p: Path) -> Path:
    p = Path(p)
    return p.resolve() if p.is_absolute() else (ROOT / p).resolve()


def norm_url(raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u.lstrip("/")
    try:
        from urllib.parse import urlparse, urlunparse

        p = urlparse(u)
        path = p.path or ""
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        out = urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))
        return out
    except Exception:
        return u


def load_state(output_path: Path, input_path: Path) -> dict:
    if not output_path.exists():
        try:
            rel = str(input_path.relative_to(ROOT))
        except ValueError:
            rel = str(input_path)
        return {
            "source_file": rel,
            "role": ROLE,
            "results": [],
        }
    return json.loads(output_path.read_text(encoding="utf-8"))


def save_state(state: dict, output_path: Path) -> None:
    output_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch portfolio pipeline → one JSON (resumable).")
    ap.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Portfolio list JSON (default: {DEFAULT_INPUT.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Merged results JSON (default: {DEFAULT_OUTPUT.relative_to(ROOT)})",
    )
    args = ap.parse_args()
    input_path = resolve_repo_path(args.input)
    output_path = resolve_repo_path(args.output)

    from app.main import run_portfolio_intelligence_pipeline

    rows = json.loads(input_path.read_text(encoding="utf-8"))
    state = load_state(output_path, input_path)
    results: list = state.setdefault("results", [])
    # Resume: only skip URLs that completed successfully (re-run failed after a crash)
    done_ok = {norm_url(r.get("portfolio_url", "")) for r in results if r.get("status") == "ok"}

    total = len(rows)
    for i, row in enumerate(rows, 1):
        url = norm_url(row.get("portfolio", ""))
        if not url:
            print(f"[{i}/{total}] skip (no URL): {row.get('name')}", flush=True)
            continue
        if url in done_ok:
            print(f"[{i}/{total}] skip (already ok): {url}", flush=True)
            continue

        print(f"\n=== [{i}/{total}] {row.get('name', '—')} :: {url} ===", flush=True)
        entry = {
            "portfolio_url": url,
            "human": {
                "name": row.get("name"),
                "jobProfile": row.get("jobProfile"),
                "score": row.get("score"),
                "comment": row.get("comment", ""),
                "reviewer": row.get("reviewer"),
            },
            "status": "pending",
        }
        try:
            report = run_portfolio_intelligence_pipeline(url, candidate_role=ROLE)
            entry["report"] = report
            entry["run_id"] = report.get("run_id")
            entry["status"] = "ok" if report.get("status") != "skipped" else "skipped"
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)

        # Replace or append: remove prior failed entry for same URL
        results = [r for r in results if norm_url(r.get("portfolio_url", "")) != url]
        results.append(entry)
        state["results"] = results
        state["completed_count"] = len(results)
        try:
            state["source_file"] = str(input_path.relative_to(ROOT))
        except ValueError:
            state["source_file"] = str(input_path)
        state["role"] = ROLE
        save_state(state, output_path)
        print(f"  → saved ({len(results)} rows in {output_path.name})", flush=True)

    print("\nDone. Full report written to:", output_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
