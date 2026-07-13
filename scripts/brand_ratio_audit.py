#!/usr/bin/env python3
"""
Keyword-based brand_ratio audit + relevance pre-filter check (no LLM).

Discovers projects per profile, classifies each title/URL, and reports
brand_ratio plus classify_relevance() label (Brand Designer role assumed).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.browser_capture import PortfolioBrowser, _classify_project_from_title_and_url
from app.relevance_classifier import classify_relevance, classify_relevance_legacy

POC = ROOT / "poc_batch_trusted.json"
CACHE = ROOT / "scripts" / "brand_ratio_audit_cache.json"
BRAND_ROLE = "Brand Designer"


def discover_all_projects(profile_url: str) -> list[dict]:
    pb = PortfolioBrowser()
    url = profile_url.strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    platform = pb.identify_platform(url)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        time.sleep(2)
        projects = pb.discover_projects(page, platform)
        browser.close()
    return projects


def brand_ratio(projects: list[dict]) -> tuple[float, list[dict]]:
    if not projects:
        return 0.0, []
    classified = []
    brand_n = 0
    for p in projects:
        cat = _classify_project_from_title_and_url(p.get("title", ""), p.get("url", ""))
        classified.append(
            {
                "title": p.get("title", ""),
                "url": p.get("url", ""),
                "category": cat,
            }
        )
        if cat == "Brand Identity":
            brand_n += 1
    return brand_n / len(projects), classified


def main() -> int:
    rows = json.loads(POC.read_text(encoding="utf-8"))
    cache: dict = {}
    if CACHE.is_file():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    print("Discovering projects + classifying relevance (legacy vs new rule)...", flush=True)
    audited: list[dict] = []
    for r in rows:
        name = r.get("name", "")
        url = r.get("portfolio", "")
        if name in cache:
            entry = cache[name]
        else:
            try:
                projects = discover_all_projects(url)
                ratio, classified = brand_ratio(projects)
                relevance_new, rel_meta = classify_relevance(projects)
                relevance_old, _ = classify_relevance_legacy(projects)
                entry = {
                    "name": name,
                    "human": r.get("score", "?"),
                    "n": len(projects),
                    "brand_ratio": ratio,
                    "relevance_old": relevance_old,
                    "relevance_new": relevance_new,
                    "rel_meta": rel_meta,
                    "comment": (r.get("comment") or "").strip().replace("\n", " ")[:45],
                    "classified": classified,
                }
                cache[name] = entry
                CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"{name[:28]:<28} ERR: {e}", flush=True)
                continue
        audited.append(entry)

    old_irrelevant = [a for a in audited if a["relevance_old"] == "irrelevant"]
    new_irrelevant = [a for a in audited if a["relevance_new"] == "irrelevant"]
    old_names = {a["name"] for a in old_irrelevant}
    new_names = {a["name"] for a in new_irrelevant}

    print(flush=True)
    print("BEFORE (legacy: brand_ratio=0 and n>=6)", flush=True)
    print(f"{'Name':<28} {'Hum':>3} {'n':>3} {'Brand%':>7} {'Majority':>18}  Comment", flush=True)
    print("-" * 100, flush=True)
    for a in old_irrelevant:
        m = a["rel_meta"]
        maj = f"{m.get('majority_category','?')} {m.get('majority_share',0):.0%}"
        print(
            f"{a['name'][:28]:<28} {a['human']:>3} {a['n']:>3} {a['brand_ratio']:>6.0%} {maj:>18}  "
            f"{a['comment'] or '(no comment)'}",
            flush=True,
        )
    print(f"\nLegacy irrelevant ({len(old_irrelevant)}): {', '.join(a['name'] for a in old_irrelevant)}", flush=True)

    print(flush=True)
    print("AFTER (new: majority non-brand category >=70%, excluding Other)", flush=True)
    print(f"{'Name':<28} {'Hum':>3} {'n':>3} {'Brand%':>7} {'Majority':>18}  Comment", flush=True)
    print("-" * 100, flush=True)
    for a in new_irrelevant:
        m = a["rel_meta"]
        maj = f"{m.get('majority_category','?')} {m.get('majority_share',0):.0%}"
        print(
            f"{a['name'][:28]:<28} {a['human']:>3} {a['n']:>3} {a['brand_ratio']:>6.0%} {maj:>18}  "
            f"{a['comment'] or '(no comment)'}",
            flush=True,
        )
    print(f"\nNew irrelevant ({len(new_irrelevant)}): {', '.join(a['name'] for a in new_irrelevant)}", flush=True)

    print(flush=True)
    print("CHANGED (legacy -> new)", flush=True)
    freed = [a for a in audited if a["name"] in old_names and a["name"] not in new_names]
    newly_flagged = [a for a in audited if a["name"] in new_names and a["name"] not in old_names]
    print(f"  No longer irrelevant ({len(freed)}): {', '.join(a['name'] for a in freed) or '(none)'}", flush=True)
    print(f"  Newly irrelevant ({len(newly_flagged)}): {', '.join(a['name'] for a in newly_flagged) or '(none)'}", flush=True)

    watch = ("Shweta Parate", "Abilash H", "akansha tomar")
    print(flush=True)
    print("WATCHLIST", flush=True)
    for w in watch:
        a = next((x for x in audited if x["name"].lower() == w.lower()), None)
        if not a:
            print(f"  {w}: not found", flush=True)
            continue
        m = a["rel_meta"]
        cats = m.get("categories") or []
        from collections import Counter
        dist = ", ".join(f"{k}:{v}" for k, v in sorted(Counter(cats).items(), key=lambda x: -x[1]))
        print(
            f"  {a['name']}: human={a['human']} legacy={a['relevance_old']} new={a['relevance_new']} "
            f"majority={m.get('majority_category')} {m.get('majority_share',0):.0%} cats=[{dist}]",
            flush=True,
        )

    # Flag regressions: was correctly irrelevant (dominant UI UX/Motion) but now passes
    print(flush=True)
    print("REGRESSION CHECK (was legacy irrelevant + dominant UI UX/Motion >=70%, now unclassified)", flush=True)
    regressions = []
    for a in freed:
        m = a["rel_meta"]
        maj = m.get("majority_category", "")
        share = m.get("majority_share", 0)
        if maj in {"UI UX", "Motion", "Graphic", "Illustration"} and share >= 0.7:
            regressions.append(a)
    if regressions:
        for a in regressions:
            m = a["rel_meta"]
            print(
                f"  *** {a['name']}: human={a['human']} majority={m.get('majority_category')} "
                f"{m.get('majority_share',0):.0%} — was correctly blocked, now passes",
                flush=True,
            )
    else:
        print("  (none)", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
