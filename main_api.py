"""
FastAPI wrapper for run_portfolio_intelligence_pipeline.

Run from project root using the project venv (not system /usr/bin/uvicorn):

  ./venv/bin/python -m pip install -r requirements.txt   # once
  ./venv/bin/python -m uvicorn main_api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main import run_portfolio_intelligence_pipeline

# Global dict to store results
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


async def _background_score(candidate_id: str, behance_url: str, role: str) -> None:
    result = await asyncio.to_thread(_run_pipeline_sync, behance_url, role)
    if result is not None:
        results_store[candidate_id] = {
            "status": "completed",
            "score": result.get("score"),
            "reasoning": result.get("reasoning"),
            "recommendation": result.get("recommendation"),
        }


@app.post("/score")
async def score(body: ScoreRequest) -> dict:
    asyncio.create_task(
        _background_score(body.candidate_id, body.behance_url, body.role)
    )
    return {"status": "processing", "candidate_id": body.candidate_id}


@app.get("/score-status/{candidate_id}")
async def score_status(candidate_id: str) -> dict:
    if candidate_id in results_store:
        return results_store[candidate_id]
    return {"status": "processing", "candidate_id": candidate_id}
