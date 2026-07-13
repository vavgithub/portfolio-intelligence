"""
FastAPI wrapper for run_portfolio_intelligence_pipeline.

Run from project root using the project venv (not system /usr/bin/uvicorn):

  ./venv/bin/python -m pip install -r requirements.txt   # once
  ./venv/bin/python -m uvicorn main_api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main import run_portfolio_intelligence_pipeline


class JobStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


logger = logging.getLogger(__name__)

MAX_INSTANCES = int(os.getenv("MAX_INSTANCES", "1"))
MAX_PROJECTS_TO_ANALYZE = max(1, int(os.getenv("MAX_PROJECTS_TO_ANALYZE", "3")))

# Single-process only. Safe while MAX_INSTANCES=1. Replace with Redis when scaling.
results_store = {}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_ngrok_header(request, call_next):
    response = await call_next(request)
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if exc.status_code == 404:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "path": request.url.path,
                "message": "No route here. Try GET / for available endpoints.",
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.get("/")
async def root() -> dict[str, object]:
    return {
        "service": "portfolio_intelligence_api",
        "endpoints": {
            "GET /health": "Liveness check",
            "POST /score": "Body: { behance_url, candidate_id, role }",
            "GET /docs": "Swagger UI",
            "GET /redoc": "ReDoc",
        },
    }


class ScoreRequest(BaseModel):
    behance_url: str = Field(..., description="Behance portfolio URL")
    candidate_id: str = Field(..., description="External candidate identifier")
    role: str = Field(..., description="e.g. brand_identity_designer")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _run_pipeline_sync(
    behance_url: str, role: str, job_id: str, max_projects: int
) -> dict:
    """Always returns a dict: {ok: bool, error?: str, result?: dict} for store payload."""
    logger.info(
        "pipeline_start",
        extra={"job_id": job_id, "url": behance_url, "role": role},
    )
    try:
        report = run_portfolio_intelligence_pipeline(
            behance_url,
            candidate_role=role,
            max_projects=max_projects,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    fc = report.get("final_scorecard") or {}
    skipped = report.get("status") == JobStatus.SKIPPED
    needs_human_review = (
        report.get("status") == JobStatus.NEEDS_HUMAN_REVIEW
        or bool(fc.get("insufficient_content"))
    )

    # Per-project confidence from standouts
    standouts = fc.get("top_standout_projects") or []
    scored = [
        p
        for p in standouts
        if not p.get("error") and not p.get("insufficient_content")
    ]
    projects_scored = len(scored)

    confidence_rank = {"high": 3, "medium": 2, "low": 1}

    # Signal 1 — data completeness
    if projects_scored >= 3:
        data_confidence = "high"
    elif projects_scored == 2:
        data_confidence = "medium"
    else:
        data_confidence = "low"

    # Signal 2 — Gemini's own certainty (minimum across projects)
    if scored:
        gemini_confidence = min(
            scored,
            key=lambda p: confidence_rank.get(p.get("confidence", "low"), 1),
        ).get("confidence", "low")
    else:
        gemini_confidence = "low"

    # Final — take the worse of the two signals
    final_confidence = min(
        [data_confidence, gemini_confidence],
        key=lambda c: confidence_rank.get(c, 1),
    )

    payload = {
        "score": fc.get("average_quality_score"),
        "recommendation": str(fc.get("hire_recommendation", "")),
        "reasoning": str(fc.get("summary_reasoning", "")),
        "confidence": final_confidence,
        "projects_scored": projects_scored,
    }
    if skipped:
        return {
            "ok": True,
            "result": {
                "status": JobStatus.SKIPPED,
                **payload,
            },
        }
    if needs_human_review:
        return {
            "ok": True,
            "result": {
                "status": JobStatus.NEEDS_HUMAN_REVIEW,
                **payload,
                "recommendation": str(fc.get("hire_recommendation", "Route to human review")),
                "reasoning": str(fc.get("summary_reasoning", "")),
            },
        }
    return {
        "ok": True,
        "result": {
            "status": JobStatus.COMPLETED,
            **payload,
        },
    }


async def _background_score(
    candidate_id: str,
    behance_url: str,
    role: str,
    max_projects: int,
) -> None:
    job_id = candidate_id
    outcome = await asyncio.to_thread(
        _run_pipeline_sync,
        behance_url,
        role,
        job_id,
        max_projects,
    )
    if outcome.get("ok") is False:
        err = str(outcome.get("error", "unknown"))
        results_store[candidate_id] = {
            "status": JobStatus.FAILED,
            "error": err,
            "job_id": job_id,
        }
        logger.error(
            "job_failed",
            extra={
                "job_id": job_id,
                "candidate_id": candidate_id,
                "error": err,
            },
        )
    else:
        results_store[candidate_id] = outcome.get("result") or outcome


@app.post("/score")
async def score(request: ScoreRequest) -> dict:
    results_store.pop(request.candidate_id, None)
    asyncio.create_task(
        _background_score(
            request.candidate_id,
            request.behance_url,
            request.role,
            MAX_PROJECTS_TO_ANALYZE,
        )
    )
    return {"status": JobStatus.PROCESSING, "candidate_id": request.candidate_id}


@app.get("/score-status/{candidate_id}")
async def score_status(candidate_id: str) -> dict:
    if candidate_id in results_store:
        return results_store[candidate_id]
    # Lookup miss — not a job lifecycle state (see JobStatus).
    return {"status": "not_found", "candidate_id": candidate_id}
