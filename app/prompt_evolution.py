"""
Periodic feedback pull from HireHive for human rubric review.

Fetches agreement/correction examples, formats them, and writes a review
file — does NOT auto-rewrite the scoring rubric. Safe to call from a cron
job or admin script; errors are logged, never raised to the API.
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HIREHIVE_URL = os.getenv("HIREHIVE_INTERNAL_URL", "").strip()
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()

ROOT = Path(__file__).resolve().parent.parent
REVIEW_OUT = ROOT / "scripts" / "rubric_review_candidates.txt"


def format_examples(entries, label):
    out = []
    for e in entries:
        out.append(
            f"""
[{label}]
AI Score: {e['aiScore']} | Designer Score: {e['designerScore']} | Delta: {e['scoreDelta']}
AI Reasoning: {e['aiReasoning'][:300]}
Designer Feedback: {e['designerFeedback'][:200]}
"""
        )
    return "\n".join(out)


def run_prompt_evolution() -> dict:
    try:
        if not HIREHIVE_URL or not INTERNAL_API_KEY:
            logger.warning(
                "prompt_evolution_skipped",
                extra={"reason": "HIREHIVE_INTERNAL_URL or INTERNAL_API_KEY not set"},
            )
            return {"status": "skipped", "reason": "missing_env", "examples": 0}

        # Step 1 — Fetch feedback from HireHive
        try:
            response = requests.get(
                f"{HIREHIVE_URL}/api/v1/internal/feedback-examples",
                headers={"x-internal-key": INTERNAL_API_KEY},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(
                "prompt_evolution_fetch_failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return {"status": "fetch_failed", "reason": str(e), "examples": 0}

        agreements = data.get("agreements", [])
        corrections = data.get("corrections", [])
        total = len(agreements) + len(corrections)
        if total == 0:
            logger.info("prompt_evolution_skipped", extra={"reason": "no feedback examples"})
            return {"status": "skipped", "reason": "no_examples", "examples": 0}

        # Step 2 — Format few-shot examples and save for human review
        agreement_text = format_examples(agreements, "AGREEMENT")
        correction_text = format_examples(corrections, "CORRECTION — AI was wrong")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        review_body = (
            f"Rubric review candidates — generated {stamp}\n"
            f"Source: {HIREHIVE_URL}/api/v1/internal/feedback-examples\n"
            f"Agreements: {len(agreements)} | Corrections: {len(corrections)}\n"
            f"{'=' * 72}\n"
            f"\n## AGREEMENTS (AI and designer agreed — keep doing this)\n"
            f"{agreement_text}\n"
            f"{'=' * 72}\n"
            f"\n## CORRECTIONS (AI was wrong — learn from these)\n"
            f"{correction_text}\n"
        )

        REVIEW_OUT.parent.mkdir(parents=True, exist_ok=True)
        REVIEW_OUT.write_text(review_body, encoding="utf-8")
        print(review_body)
        print(f"✅ Wrote review file: {REVIEW_OUT}")
        print(f"📊 {len(agreements)} agreements, {len(corrections)} corrections")

        logger.info(
            "prompt_evolution_review_ready",
            extra={
                "agreements": len(agreements),
                "corrections": len(corrections),
                "review_path": str(REVIEW_OUT),
            },
        )
        return {
            "status": "ok",
            "examples": total,
            "agreements": len(agreements),
            "corrections": len(corrections),
            "review_path": str(REVIEW_OUT),
        }
    except Exception as e:
        logger.error(
            "prompt_evolution_failed",
            extra={"error": str(e)},
            exc_info=True,
        )
        return {"status": "failed", "reason": str(e), "examples": 0}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_prompt_evolution()
