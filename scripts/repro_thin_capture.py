#!/usr/bin/env python3
"""
Repro / regression checks for the shared content-sufficiency gate.

Fast path (default): unit + pipeline logic tests — no Playwright or Gemini.
Integration (--integration): live Behance control profile; requires network + GCP creds.

Usage:
  .venv/bin/python scripts/repro_thin_capture.py
  .venv/bin/python scripts/repro_thin_capture.py --integration
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.content_sufficiency import assess_capture_quality
from app.scoring import aggregate_scores

# Known-good Behance control (human-rated score 3 in poc_batch_trusted.json)
CONTROL_BEHANCE = "https://www.behance.net/bhanuprakashcs"
THIN_PERSONAL = "https://example.com"


def test_assess_capture_quality_unit() -> None:
    """Gate function: login-wall / thin capture signatures without browser."""
    ok, reasons = assess_capture_quality(
        screenshots=["/tmp/fake.png"],
        case_study_text="x" * 300,
        page=None,
    )
    assert ok, f"expected sufficient with 3 screenshots paths + long text, got {reasons}"

    ok, reasons = assess_capture_quality(
        screenshots=[],
        case_study_text="Sign in to view this project. " * 5,
        page=None,
    )
    assert not ok, "empty screenshots should fail"
    assert any("zero screenshots" in r for r in reasons)

    ok, reasons = assess_capture_quality(
        screenshots=["/tmp/fake.png"],
        case_study_text="short",
        page=None,
    )
    assert not ok, "short case study should fail"
    assert any("case study text under" in r for r in reasons)

    page = MagicMock()
    page.url = "https://www.behance.net/gallery/123/test"
    page.evaluate.return_value = 0
    ok, reasons = assess_capture_quality(
        screenshots=["/tmp/a.png", "/tmp/b.png"],
        case_study_text="x" * 300,
        page=page,
    )
    assert not ok, "DOM with 0 loaded images should fail"
    assert any("substantively-sized" in r and "images" in r for r in reasons)


def test_aggregate_all_insufficient() -> None:
    analyses = [
        {
            "project_title": "P1",
            "insufficient_content": True,
            "reasons": ["case study text under 200 chars"],
        },
        {
            "project_title": "P2",
            "insufficient_content": True,
            "reasons": ["fewer than 2 substantively-sized images in DOM"],
        },
    ]
    fc = aggregate_scores(analyses)
    assert fc.get("insufficient_content") is True
    assert fc.get("average_quality_score") is None
    assert fc.get("hire_recommendation") == "Route to human review"


def test_pipeline_empty_projects_needs_review() -> None:
    from app.main import run_portfolio_intelligence_pipeline

    metadata = {"name": "Test", "url": THIN_PERSONAL, "platform": "personal"}
    with patch("app.main.PortfolioBrowser") as mock_pb:
        mock_pb.return_value.full_pipeline_scan.return_value = (
            metadata,
            [],
            "test_run",
        )
        report = run_portfolio_intelligence_pipeline(THIN_PERSONAL, candidate_role="ui_ux_designer")
    assert report["status"] == "needs_human_review", report
    assert report["final_scorecard"]["insufficient_content"] is True


def test_pipeline_all_analyses_insufficient() -> None:
    from app.main import run_portfolio_intelligence_pipeline

    metadata = {"name": "Test", "url": CONTROL_BEHANCE, "platform": "behance"}
    projects = [
        {"title": "A", "screenshots": ["/tmp/a.png"], "case_study_text": "x" * 300},
        {"title": "B", "screenshots": ["/tmp/b.png"], "case_study_text": "x" * 300},
    ]
    insufficient = {
        "insufficient_content": True,
        "reasons": ["case study text under 200 chars"],
        "model": "gemini-2.5-flash",
    }

    with patch("app.main.PortfolioBrowser") as mock_pb, patch(
        "app.main.analyze_portfolio_visuals", return_value=insufficient
    ):
        mock_pb.return_value.full_pipeline_scan.return_value = (
            metadata,
            projects,
            "test_run",
        )
        report = run_portfolio_intelligence_pipeline(
            CONTROL_BEHANCE, candidate_role="brand_identity_designer", max_projects=2
        )
    assert report["status"] == "needs_human_review", report
    assert report["final_scorecard"]["insufficient_content"] is True
    assert report["final_scorecard"]["average_quality_score"] is None


def test_pipeline_control_scores_normally() -> None:
    from app.main import run_portfolio_intelligence_pipeline

    metadata = {"name": "Control", "url": CONTROL_BEHANCE, "platform": "behance"}
    projects = [
        {"title": "A", "screenshots": ["/tmp/a.png"], "case_study_text": "x" * 300},
        {"title": "B", "screenshots": ["/tmp/b.png"], "case_study_text": "x" * 300},
        {"title": "C", "screenshots": ["/tmp/c.png"], "case_study_text": "x" * 300},
    ]
    scored = {
        "score": 3,
        "reasoning": "Solid work.",
        "confidence": "medium",
        "design_category": "Brand Identity",
        "quality_indicators": ["clear system"],
        "weaknesses": ["limited depth"],
        "seniority": "mid",
        "model": "gemini-2.5-flash",
    }

    with patch("app.main.PortfolioBrowser") as mock_pb, patch(
        "app.main.analyze_portfolio_visuals", return_value=scored.copy()
    ):
        mock_pb.return_value.full_pipeline_scan.return_value = (
            metadata,
            projects,
            "test_run",
        )
        report = run_portfolio_intelligence_pipeline(
            CONTROL_BEHANCE, candidate_role="brand_identity_designer"
        )
    assert report["status"] == "completed", report
    assert not report["final_scorecard"].get("insufficient_content")
    assert report["final_scorecard"]["average_quality_score"] == 3.0


def test_integration_control_live() -> None:
    from app.main import run_portfolio_intelligence_pipeline

    report = run_portfolio_intelligence_pipeline(
        CONTROL_BEHANCE,
        candidate_role="brand_identity_designer",
        max_projects=1,
    )
    assert report["status"] == "completed", report
    assert report["final_scorecard"].get("average_quality_score", 0) > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Repro thin-capture / login-wall gating")
    parser.add_argument(
        "--integration",
        action="store_true",
        help="Run live Behance control (slow; needs Playwright + Vertex)",
    )
    args = parser.parse_args()

    tests = [
        ("assess_capture_quality unit", test_assess_capture_quality_unit),
        ("aggregate all insufficient", test_aggregate_all_insufficient),
        ("pipeline empty projects", test_pipeline_empty_projects_needs_review),
        ("pipeline all analyses insufficient", test_pipeline_all_analyses_insufficient),
        ("pipeline control scores normally (mocked)", test_pipeline_control_scores_normally),
    ]

    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {e}")

    if args.integration:
        try:
            test_integration_control_live()
            print("PASS  integration control (live Behance)")
        except Exception as e:
            failed += 1
            print(f"FAIL  integration control (live Behance): {e}")

    print(f"\n{len(tests) + (1 if args.integration else 0) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
