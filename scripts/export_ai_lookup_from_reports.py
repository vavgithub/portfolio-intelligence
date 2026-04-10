#!/usr/bin/env python3
"""
Build maintain/src/data/ai_lookup.json from report_*.json pipeline outputs.

Keys are normalized portfolio URLs (see normalizePortfolioUrl in the Maintain app).
Run from repo root after new reports are generated:

  python scripts/export_ai_lookup_from_reports.py

If multiple reports share the same URL, the newest file (by mtime) wins.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def norm_url(url: str) -> str:
    u = url.strip()
    if not u:
        return ""
    u = u.split("#", 1)[0].rstrip("/")
    return u.lower()


def load_report(path: Path) -> tuple[str | None, float | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, None
    ident = data.get("candidate_identity") or {}
    url = ident.get("url")
    if not url or not isinstance(url, str):
        return None, None, None
    fc = data.get("final_scorecard") or {}
    score = fc.get("average_quality_score")
    try:
        ai = float(score) if score is not None else None
    except (TypeError, ValueError):
        ai = None
    reason = fc.get("summary_reasoning")
    if reason is not None and not isinstance(reason, str):
        reason = str(reason)
    return url, ai, reason


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_path = root / "maintain" / "src" / "data" / "ai_lookup.json"
    reports = sorted(root.glob("report_*.json"), key=lambda p: p.stat().st_mtime)
    merged: dict[str, dict[str, object]] = {}
    for path in reports:
        url, ai, reason = load_report(path)
        if not url or ai is None:
            continue
        key = norm_url(url)
        if not key:
            continue
        merged[key] = {
            "aiScore": round(float(ai), 2) if float(ai) != int(ai) else int(ai),
            "aiReasoning": (reason or "").strip() or "—",
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged)} URL(s) -> {out_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
