#!/usr/bin/env python3
"""
Pick N portfolios (default 10) from rudra_kshitija_all_reports.json that:
  - status ok, full AI score + summary_reasoning
  - not already in reviewer1 / reviewer2 / default Maintain lists
  - not in scripts/unassigned_url_blocklist.json (cumulative unassigned deploys)
  - reviewer "" (unassigned) for human review

Writes:
  maintain/src/data/portfolios_unassigned.json
  maintain/src/data/ai_lookup_unassigned.json
  scripts/unassigned_batch_meta.json

Usage:
  python scripts/export_unassigned_batch.py
  python scripts/export_unassigned_batch.py --count 5
  python scripts/export_unassigned_batch.py --count 10
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALL_REPORTS = ROOT / "scripts" / "rudra_kshitija_all_reports.json"
MAINTAIN_DATA = ROOT / "maintain" / "src" / "data"
# Cumulative URLs already shown in unassigned deploys (append-only; avoids repeats after meta overwrite).
BLOCKLIST = ROOT / "scripts" / "unassigned_url_blocklist.json"

EXCLUDE_FILES = [
    MAINTAIN_DATA / "portfolios_reviewer1.json",
    MAINTAIN_DATA / "portfolios_reviewer2.json",
    MAINTAIN_DATA / "portfolios.json",
    # Prior unassigned exports — skip those URLs so each run is a fresh set.
    MAINTAIN_DATA / "portfolios_unassigned.json",
    MAINTAIN_DATA / "portfolios_unassigned10.json",
]


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
    return score, (reason or "").strip()


def urls_from_maintain(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {norm_url(r.get("portfolio", "")) for r in data}


def sync_blocklist_from_current_unassigned() -> None:
    """Persist any URLs in portfolios_unassigned.json onto the blocklist so repeats cannot slip in."""
    pf = MAINTAIN_DATA / "portfolios_unassigned.json"
    if not pf.exists():
        return
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, list):
        return
    raw_urls = [str(row.get("portfolio", "")).strip() for row in data if isinstance(row, dict)]
    block_urls: list[str] = []
    if BLOCKLIST.exists():
        try:
            raw = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                block_urls = [str(x) for x in raw]
        except (json.JSONDecodeError, OSError):
            pass
    seen = {norm_url(u) for u in block_urls}
    changed = False
    for u in raw_urls:
        nu = norm_url(u)
        if nu and nu not in seen:
            block_urls.append(u)
            seen.add(nu)
            changed = True
    if changed:
        BLOCKLIST.parent.mkdir(parents=True, exist_ok=True)
        BLOCKLIST.write_text(json.dumps(block_urls, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pick_n(rows: list[dict], pick: int) -> list[dict]:
    """Prefer candidates across score buckets 1–5; multiple rounds until pick reached."""
    usable: list[tuple[int, dict]] = []
    for r in rows:
        sc, reason = ai_entry(r["report"])
        if sc is None or not reason:
            continue
        b = int(round(sc))
        b = max(1, min(5, b))
        usable.append((b, r))

    by_bucket: dict[int, list[dict]] = {i: [] for i in range(1, 6)}
    for b, r in usable:
        by_bucket[b].append(r)
    for b in range(1, 6):
        random.shuffle(by_bucket[b])

    order = [3, 4, 2, 5, 1, 4, 2, 5, 1, 3, 4, 2, 5, 1]
    picked: list[dict] = []
    seen_url: set[str] = set()

    for b in order:
        if len(picked) >= pick:
            break
        pool = by_bucket.get(b, [])
        while pool:
            r = pool.pop(0)
            u = norm_url(r.get("portfolio_url", ""))
            if u and u not in seen_url:
                seen_url.add(u)
                picked.append(r)
                break

    # More rounds: cycle buckets until full
    round_order = [3, 4, 2, 5, 1]
    guard = 0
    while len(picked) < pick and guard < 50:
        guard += 1
        progressed = False
        for b in round_order:
            if len(picked) >= pick:
                break
            pool = by_bucket.get(b, [])
            while pool:
                r = pool.pop(0)
                u = norm_url(r.get("portfolio_url", ""))
                if u and u not in seen_url:
                    seen_url.add(u)
                    picked.append(r)
                    progressed = True
                    break
        if not progressed:
            break

    if len(picked) < pick:
        rest = [r for r in rows if norm_url(r.get("portfolio_url", "")) not in seen_url]
        random.shuffle(rest)
        n = len(rest)
        if n:
            step = max(1, n // max(1, pick - len(picked)))
            for i in range(0, n, step):
                if len(picked) >= pick:
                    break
                r = rest[i]
                u = norm_url(r.get("portfolio_url", ""))
                if u and u not in seen_url:
                    seen_url.add(u)
                    picked.append(r)
        for r in rest:
            if len(picked) >= pick:
                break
            u = norm_url(r.get("portfolio_url", ""))
            if u and u not in seen_url:
                seen_url.add(u)
                picked.append(r)

    return picked[:pick]


def to_portfolio_row(r: dict) -> dict:
    h = r.get("human") or {}
    return {
        "name": h.get("name"),
        "jobProfile": h.get("jobProfile") or "Brand Designer",
        "portfolio": r.get("portfolio_url"),
        "score": h.get("score"),
        "comment": h.get("comment") or "",
        "reviewer": "",
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10, help="Batch size (default 10; use 5 for smaller batch)")
    args = ap.parse_args()
    pick = max(1, args.count)

    if not ALL_REPORTS.exists():
        print("Missing", ALL_REPORTS, file=sys.stderr)
        return 1

    sync_blocklist_from_current_unassigned()

    used: set[str] = set()
    for p in EXCLUDE_FILES:
        used |= urls_from_maintain(p)

    if BLOCKLIST.exists():
        try:
            raw = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for u in raw:
                    nu = norm_url(str(u))
                    if nu:
                        used.add(nu)
        except (json.JSONDecodeError, OSError):
            pass

    meta_path = ROOT / "scripts" / "unassigned_batch_meta.json"
    if meta_path.exists():
        try:
            prev = json.loads(meta_path.read_text(encoding="utf-8"))
            for row in prev.get("picked", []):
                u = norm_url(row.get("portfolio_url", ""))
                if u:
                    used.add(u)
        except (json.JSONDecodeError, OSError):
            pass

    state = json.loads(ALL_REPORTS.read_text(encoding="utf-8"))
    pool: list[dict] = []
    for r in state.get("results", []):
        if r.get("status") != "ok":
            continue
        rep = r.get("report") or {}
        sc, reason = ai_entry(rep)
        if sc is None or not reason:
            continue
        if sc <= 0:
            continue
        low = reason.lower()
        if "no projects found" in low or "analysis failed" in low:
            continue
        url = norm_url(r.get("portfolio_url", ""))
        if not url or url in used:
            continue
        pool.append(r)

    if len(pool) < pick:
        print(f"Need at least {pick} candidates; got {len(pool)}", file=sys.stderr)
        return 1

    selected = pick_n(pool, pick)
    MAINTAIN_DATA.mkdir(parents=True, exist_ok=True)
    (ROOT / "scripts").mkdir(parents=True, exist_ok=True)

    pf = MAINTAIN_DATA / "portfolios_unassigned.json"
    lf = MAINTAIN_DATA / "ai_lookup_unassigned.json"
    mf = ROOT / "scripts" / "unassigned_batch_meta.json"

    pf.write_text(
        json.dumps([to_portfolio_row(r) for r in selected], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    lf.write_text(json.dumps(to_lookup_map(selected), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    meta = {
        "count": pick,
        "source": str(ALL_REPORTS.relative_to(ROOT)),
        "excluded_maintain_files": [str(p.relative_to(ROOT)) for p in EXCLUDE_FILES],
        "pool_size_before_pick": len(pool),
        "picked": [
            {
                "name": (r.get("human") or {}).get("name"),
                "portfolio_url": r.get("portfolio_url"),
                "ai_score": ai_entry(r.get("report") or {})[0],
            }
            for r in selected
        ],
    }
    mf.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    block_urls: list[str] = []
    if BLOCKLIST.exists():
        try:
            raw = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                block_urls = [str(x) for x in raw]
        except (json.JSONDecodeError, OSError):
            pass
    seen_bl = {norm_url(u) for u in block_urls}
    for r in selected:
        u = norm_url(r.get("portfolio_url", ""))
        if u and u not in seen_bl:
            block_urls.append(r.get("portfolio_url", "").strip())
            seen_bl.add(u)
    BLOCKLIST.write_text(json.dumps(block_urls, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {pf.relative_to(ROOT)}, {lf.relative_to(ROOT)}")
    print(f"Meta: {mf.relative_to(ROOT)}")
    for row in meta["picked"]:
        print(f"  • {row['name']}  AI≈{row['ai_score']}  {row['portfolio_url']}")
    print("\nMaintain: VITE_REVIEWER_SLOT=3  (dev:unassigned / build:unassigned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
