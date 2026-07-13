"""Keyword-based portfolio relevance pre-filter (no LLM).

Thresholds and exempt categories come from config/pipeline.json (env-overridable).
"""

from __future__ import annotations

from collections import Counter

from app.browser_capture import _classify_project_from_title_and_url
from app.settings import get_settings


def _rel_settings() -> dict:
    return get_settings().get("relevance") or {}


def _project_categories(projects: list) -> list[str]:
    return [
        _classify_project_from_title_and_url(p.get("title", ""), p.get("url", ""))
        for p in projects
    ]


def _majority_category_stats(categories: list[str]) -> tuple[str, float]:
    if not categories:
        return "Other", 0.0
    majority_cat, count = Counter(categories).most_common(1)[0]
    return majority_cat, count / len(categories)


def _primary_focus_label(brand_ratio: float, categories: list[str]) -> str:
    rel = _rel_settings()
    brand_label = rel.get("brand_category", "Brand Identity")
    multi_label = rel.get("multi_disciplinary_label", "Multi-disciplinary")
    brand_min = float(rel.get("primary_focus_brand_min", 0.5))
    multi_min = float(rel.get("primary_focus_multi_min", 0.15))
    if brand_ratio >= brand_min:
        return brand_label
    if brand_ratio >= multi_min:
        return multi_label
    if not categories:
        return "Other"
    return Counter(categories).most_common(1)[0][0]


def _is_irrelevant(categories: list[str]) -> bool:
    """
    Irrelevant when a single non-brand category dominates (share >= configured threshold).
    Exempt categories (e.g. Brand Identity, Other) never trigger a hard reject.
    """
    rel = _rel_settings()
    threshold = float(rel.get("dominance_threshold", 0.7))
    exempt = set(rel.get("reject_exempt_categories") or ["Brand Identity", "Other"])
    majority_cat, share = _majority_category_stats(categories)
    if share < threshold:
        return False
    if majority_cat in exempt:
        return False
    return True


def classify_relevance_legacy(projects: list) -> tuple[str, dict]:
    """Previous rule: brand_ratio == 0 and n >= 6."""
    n = len(projects)
    if n == 0:
        return "unclassified", {}
    categories = _project_categories(projects)
    brand_label = _rel_settings().get("brand_category", "Brand Identity")
    brand_n = sum(1 for cat in categories if cat == brand_label)
    if brand_n == 0 and n >= 6:
        return "irrelevant", {"categories": categories, "n": n, "brand_ratio": 0}
    return "unclassified", {"categories": categories, "n": n}


def classify_relevance(projects: list) -> tuple[str, dict]:
    """
    Classify portfolio relevance from discovered project titles/URLs.

    Returns:
        ('irrelevant', meta) when one non-exempt category has share >= dominance_threshold
        ('unclassified', meta) otherwise — scoring proceeds normally
    """
    rel = _rel_settings()
    brand_label = rel.get("brand_category", "Brand Identity")
    n = len(projects)
    if n == 0:
        return "unclassified", {
            "brand_ratio": 0,
            "n": 0,
            "brand_count": 0,
            "primary_focus": "Other",
            "composition_count": f"0 of 0 discovered projects are {brand_label}",
            "majority_category": "Other",
            "majority_share": 0.0,
        }

    categories = _project_categories(projects)
    brand_n = sum(1 for cat in categories if cat == brand_label)
    brand_ratio = brand_n / n
    majority_cat, majority_share = _majority_category_stats(categories)
    primary_focus = _primary_focus_label(brand_ratio, categories)
    composition_count = f"{brand_n} of {n} discovered projects are {brand_label}"

    meta = {
        "brand_ratio": brand_ratio,
        "n": n,
        "brand_count": brand_n,
        "primary_focus": primary_focus,
        "composition_count": composition_count,
        "majority_category": majority_cat,
        "majority_share": round(majority_share, 4),
        "categories": categories,
    }

    if _is_irrelevant(categories):
        return "irrelevant", meta

    return "unclassified", meta
