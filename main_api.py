"""
FastAPI wrapper for run_portfolio_intelligence_pipeline.

Run from project root using the project venv (not system /usr/bin/uvicorn):

  ./venv/bin/python -m pip install -r requirements.txt   # once
  ./venv/bin/python -m uvicorn main_api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main import run_portfolio_intelligence_pipeline


async def _save_to_geode(candidate_id: str, job_id: str, result: dict):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "http://localhost:8008/api/v1/hr/save-ai-score",
                json={
                    "candidateId": candidate_id,
                    "jobId": job_id,
                    "aiScore": result.get("score"),
                    "aiReasoning": result.get("reasoning"),
                    "aiRecommendation": result.get("recommendation"),
                },
                timeout=30.0
            )
    except Exception as e:
        print(f"Failed to save to Geode: {e}")


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
    job_id: str = Field(default="", description="Job id for Geode save-ai-score")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _run_pipeline_sync(behance_url: str, role: str) -> dict | None:
    try:
        report = run_portfolio_intelligence_pipeline(behance_url, role)
    except Exception:
        return None

    fc = report.get("final_scorecard") or {}
    skipped = report.get("status") == "skipped"
    result = {
        "score": fc.get("average_quality_score"),
        "recommendation": str(fc.get("hire_recommendation", "")),
        "reasoning": str(fc.get("summary_reasoning", "")),
        "seniority": str(fc.get("seniority_estimate", "")),
    }
    if skipped:
        return None
    return result


async def _background_score(
    candidate_id: str, job_id: str, behance_url: str, role: str
) -> None:
    result = await asyncio.to_thread(_run_pipeline_sync, behance_url, role)
    if result is not None:
        await _save_to_geode(candidate_id, job_id, result)


@app.post("/score")
async def score(body: ScoreRequest) -> dict:
    asyncio.create_task(
        _background_score(
            body.candidate_id, body.job_id, body.behance_url, body.role
        )
    )
    return {"status": "processing", "candidate_id": body.candidate_id}


@app.get("/score-status/{candidate_id}")
async def score_status(candidate_id: str) -> dict:
    return {"status": "processing", "candidate_id": candidate_id}
