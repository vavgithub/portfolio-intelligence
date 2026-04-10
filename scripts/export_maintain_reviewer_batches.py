#!/usr/bin/env python3
"""
Build Maintain data for two Vercel deploys (reviewer1 / reviewer2) from
scripts/rudra_kshitija_all_reports.json.

Reviewer1 = Rudra Sindwani, Reviewer2 = Kshitija Chavan.
Picks 5 portfolios each with status ok, AI score + summary_reasoning present.
Selection favors spread across score bands (1–5).

Usage:
  python scripts/export_maintain_reviewer_batches.py

Writes:
  maintain/src/data/portfolios_reviewer1.json
  maintain/src/data/ai_lookup_reviewer1.json
  maintain/src/data/portfolios_reviewer2.json
  maintain/src/data/ai_lookup_reviewer2.json

Deploy: copy the pair for that reviewer to portfolios.json + ai_lookup.json, then build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALL_REPORTS = ROOT / "scripts" / "rudra_kshitija_all_reports.json"
DATA = ROOT / "maintain" / "src" / "data"

REVIEWER1 = "Rudra Sindwani"
REVIEWER2 = "Kshitija Chavan"
PICK = 5


def norm_url(url: str) -> str:
    u = (url or "").strip().split("#", 1)[0].rstrip("/")
    return u.lower()


def ai_entry(report: dict) -> tuple[float | None, str]:
    fc = report.get("final_scorecard") or {}
    raw = fc.get("average_quality_score")
    try:
        score = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        score = None
    reason = fc.get("summary_reasoning")
    if reason is not None and not isinstance(reason, str):
        reason = str(reason)
    reason = (reason or "").strip()
    return score, reason


def pick_five(rows: list[dict]) -> list[dict]:
    """Prefer one candidate per rounded score bucket 1..5, then fill."""
    usable: list[tuple[int, dict]] = []
    for r in rows:
        sc, _ = ai_entry(r["report"])
        if sc is None:
            continue
        b = int(round(sc))
        b = max(1, min(5, b))
        usable.append((b, r))

    by_bucket: dict[int, list[dict]] = {i: [] for i in range(1, 6)}
    for b, r in usable:
        by_bucket[b].append(r)

    order = [3, 4, 2, 5, 1, 4, 2, 5, 1]  # center-out diversity
    picked: list[dict] = []
    seen_url: set[str] = set()
    for b in order:
        if len(picked) >= PICK:
            break
        pool = by_bucket.get(b, [])
        while pool:
            r = pool.pop(0)
            u = norm_url(r.get("portfolio_url", ""))
            if u and u not in seen_url:
                seen_url.add(u)
                picked.append(r)
                break

    if len(picked) < PICK:
        rest = [r for r in rows if norm_url(r.get("portfolio_url", "")) not in seen_url]
        rest.sort(key=lambda x: ai_entry(x["report"])[0] or 0.0)
        n = len(rest)
        if n:
            idxs = [0, n // 4, n // 2, (3 * n) // 4, n - 1]
            for i in idxs:
                if len(picked) >= PICK:
                    break
                if 0 <= i < n:
                    r = rest[i]
                    u = norm_url(r.get("portfolio_url", ""))
                    if u and u not in seen_url:
                        seen_url.add(u)
                        picked.append(r)
        for r in rest:
            if len(picked) >= PICK:
                break
            u = norm_url(r.get("portfolio_url", ""))
            if u and u not in seen_url:
                seen_url.add(u)
                picked.append(r)

    return picked[:PICK]


def to_portfolio_row(r: dict) -> dict:
    h = r.get("human") or {}
    return {
        "name": h.get("name"),
        "jobProfile": h.get("jobProfile") or "Brand Designer",
        "portfolio": r.get("portfolio_url"),
        "score": h.get("score"),
        "comment": h.get("comment") or "",
        "reviewer": h.get("reviewer") or "",
    }


def to_lookup_map(selected: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in selected:
        rep = r.get("report") or {}
        ident = rep.get("candidate_identity") or {}
        url = ident.get("url") or r.get("portfolio_url")
        if not url:
            continue
        sc, reason = ai_entry(rep)
        if sc is None:
            continue
        ai = float(sc)
        key = norm_url(str(url))
        if not key:
            continue
        out[key] = {
            "aiScore": round(ai, 2) if ai != int(ai) else int(ai),
            "aiReasoning": reason or "—",
        }
    return out


def run() -> int:
    if not ALL_REPORTS.exists():
        print("Missing", ALL_REPORTS, file=sys.stderr)
        return 1
    state = json.loads(ALL_REPORTS.read_text(encoding="utf-8"))
    results = state.get("results", [])

    def pool(reviewer: str) -> list[dict]:
        rows: list[dict] = []
        for r in results:
            if r.get("status") != "ok":
                continue
            h = r.get("human") or {}
            if (h.get("reviewer") or "") != reviewer:
                continue
            rep = r.get("report") or {}
            sc, reason = ai_entry(rep)
            if sc is None or not reason:
                continue
            rows.append(r)
        return rows

    p1 = pool(REVIEWER1)
    p2 = pool(REVIEWER2)
    if len(p1) < PICK or len(p2) < PICK:
        print(
            f"Need at least {PICK} ok rows per reviewer with AI score + summary. "
            f"Got Rudra={len(p1)}, Kshitija={len(p2)}",
            file=sys.stderr,
        )
        return 1

    s1 = pick_five(p1)
    s2 = pick_five(p2)

    DATA.mkdir(parents=True, exist_ok=True)

    def write_pair(
        suffix: str,
        label: str,
        selected: list[dict],
    ) -> None:
        pf = DATA / f"portfolios_{suffix}.json"
        lf = DATA / f"ai_lookup_{suffix}.json"
        pf.write_text(
            json.dumps([to_portfolio_row(r) for r in selected], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        lf.write_text(
            json.dumps(to_lookup_map(selected), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{label}: {len(selected)} portfolios -> {pf.name}, {lf.name}")
        for r in selected:
            h = r.get("human") or {}
            sc, _ = ai_entry(r.get("report") or {})
            print(f"  • {h.get('name')}  AI≈{sc}  {r.get('portfolio_url')}")

    write_pair("reviewer1", "Reviewer1 (Rudra Sindwani)", s1)
    write_pair("reviewer2", "Reviewer2 (Kshitija Chavan)", s2)
    print(
        "\nLocal dev / build (no file copying):\n"
        "  cd maintain && npm run dev              # Rudra (reviewer 1)\n"
        "  cd maintain && npm run dev:reviewer2    # Kshitija\n"
        "  npm run build:reviewer1   # dist for Rudra\n"
        "  npm run build:reviewer2   # dist for Kshitija\n"
        "\nVercel: set env VITE_REVIEWER_SLOT=1 or 2 per project (or use build:reviewer* locally)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
