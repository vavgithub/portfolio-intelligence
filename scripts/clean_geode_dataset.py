#!/usr/bin/env python3
"""
Clean Geode portfolio evaluation dataset for ML / fine-tuning prep.

- Filter out Video Editors (or any role you exclude).
- Optionally drop score=1 rows where comment suggests "cannot view" / inaccessible.
- Output cleaned JSON + a short summary (counts, score distribution, roles).

Usage:
  python scripts/clean_geode_dataset.py path/to/geode_evaluations.json [--output cleaned.json]
  python scripts/clean_geode_dataset.py path/to/geode_evaluations.json --dry-run

Expected input shape (flexible):
  - List of dicts, or
  - {"records": [...]} or {"data": [...]}
  Each record should have at least: name, jobProfile (or role), portfolio URL, score (1-5).
  Optional: reviewer, comment.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Roles to exclude from training (adjust to your needs)
EXCLUDED_ROLES = {"Video Editor", "video editor", "VIDEO EDITOR"}

# Phrases in comment that suggest "cannot view" / tech failure, not poor design
INACCESSIBLE_PATTERNS = [
    r"cannot view",
    r"can\'t view",
    r"unable to view",
    r"link (is )?broken",
    r"page (not )?found",
    r"404",
    r"not accessible",
    r"doesn\'t load",
    r"does not load",
    r"blocked",
    r"private",
    r"login required",
    r"password protected",
]


def looks_inaccessible(comment: str | None) -> bool:
    if not comment or not isinstance(comment, str):
        return False
    text = comment.lower().strip()
    for pat in INACCESSIBLE_PATTERNS:
        if re.search(pat, text, re.I):
            return True
    return False


def normalize_record(raw: dict) -> dict:
    """Map common field names to canonical keys."""
    out = dict(raw)
    # jobProfile vs role vs job_profile
    role = (
        raw.get("jobProfile")
        or raw.get("job_profile")
        or raw.get("role")
        or raw.get("jobRole")
        or ""
    )
    out["_role_normalized"] = (role or "").strip()
    out["_score"] = raw.get("score")
    if out["_score"] is not None:
        try:
            out["_score"] = int(out["_score"])
        except (TypeError, ValueError):
            out["_score"] = None
    out["_comment"] = raw.get("comment") or raw.get("reviewer_comment") or ""
    # Keep portfolio/portfolioUrl as-is in output
    return out


def load_records(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("records", "data", "evaluations", "rows"):
            if key in data and isinstance(data[key], list):
                return data[key]
    raise ValueError("Input must be a JSON array or an object with a list under records/data/...")


def main():
    ap = argparse.ArgumentParser(description="Clean Geode dataset for ML prep.")
    ap.add_argument("input", type=Path, help="Path to Geode JSON file")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output cleaned JSON path")
    ap.add_argument("--dry-run", action="store_true", help="Print summary only, do not write file")
    ap.add_argument("--keep-inaccessible", action="store_true", help="Do not drop score=1 with 'cannot view' comments")
    args = ap.parse_args()

    records = load_records(args.input)
    total = len(records)

    normalized = [normalize_record(r) for r in records]

    # Filter 1: excluded roles
    after_role = []
    dropped_role = 0
    for r in normalized:
        role = r.get("_role_normalized") or ""
        if role in EXCLUDED_ROLES or any(x in role for x in ("Video Editor", "video editor")):
            dropped_role += 1
            continue
        after_role.append(r)

    # Filter 2: score=1 + inaccessible comment (design quality label is noisy)
    after_inaccessible = []
    dropped_inaccessible = 0
    for r in after_role:
        if args.keep_inaccessible:
            after_inaccessible.append(r)
            continue
        score = r.get("_score")
        comment = r.get("_comment") or ""
        if score == 1 and looks_inaccessible(comment):
            dropped_inaccessible += 1
            continue
        after_inaccessible.append(r)

    # Strip internal keys for output
    cleaned = []
    for r in after_inaccessible:
        out = {k: v for k, v in r.items() if not k.startswith("_")}
        cleaned.append(out)

    # Summary
    score_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in after_inaccessible:
        s = r.get("_score")
        if s in score_counts:
            score_counts[s] += 1

    role_counts: dict[str, int] = {}
    for r in after_inaccessible:
        role = r.get("_role_normalized") or "Unknown"
        role_counts[role] = role_counts.get(role, 0) + 1

    print("--- Geode dataset cleaning summary ---")
    print(f"Input rows:           {total}")
    print(f"Dropped (role):       {dropped_role}  (excluded: {sorted(EXCLUDED_ROLES)})")
    print(f"Dropped (inaccessible): {dropped_inaccessible}  (score=1 + cannot-view comment)")
    print(f"Output rows:          {len(cleaned)}")
    print()
    print("Score distribution (after cleaning):")
    for k in (1, 2, 3, 4, 5):
        print(f"  {k}: {score_counts[k]}")
    print()
    print("Role distribution (top 15):")
    for role, cnt in sorted(role_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"  {role[:50]:<50} {cnt}")

    if args.dry_run:
        print("\nDry run — no file written.")
        return

    out_path = args.output or (args.input.parent / f"{args.input.stem}_cleaned.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
