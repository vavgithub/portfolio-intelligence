#!/usr/bin/env python3
"""
Baseline measurement: run portfolio pipeline on a stratified sample from cleaned Geode data,
compare AI scores vs designer scores (MAE, exact, within-1, Hire/Pass agreement).
Saves progress every 5 runs; resumes from scripts/baseline_results.json if re-run.
"""

import argparse
import json
import random
import sys
from pathlib import Path
from urllib.parse import urlparse

# Project root (parent of scripts/)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_INPUT = ROOT / "cleaned_geode.json"
FALLBACK_INPUT = ROOT / "HireHive.candidates.llm_cleaned.json"
BEHANCE_SAMPLE_PATH = ROOT / "scripts" / "behance_baseline_sample.json"
RESULTS_PATH = ROOT / "scripts" / "baseline_results.json"
PROGRESS_EVERY = 5
RANDOM_SEED = 42

# Stratified sample sizes: score 1,2,3,4,5
SAMPLE_COUNTS = {1: 10, 2: 10, 3: 10, 4: 10, 5: 5}

# Non-portfolio domains: do not run pipeline (login walls, social profiles, etc.)
SKIP_DOMAINS = ("linkedin.com", "instagram.com", "twitter.com", "facebook.com", "x.com")


def is_skip_domain(url: str) -> bool:
    """True if URL is a known non-portfolio domain (LinkedIn, Instagram, etc.)."""
    if not url:
        return True
    try:
        netloc = urlparse(url).netloc.lower()
        return any(skip in netloc for skip in SKIP_DOMAINS)
    except Exception:
        return False


def normalize_url(portfolio: str) -> str:
    if not portfolio or not isinstance(portfolio, str):
        return ""
    p = portfolio.strip()
    if not p:
        return ""
    if p.startswith("http://") or p.startswith("https://"):
        return p
    return "https://" + p


def designer_recommendation(score: int) -> str:
    """Designer 1-2 = Pass, 3 = Shortlist, 4-5 = Hire."""
    if score <= 2:
        return "Pass"
    if score == 3:
        return "Shortlist"
    return "Hire"


