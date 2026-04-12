"""
FastAPI application — HTTP wrapper around the triage pipeline.

Sprint 4: single /triage endpoint that accepts an incident bundle
and returns a structured triage report.

The API is intentionally thin — it delegates all logic to the
same agent pipeline used by the CLI.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.routes.triage import router as triage_router

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)

app = FastAPI(
    title="Incident Triage Agent",
    description=(
        "AI-assisted incident triage: ingest logs, metrics, and deployment "
        "context — get ranked root cause hypotheses with evidence citations."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(triage_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "service": "incident-triage-agent"}


@app.get("/")
def root():
    return {
        "service": "Incident Triage Agent",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "triage": "POST /api/v1/triage",
            "triage_scenario": "POST /api/v1/triage/scenario/{scenario_name}",
        },
    }