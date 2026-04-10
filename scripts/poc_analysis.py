#!/usr/bin/env python3
"""Match poc_batch_trusted.json to scripts/baseline_results.json and report accuracy."""

import json
import os
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_behance_url_simple(raw: str) -> str:
    """https://www.behance.net/.../path lowercase, no trailing slash."""
    if not raw or not str(raw).strip():
        return ""
    s = str(raw).strip()
    s = re.sub(r"\s+", "", s)
    if not s.lower().startswith(("http://", "https://")):
        s = "https://" + s.lstrip("/")
    p = urlparse(s)
    host = (p.netloc or "").lower()
    if not host:
        return s.lower().rstrip("/")
    if host == "behance.net":
        host = "www.behance.net"
    path = (p.path or "").rstrip("/").lower()
    if not path.startswith("/"):
        path = "/" + path
    return f"https://{host}{path}".rstrip("/")


def main():
    gt_path = os.path.join(ROOT, "poc_batch_trusted.json")
    baseline_path = os.path.join(ROOT, "scripts", "baseline_results.json")
    out_path = os.path.join(ROOT, "scripts", "poc_results_detailed.json")

    with open(gt_path) as f:
        ground_truth = json.load(f)

    with open(baseline_path) as f:
        pipeline_data = json.load(f)

    pipeline_results = pipeline_data.get("results", [])

    # Multiple GT rows can map to same normalized URL — keep first or merge; use last-wins for human score if dupes
    gt_by_url = {}
    for r in ground_truth:
        key = normalize_behance_url_simple(r.get("portfolio", ""))
        if key:
            gt_by_url[key] = r

    matched = []

    for result in pipeline_results:
        if result.get("status") != "ok":
            continue
        url = normalize_behance_url_simple(result.get("portfolio_url", ""))
        if not url or url not in gt_by_url:
            continue
        gt = gt_by_url[url]
        human = gt.get("score")
        if human is None:
            continue
        human = float(human)
        ai = result.get("ai_score")
        if ai is None:
            continue
        ai = float(ai)
        matched.append(
            {
                "name": gt.get("name", ""),
                "portfolio": url,
                "reviewer": gt.get("reviewer", ""),
                "human_score": human,
                "ai_score": ai,
                "diff": abs(human - ai),
                "within_1": abs(human - ai) <= 1,
                "recommendation": result.get("ai_recommendation", result.get("recommendation", "")),
            }
        )

    print(f"Matched: {len(matched)} / {len(ground_truth)}")
    if not matched:
        print("No matches — check URL formatting between the two files")
        return

    within_1 = sum(1 for r in matched if r["within_1"])
    print("\n=== Overall Accuracy ===")
    print(f"Within 1 point: {within_1}/{len(matched)} = {within_1/len(matched)*100:.1f}%")

    high_value = [r for r in matched if r["human_score"] >= 4]
    false_rejects = [r for r in high_value if r["ai_score"] <= 2]
    print("\n=== False Reject Rate (human score 4+) ===")
    print(f"High value candidates: {len(high_value)}")
    denom = max(len(high_value), 1)
    print(f"False rejects (AI ≤2): {len(false_rejects)} = {len(false_rejects)/denom*100:.1f}%")
    for r in false_rejects:
        print(f"  ✗ {r['name']} | human: {r['human_score']} | ai: {r['ai_score']}")

    human_dist = Counter(int(r["human_score"]) for r in matched)
    ai_dist = Counter(round(r["ai_score"]) for r in matched)
    print("\n=== Score Distribution ===")
    print(f"{'Score':<8} {'Human':>8} {'AI':>8}")
    for s in range(1, 6):
        print(f"{s:<8} {human_dist.get(s, 0):>8} {ai_dist.get(s, 0):>8}")

    by_reviewer = defaultdict(list)
    for r in matched:
        by_reviewer[r["reviewer"]].append(r["within_1"])
    print("\n=== By Reviewer ===")
    for reviewer in sorted(by_reviewer.keys()):
        scores = by_reviewer[reviewer]
        pct = sum(scores) / len(scores) * 100
        print(f"  {reviewer}: {pct:.1f}% (n={len(scores)})")

    big_misses = sorted([r for r in matched if r["diff"] >= 2], key=lambda x: -x["diff"])
    print("\n=== Big Misses (diff >= 2) ===")
    if big_misses:
        for r in big_misses:
            print(f"  {r['name']} | human: {r['human_score']} | ai: {r['ai_score']} | diff: {r['diff']}")
    else:
        print("  None — clean run")

    with open(out_path, "w") as f:
        json.dump(matched, f, indent=2)
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