def load_records(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    raise ValueError("Expected JSON array or {records: [...]}")


def dedupe_by_portfolio_url(records: list[dict]) -> list[dict]:
    """One row per portfolio URL (first occurrence wins). Prevents same URL in multiple strata."""
    seen = {}
    for r in records:
        url = normalize_url(r.get("portfolio") or r.get("portfolioUrl") or "")
        if not url:
            continue
        if url not in seen:
            seen[url] = r
    return list(seen.values())


def stratified_sample(records: list[dict], seed: int = RANDOM_SEED, max_total: int | None = None) -> list[dict]:
    """Pick per score stratum. If max_total set (e.g. --limit 10), take max_total//5 from each stratum to keep stratification."""
    by_score = {1: [], 2: [], 3: [], 4: [], 5: []}
    for r in records:
        s = r.get("score")
        if s is None:
            continue
        try:
            s = int(s)
        except (TypeError, ValueError):
            continue
        if s in by_score:
            by_score[s].append(r)

    rng = random.Random(seed)
    out = []
    for score, full_n in SAMPLE_COUNTS.items():
        pool = by_score[score]
        if max_total is not None and max_total > 0:
            n = min(max_total // 5, full_n, len(pool))
        else:
            n = min(full_n, len(pool))
        if n <= 0:
            continue
        if len(pool) <= n:
            chosen = pool[:]
        else:
            chosen = rng.sample(pool, n)
        out.extend(chosen)
    return out


def run_one(url: str, candidate_role=None):
    """Run pipeline and return (report_dict or None, error_message or None). candidate_role = jobProfile from Geode (e.g. UI UX, Brand Designer, Motion Designer)."""
    from app.main import run_portfolio_intelligence_pipeline as run_pipeline
    try:
        report = run_pipeline(url, candidate_role=candidate_role)
        return report, None
    except Exception as e:
        return None, str(e)


def compute_metrics(results: list[dict]) -> dict:
    """results: list of {designer_score, ai_score, ...}. Exclude score-0 (failed scrapes)."""
    ok = [r for r in results if r.get("status") == "ok" and r.get("ai_score") is not None and (r.get("ai_score") or 0) > 0]
    if not ok:
        return {
            "mae": None,
            "exact_match_rate": None,
            "within_1_rate": None,
            "hire_pass_agreement_rate": None,
            "n": 0,
        }

    gaps = [abs(r["designer_score"] - r["ai_score"]) for r in ok]
    mae = sum(gaps) / len(ok)
    exact = sum(1 for r in ok if r["designer_score"] == r["ai_score"]) / len(ok)
    within_1 = sum(1 for g in gaps if g <= 1) / len(ok)

    # Hire/Pass agreement: designer 1-2 Pass, 3 Shortlist, 4-5 Hire
    agreed = 0
    for r in ok:
        dr = designer_recommendation(r["designer_score"])
        ar = r.get("ai_recommendation") or "Pass"
        if dr == ar:
            agreed += 1
    hire_pass_rate = agreed / len(ok)

    return {
        "mae": round(mae, 2),
        "exact_match_rate": round(exact * 100, 1),
        "within_1_rate": round(within_1 * 100, 1),
        "hire_pass_agreement_rate": round(hire_pass_rate * 100, 1),
        "n": len(ok),
    }


def breakdown_by(results: list[dict], key: str) -> dict:
    """key = 'role' or 'reviewer'. Exclude score-0 (failed scrapes). Returns { value: { within_1_rate, n } }."""
    ok = [r for r in results if r.get("status") == "ok" and r.get("ai_score") is not None and (r.get("ai_score") or 0) > 0]
    by_val = {}
    for r in ok:
        val = (r.get(key) or "Unknown").strip() or "Unknown"
        if val not in by_val:
            by_val[val] = []
        by_val[val].append(r)

    breakdown = {}
    for val, rows in by_val.items():
        gaps = [abs(x["designer_score"] - x["ai_score"]) for x in rows]
        within_1 = sum(1 for g in gaps if g <= 1) / len(rows) if rows else 0
        breakdown[val] = {"within_1_rate": round(within_1 * 100, 1), "n": len(rows)}
    return breakdown


def main():
    ap = argparse.ArgumentParser(description="Baseline measurement: AI vs designer scores.")
    ap.add_argument("--limit", type=int, default=None, metavar="N", help="Run only first N portfolios (for smoke test)")
    args = ap.parse_args()

    # Load from Behance-only sample if it exists
    if BEHANCE_SAMPLE_PATH.exists():
        print(f"Loading Behance-only sample: {BEHANCE_SAMPLE_PATH}")
        sample = load_records(BEHANCE_SAMPLE_PATH)
        sample = dedupe_by_portfolio_url(sample)
        for r in sample:
            if r.get("portfolio", "").startswith("behance.net"):
                r["portfolio"] = "https://" + r["portfolio"]
        if args.limit is not None and args.limit > 0:
            sample = sample[: args.limit]
        input_path = BEHANCE_SAMPLE_PATH
    else:
        input_path = DEFAULT_INPUT if DEFAULT_INPUT.exists() else FALLBACK_INPUT
        if not input_path.exists():
            print(f"Input not found: {DEFAULT_INPUT} or {FALLBACK_INPUT}")
            sys.exit(1)
        records = load_records(input_path)
        records = dedupe_by_portfolio_url(records)
        sample = stratified_sample(records, max_total=args.limit if (args.limit is not None and args.limit > 0) else None)

    # Resume: load existing results if any
    existing = []
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        existing = saved.get("results") or []
        done_urls = {r.get("portfolio_url") for r in existing if r.get("portfolio_url")}
    else:
        done_urls = set()

    # Build list of (url, record) for remaining only
    to_run = []
    for rec in sample:
        url = normalize_url(rec.get("portfolio") or rec.get("portfolioUrl") or "")
        if not url:
            continue
        if url in done_urls:
            continue
        to_run.append((url, rec))

    results = list(existing)
    failed_count = sum(1 for r in results if r.get("status") == "failed")

    print(f"Total in sample: {len(sample)} | Already done: {len(existing)} | To run: {len(to_run)}")
    if not to_run:
        print("Nothing to run (all done or no URLs). Summary from cache.")
    else:
        for i, (url, rec) in enumerate(to_run):
            designer_score = int(rec.get("score", 0))
            reviewer = rec.get("reviewer") or ""
            role = (rec.get("jobProfile") or rec.get("role") or "").strip()
            row = {
                "portfolio_url": url,
                "designer_score": designer_score,
                "reviewer": reviewer,
                "role": role,
                "status": "pending",
            }
            if is_skip_domain(url):
                row["status"] = "skipped"
                row["error"] = "non-portfolio URL (linkedin/instagram/twitter/facebook)"
                results.append(row)
                if (len(results) % PROGRESS_EVERY == 0) or (i == len(to_run) - 1):
                    payload = {"input_file": str(input_path), "sample_size": len(sample), "results": results, "failed_count": failed_count}
                    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2, ensure_ascii=False)
                continue
            report, err = run_one(url, candidate_role=role or None)
            if report is None:
                row["status"] = "failed"
                row["error"] = err
                failed_count += 1
            else:
                fc = report.get("final_scorecard") or {}
                row["ai_score"] = fc.get("average_quality_score")
                row["ai_recommendation"] = fc.get("hire_recommendation")
                row["status"] = "ok"
            results.append(row)

            # Save progress every PROGRESS_EVERY
            if (len(results) % PROGRESS_EVERY == 0) or (i == len(to_run) - 1):
                payload = {
                    "input_file": str(input_path),
                    "sample_size": len(sample),
                    "results": results,
                    "failed_count": failed_count,
                }
                RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(RESULTS_PATH, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                print(f"  Saved progress: {len(results)} results")

    # Final save
    payload = {
        "input_file": str(input_path),
        "sample_size": len(sample),
        "results": results,
        "failed_count": failed_count,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Metrics (only ok rows)
    metrics = compute_metrics(results)
    by_role = breakdown_by(results, "role")
    by_reviewer = breakdown_by(results, "reviewer")

    # Print summary
    print()
    print("─────────────────────────────────")
    print("BASELINE MEASUREMENT RESULTS")
    print("─────────────────────────────────")
    print(f"Total portfolios tested: {len(results)}")
    print(f"Valid for metrics (excl. zeros): {metrics['n']}")
    if metrics["n"]:
        print(f"MAE: {metrics['mae']}")
        print(f"Exact match: {metrics['exact_match_rate']}%")
        print(f"Within 1 point: {metrics['within_1_rate']}%  ← target 80% after calibration")
        print(f"Hire/Pass agreement: {metrics['hire_pass_agreement_rate']}%")
    else:
        print("No successful runs to compute metrics.")
    print(f"Failures: {failed_count}")
    skipped_count = sum(1 for r in results if r.get("status") == "skipped")
    if skipped_count:
        print(f"Skipped (non-portfolio URL): {skipped_count}")
    print()
    print("BY ROLE:")
    for role in sorted(by_role.keys()):
        r = by_role[role]
        print(f"  {role:<20} → within 1: {r['within_1_rate']}%  (n={r['n']})")
    print()
    print("BY REVIEWER:")
    for rev in sorted(by_reviewer.keys()):
        r = by_reviewer[rev]
        print(f"  {rev:<20} → within 1: {r['within_1_rate']}%  (n={r['n']})")
    print("─────────────────────────────────")
    print(f"Full results: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
