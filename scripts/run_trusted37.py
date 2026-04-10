#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import run_portfolio_intelligence_pipeline

INPUT = ROOT / "poc_batch_trusted.json"
OUT = ROOT / "scripts" / "trusted37_results.json"
N = 37


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    return u


def main() -> int:
    rows = json.loads(INPUT.read_text(encoding="utf-8"))[:N]

    if OUT.exists():
        saved = json.loads(OUT.read_text(encoding="utf-8"))
        results = saved.get("results", [])
    else:
        results = []
    done = {r.get("portfolio_url") for r in results if r.get("portfolio_url")}

    for i, r in enumerate(rows, 1):
        url = normalize_url(r.get("portfolio", ""))
        if not url or url in done:
            continue
        print(f"\n[{i}/{N}] {r.get('name', '—')} :: {url}", flush=True)
        out = {
            "name": r.get("name", ""),
            "reviewer": r.get("reviewer", ""),
            "role": r.get("jobProfile", ""),
            "portfolio_url": url,
            "designer_score": int(r.get("score", 0) or 0),
            "status": "pending",
        }
        try:
            rep = run_portfolio_intelligence_pipeline(
                url, candidate_role=(r.get("jobProfile") or "").strip() or None
            )
            fc = (rep or {}).get("final_scorecard") or {}
            out["ai_score"] = fc.get("average_quality_score")
            out["ai_recommendation"] = fc.get("hire_recommendation")
            out["summary_reasoning"] = fc.get("summary_reasoning")
            out["status"] = "ok"
        except Exception as e:
            out["status"] = "failed"
            out["error"] = str(e)
        results.append(out)
        OUT.write_text(
            json.dumps({"input_file": str(INPUT), "target_n": N, "results": results}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    ok = [x for x in results if x.get("status") == "ok" and x.get("ai_score") is not None and (x.get("ai_score") or 0) > 0]
    if ok:
        gaps = [abs(float(x["designer_score"]) - float(x["ai_score"])) for x in ok]
        mae = sum(gaps) / len(gaps)
        exact = sum(1 for g in gaps if g == 0) / len(gaps)
        within_1 = sum(1 for g in gaps if g <= 1) / len(gaps)
    else:
        mae = exact = within_1 = None

    summary = {
        "tested": len(results),
        "valid_for_metrics": len(ok),
        "mae": round(mae, 2) if mae is not None else None,
        "exact_match_rate": round(exact * 100, 1) if exact is not None else None,
        "within_1_rate": round(within_1 * 100, 1) if within_1 is not None else None,
    }
    final = {"input_file": str(INPUT), "target_n": N, "results": results, "summary": summary}
    OUT.write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\nSUMMARY", summary, flush=True)
    print("Saved ->", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
