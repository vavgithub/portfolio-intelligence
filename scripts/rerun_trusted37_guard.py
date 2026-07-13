#!/usr/bin/env python3
"""Re-run trusted37 batch with guard-cap analyzer; write fresh results JSON."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import run_portfolio_intelligence_pipeline
from app.analyzer import apply_visual_polish_guard_cap
from app.scoring import aggregate_scores

from app.portfolio_url import normalize_portfolio_url

INPUT = ROOT / "poc_batch_trusted.json"
OUT = ROOT / "scripts" / "trusted37_results_guard_cap.json"
N = 37


def _recap_project(guard_row: dict, role: str) -> dict:
    raw = guard_row.get("score_before_guard_cap")
    if raw is None:
        raw = guard_row.get("score")
    out = {
        "score": raw,
        "guard_q1_positioning": guard_row.get("guard_q1_positioning"),
        "guard_q2_typography": guard_row.get("guard_q2_typography"),
        "guard_q3_distinctive": guard_row.get("guard_q3_distinctive"),
    }
    capped = apply_visual_polish_guard_cap(out, role)
    guard_row["score"] = capped.get("score")
    guard_row["score_before_guard_cap"] = capped.get("score_before_guard_cap")
    guard_row["guard_cap_applied"] = capped.get("guard_cap_applied")
    guard_row["guard_q1_positioning"] = capped.get("guard_q1_positioning")
    guard_row["guard_q2_typography"] = capped.get("guard_q2_typography")
    guard_row["guard_q3_distinctive"] = capped.get("guard_q3_distinctive")
    return {
        "project_title": guard_row.get("title"),
        "score": capped.get("score"),
        "score_before_guard_cap": capped.get("score_before_guard_cap"),
        "guard_cap_applied": capped.get("guard_cap_applied"),
        "guard_q1_positioning": capped.get("guard_q1_positioning"),
        "guard_q2_typography": capped.get("guard_q2_typography"),
        "guard_q3_distinctive": capped.get("guard_q3_distinctive"),
    }


def recap_existing_results(results: list[dict]) -> int:
    """Re-apply guard-cap logic to stored project_guards without re-running Gemini."""
    updated = 0
    for row in results:
        guards = row.get("project_guards") or []
        if not guards:
            continue
        role = (row.get("role") or "").strip() or None
        project_results = []
        for g in guards:
            project_results.append(_recap_project(g, role))
        fc = aggregate_scores(
            [
                {
                    **pr,
                    "project_title": pr.get("project_title") or g.get("title"),
                }
                for pr, g in zip(project_results, guards)
            ]
        )
        row["ai_score"] = fc.get("average_quality_score")
        row["ai_recommendation"] = fc.get("hire_recommendation")
        row["summary_reasoning"] = fc.get("summary_reasoning")
        updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recap-only",
        action="store_true",
        help="Re-apply guard-cap to stored project_guards only (no pipeline re-run).",
    )
    args = parser.parse_args()

    rows = json.loads(INPUT.read_text(encoding="utf-8"))[:N]
    results = []
    if OUT.exists():
        saved = json.loads(OUT.read_text(encoding="utf-8"))
        results = saved.get("results", [])

    if args.recap_only:
        n = recap_existing_results(results)
        OUT.write_text(
            json.dumps(
                {
                    "input_file": str(INPUT),
                    "target_n": N,
                    "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "guard_cap_logic": "unclear-aware-v2",
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Recapped {n} rows -> {OUT}", flush=True)
        return 0

    done = {r.get("portfolio_url") for r in results if r.get("portfolio_url")}

    t0 = time.time()
    for i, r in enumerate(rows, 1):
        raw_portfolio = r.get("portfolio", "")
        url, url_err = normalize_portfolio_url(raw_portfolio)
        if url and url in done:
            continue
        print(f"\n[{i}/{N}] {r.get('name', '—')} :: {url or raw_portfolio}", flush=True)
        out = {
            "name": r.get("name", ""),
            "reviewer": r.get("reviewer", ""),
            "role": r.get("jobProfile", ""),
            "portfolio_url": url or raw_portfolio,
            "portfolio_raw": raw_portfolio,
            "designer_score": int(r.get("score", 0) or 0),
            "comment": r.get("comment", ""),
            "status": "pending",
        }
        if url_err == "invalid_link":
            out["status"] = "invalid_link"
            out["error"] = "No Behance URL found in multi-link portfolio field"
            results.append(out)
            OUT.write_text(
                json.dumps(
                    {
                        "input_file": str(INPUT),
                        "target_n": N,
                        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "results": results,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            continue
        if url_err == "empty" or not url:
            out["status"] = "invalid_link"
            out["error"] = "Empty portfolio field"
            results.append(out)
            OUT.write_text(
                json.dumps(
                    {
                        "input_file": str(INPUT),
                        "target_n": N,
                        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "results": results,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            continue
        try:
            rep = run_portfolio_intelligence_pipeline(
                url,
                candidate_role=(r.get("jobProfile") or "").strip() or None,
                max_projects=3,
            )
            fc = (rep or {}).get("final_scorecard") or {}
            out["ai_score"] = fc.get("average_quality_score")
            out["ai_recommendation"] = fc.get("hire_recommendation")
            out["summary_reasoning"] = fc.get("summary_reasoning")
            out["pipeline_status"] = rep.get("status")
            out["status"] = "ok"
            guards = []
            for proj in rep.get("visual_analysis_results") or []:
                guards.append(
                    {
                        "title": proj.get("project_title"),
                        "score": proj.get("score"),
                        "score_before_guard_cap": proj.get("score_before_guard_cap"),
                        "guard_cap_applied": proj.get("guard_cap_applied"),
                        "guard_q1_positioning": proj.get("guard_q1_positioning"),
                        "guard_q2_typography": proj.get("guard_q2_typography"),
                        "guard_q3_distinctive": proj.get("guard_q3_distinctive"),
                    }
                )
            out["project_guards"] = guards
        except Exception as e:
            out["status"] = "failed"
            out["error"] = str(e)
        results.append(out)
        OUT.write_text(
            json.dumps(
                {
                    "input_file": str(INPUT),
                    "target_n": N,
                    "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f} min -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
