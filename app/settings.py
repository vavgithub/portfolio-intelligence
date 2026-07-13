"""Runtime settings loaded from config/pipeline.json with optional env overrides.

Tunable thresholds and vocabulary live in JSON — not scattered as magic numbers/lists
in application code. Env vars override scalars when set.

Missing / empty / malformed config fails loudly (no silent empty-dict defaults).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "pipeline.json"

_REQUIRED_TOP = (
    "model_name",
    "empty_strengths_max_score",
    "shortlist_ready_message",
    "shortlist_min_score",
    "relevance",
    "content_sufficiency",
    "behance",
    "role_to_categories",
    "project_classification",
    "brand_project_selection",
)
_REQUIRED_RELEVANCE = (
    "dominance_threshold",
    "reject_exempt_categories",
    "primary_focus_brand_min",
    "primary_focus_multi_min",
    "brand_category",
    "multi_disciplinary_label",
)
_REQUIRED_CS = ("min_case_study_chars", "min_loaded_images", "min_natural_width")
_REQUIRED_BEHANCE = (
    "reserved_paths",
    "portfolio_container_slugs",
    "portfolio_container_titles",
    "wall_phrases",
)


class PipelineConfigError(RuntimeError):
    """Raised when pipeline config is missing, empty, or invalid."""


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return int(raw)


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip() or default


def _validate(data: dict[str, Any], cfg_path: Path) -> None:
    if not isinstance(data, dict) or not data:
        raise PipelineConfigError(
            f"Pipeline config is empty or not an object: {cfg_path}"
        )
    missing = [k for k in _REQUIRED_TOP if k not in data]
    if missing:
        raise PipelineConfigError(
            f"Pipeline config missing required keys {missing}: {cfg_path}"
        )
    rel = data.get("relevance")
    if not isinstance(rel, dict):
        raise PipelineConfigError(f"relevance must be an object: {cfg_path}")
    missing_rel = [k for k in _REQUIRED_RELEVANCE if k not in rel]
    if missing_rel:
        raise PipelineConfigError(
            f"relevance missing keys {missing_rel}: {cfg_path}"
        )
    cs = data.get("content_sufficiency")
    if not isinstance(cs, dict):
        raise PipelineConfigError(f"content_sufficiency must be an object: {cfg_path}")
    missing_cs = [k for k in _REQUIRED_CS if k not in cs]
    if missing_cs:
        raise PipelineConfigError(
            f"content_sufficiency missing keys {missing_cs}: {cfg_path}"
        )
    beh = data.get("behance")
    if not isinstance(beh, dict):
        raise PipelineConfigError(f"behance must be an object: {cfg_path}")
    missing_beh = [k for k in _REQUIRED_BEHANCE if k not in beh]
    if missing_beh:
        raise PipelineConfigError(
            f"behance missing keys {missing_beh}: {cfg_path}"
        )
    if not isinstance(data.get("project_classification"), list) or not data["project_classification"]:
        raise PipelineConfigError(
            f"project_classification must be a non-empty list: {cfg_path}"
        )
    if not isinstance(data.get("role_to_categories"), dict) or not data["role_to_categories"]:
        raise PipelineConfigError(
            f"role_to_categories must be a non-empty object: {cfg_path}"
        )
    bps = data.get("brand_project_selection")
    if not isinstance(bps, dict) or "positive_keywords" not in bps or "negative_keywords" not in bps:
        raise PipelineConfigError(
            f"brand_project_selection must include positive_keywords and negative_keywords: {cfg_path}"
        )


@lru_cache(maxsize=1)
def load_pipeline_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else Path(
        os.getenv("PIPELINE_CONFIG_PATH", str(_DEFAULT_CONFIG_PATH))
    )
    if not cfg_path.is_file():
        raise PipelineConfigError(f"Pipeline config file not found: {cfg_path}")
    try:
        raw_text = cfg_path.read_text(encoding="utf-8")
    except OSError as e:
        raise PipelineConfigError(f"Cannot read pipeline config {cfg_path}: {e}") from e
    if not raw_text.strip():
        raise PipelineConfigError(f"Pipeline config file is empty: {cfg_path}")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise PipelineConfigError(
            f"Pipeline config is not valid JSON ({cfg_path}): {e}"
        ) from e

    _validate(data, cfg_path)

    # Scalar / nested overrides from env (ops can tune without editing JSON)
    try:
        data["model_name"] = _env_str("AI_MODEL_NAME", str(data["model_name"]))
        data["empty_strengths_max_score"] = _env_float(
            "EMPTY_STRENGTHS_MAX_SCORE", float(data["empty_strengths_max_score"])
        )
        data["shortlist_min_score"] = _env_float(
            "SHORTLIST_MIN_SCORE", float(data["shortlist_min_score"])
        )
        data["shortlist_ready_message"] = _env_str(
            "SHORTLIST_READY_MESSAGE",
            str(data["shortlist_ready_message"]),
        )
        rel = data["relevance"]
        rel["dominance_threshold"] = _env_float(
            "RELEVANCE_DOMINANCE_THRESHOLD", float(rel["dominance_threshold"])
        )
        rel["primary_focus_brand_min"] = _env_float(
            "RELEVANCE_BRAND_FOCUS_MIN", float(rel["primary_focus_brand_min"])
        )
        rel["primary_focus_multi_min"] = _env_float(
            "RELEVANCE_MULTI_FOCUS_MIN", float(rel["primary_focus_multi_min"])
        )
        cs = data["content_sufficiency"]
        cs["min_case_study_chars"] = _env_int(
            "MIN_CASE_STUDY_CHARS", int(cs["min_case_study_chars"])
        )
        cs["min_loaded_images"] = _env_int(
            "MIN_LOADED_IMAGES", int(cs["min_loaded_images"])
        )
        cs["min_natural_width"] = _env_int(
            "MIN_NATURAL_WIDTH", int(cs["min_natural_width"])
        )
    except (TypeError, ValueError) as e:
        raise PipelineConfigError(
            f"Pipeline config has invalid scalar type(s) in {cfg_path}: {e}"
        ) from e
    return data


def get_settings() -> dict[str, Any]:
    return load_pipeline_config()


def reload_settings() -> dict[str, Any]:
    load_pipeline_config.cache_clear()
    return load_pipeline_config()
