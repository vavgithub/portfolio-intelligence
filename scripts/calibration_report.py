#!/usr/bin/env python3
"""
Calibration audit: AI score vs human portfolio ratings.

Primary paired dataset: scripts/trusted37_results.json
  (pipeline runs on poc_batch_trusted.json with designer_score + ai_score).

Usage:
  .venv/bin/python scripts/calibration_report.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TRUSTED_RESULTS = Path(
    os.environ.get(
        "CALIBRATION_RESULTS",
        str(ROOT / "scripts" / "trusted37_results.json"),
    )
)
POC_BATCH = ROOT / "poc_batch_trusted.json"
LLM_CLEANED = ROOT / "HireHive.candidates.llm_cleaned.json"
FRAMEWORK_V2 = ROOT / "app" / "brand_identity_expert_framework_v2.txt"
CONTENT_SUFFICIENCY = ROOT / "app" / "content_sufficiency.py"

# Known pipeline milestones (file mtimes / manual anchors for staleness warnings).
RUBRIC_MILESTONES = [
    ("brand_identity_expert_framework_v2.txt", FRAMEWORK_V2),
    ("poc_batch_trusted.json (human labels)", POC_BATCH),
    ("trusted37_results.json (AI run batch)", TRUSTED_RESULTS),
    ("content_sufficiency.py (Behance wall gate)", CONTENT_SUFFICIENCY),
]


def file_mtime_label(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def load_trusted_pairs() -> list[dict]:
    if not TRUSTED_RESULTS.is_file():
        return []
    data = json.loads(TRUSTED_RESULTS.read_text(encoding="utf-8"))
    rows = []
    for r in data.get("results") or []:
        human = r.get("designer_score")
        ai = r.get("ai_score")
        if r.get("status") != "ok":
            continue
        if human is None or ai is None:
            continue
        try:
            h = float(human)
            a = float(ai)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "name": r.get("name", ""),
                "portfolio_url": r.get("portfolio_url", ""),
                "role": r.get("role", ""),
                "human_score": h,
                "ai_score": a,
                "signed_error": round(a - h, 3),
                "abs_error": round(abs(a - h), 3),
            }
        )
    return rows


def band_stats(rows: list[dict]) -> dict[int, dict]:
    by_band: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        band = int(round(r["human_score"]))
        band = max(1, min(5, band))
        by_band[band].append(r)

    out = {}
    for band in sorted(by_band):
        items = by_band[band]
        signed = [x["signed_error"] for x in items]
        out[band] = {
            "n": len(items),
            "mean_signed_error": round(sum(signed) / len(signed), 3),
            "mean_abs_error": round(sum(x["abs_error"] for x in items) / len(items), 3),
            "ai_over_human": sum(1 for s in signed if s > 0),
            "ai_under_human": sum(1 for s in signed if s < 0),
            "exact": sum(1 for s in signed if s == 0),
        }
    return out


def staleness_flags() -> list[str]:
    flags = []
    results_mtime = TRUSTED_RESULTS.stat().st_mtime if TRUSTED_RESULTS.is_file() else None
    if results_mtime and CONTENT_SUFFICIENCY.is_file():
        if CONTENT_SUFFICIENCY.stat().st_mtime > results_mtime:
            flags.append(
                "AI batch predates Behance content-sufficiency gate "
                f"(results {file_mtime_label(TRUSTED_RESULTS)}, "
                f"gate {file_mtime_label(CONTENT_SUFFICIENCY)}) — re-run needed for fair calibration."
            )
    if results_mtime and FRAMEWORK_V2.is_file():
        if FRAMEWORK_V2.stat().st_mtime > results_mtime:
            flags.append(
                "AI batch predates framework_v2 file change — scores may not reflect current rubric."
            )
    return flags


def main() -> int:
    print("=" * 72)
    print("PORTFOLIO INTELLIGENCE — CALIBRATION REPORT")
    print("=" * 72)

    print("\n## Dataset selection")
    print(f"  PRIMARY:   {TRUSTED_RESULTS.relative_to(ROOT)}")
    print(f"  Human src: {POC_BATCH.relative_to(ROOT)} (Khushi/Eshan/Sushmitha, Brand Designer)")
    print(f"  REJECTED:  {LLM_CLEANED.name} — human scores only, no paired AI scores in-repo")
    print("  REJECTED:  maintain/ sheet export — small Rudra/Kshitija recalibration batches, not full hiring outcomes")

    rows = load_trusted_pairs()
    print(f"\n## Sample")
    print(f"  Paired rows (status=ok, both scores): {len(rows)}")

    if TRUSTED_RESULTS.is_file():
        raw = json.loads(TRUSTED_RESULTS.read_text(encoding="utf-8"))
        total = len(raw.get("results") or [])
        failed = sum(1 for r in raw.get("results") or [] if r.get("status") != "ok")
        print(f"  Total trusted37 results: {total} (non-ok: {failed})")
        if raw.get("summary"):
            print(f"  Batch summary (from run): {raw['summary']}")

    print("\n## Date range (file mtimes — rows have no timestamps)")
    for label, path in RUBRIC_MILESTONES:
        mt = file_mtime_label(path)
        print(f"  {label}: {mt or 'missing'}")

    if not rows:
        print("\nNo paired rows — cannot compute calibration metrics.")
        return 1

    signed_all = [r["signed_error"] for r in rows]
    abs_all = [r["abs_error"] for r in rows]
    print("\n## Overall error (paired subset)")
    print(f"  Mean signed error (AI − human): {sum(signed_all)/len(signed_all):+.3f}")
    print(f"  Mean absolute error:            {sum(abs_all)/len(abs_all):.3f}")
    print(f"  AI higher than human:           {sum(1 for s in signed_all if s > 0)}")
    print(f"  AI lower than human:            {sum(1 for s in signed_all if s < 0)}")
    print(f"  Exact match:                    {sum(1 for s in signed_all if s == 0)}")

    print("\n## Mean signed error by human score band")
    print(f"  {'Band':>4}  {'N':>4}  {'Mean signed':>12}  {'Mean |err|':>10}  over  under  exact")
    bands = band_stats(rows)
    for band in sorted(bands):
        b = bands[band]
        print(
            f"  {band:>4}  {b['n']:>4}  {b['mean_signed_error']:>+12.3f}  "
            f"{b['mean_abs_error']:>10.3f}  "
            f"{b['ai_over_human']:>5}  {b['ai_under_human']:>5}  {b['exact']:>5}"
        )

    print("\n## Staleness / rubric drift flags")
    flags = staleness_flags()
    if flags:
        for f in flags:
            print(f"  ⚠ {f}")
    else:
        print("  (none detected from file mtimes)")

    print("\n## Interpretation notes")
    print("  - Human scores are manual portfolio ratings (1–5), not Hire/Shortlist/Pass outcomes.")
    print("  - poc_batch_trusted.json has no rubric_version field — labels are not tied to a version in data.")
    print("  - trusted37 batch ran against pipeline ~2026-03-25; current code may differ.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
