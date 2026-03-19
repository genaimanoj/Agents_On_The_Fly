"""FastAPI application — serves the FlyAgent research API.

Endpoints:
  POST /api/research              Start a research query (returns research_id)
  GET  /api/research/{id}/events  SSE stream of real-time events
  GET  /api/research/{id}         Get final result
  GET  /api/config                Get current config
  PUT  /api/config                Update config (runtime, in-memory)
  GET  /api/health                Health check
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.middleware import Middleware

from api.events import AgentEvent, EventBus
from flyagent.config import AppConfig, load_config
from flyagent.logging_setup import setup_logging
from flyagent.orchestrator import Orchestrator, OrchestrationResult

logger = logging.getLogger("flyagent.api")

# ── Global state ────────────────────────────────────────────────
config: AppConfig = load_config()
event_bus = EventBus()
research_store: dict[str, dict[str, Any]] = {}   # id → {status, result, error}

# ── Logging setup ───────────────────────────────────────────────
setup_logging(
    level=config.logging.level,
    fmt=config.logging.format,
    log_to_console=config.logging.log_to_console,
    log_to_file=config.logging.log_to_file,
    log_file=config.logging.log_file,
    service_name=config.logging.service_name,
)

# ── FastAPI app ─────────────────────────────────────────────────
# Starlette 0.51+ enforces CSRF for POST requests.
# We set allowed_hosts to ["*"] on the app and use CORSMiddleware.
app = FastAPI(title="FlyAgent API", version="0.1.0")

# Disable default CSRF by allowing all hosts
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ───────────────────────────────────
class ResearchRequest(BaseModel):
    query: str


class ResearchResponse(BaseModel):
    research_id: str
    status: str


class ConfigUpdate(BaseModel):
    orchestrator: dict[str, Any] | None = None
    subagent: dict[str, Any] | None = None
    models: dict[str, dict[str, Any]] | None = None
    output: dict[str, Any] | None = None
    logging_config: dict[str, Any] | None = None


# ── Background runner ───────────────────────────────────────────
async def _run_research(research_id: str, query: str) -> None:
    """Run orchestrator in background, store result, emit completion."""
    research_store[research_id]["status"] = "running"
    try:
        orch = Orchestrator(config, event_bus=event_bus)
        result = await orch.run(query, research_id=research_id)
        research_store[research_id]["status"] = "complete"
        research_store[research_id]["result"] = {
            "report": result.report,
            "confidence": result.confidence,
            "total_attempts": result.total_attempts,
            "elapsed_seconds": result.elapsed_seconds,
            "subagents_spawned": len(result.task_entries),
        }
    except Exception as e:
        logger.error(f"Research {research_id} failed: {e}", extra={"research_id": research_id})
        research_store[research_id]["status"] = "error"
        research_store[research_id]["error"] = str(e)
        await event_bus.emit(research_id, "error", {"message": str(e)})
        await event_bus.emit(research_id, "research_complete", {"error": str(e)})


# ── Routes ──────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "project": config.project_name}


@app.post("/api/research", response_model=ResearchResponse)
async def start_research(req: ResearchRequest):
    research_id = uuid.uuid4().hex[:12]
    research_store[research_id] = {"status": "queued", "query": req.query}
    logger.info(f"Research queued: {research_id}", extra={"research_id": research_id})
    asyncio.create_task(_run_research(research_id, req.query))
    return ResearchResponse(research_id=research_id, status="queued")


@app.get("/api/research/{research_id}")
async def get_research(research_id: str):
    entry = research_store.get(research_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Research not found")
    return entry


@app.get("/api/research/{research_id}/events")
async def stream_events(research_id: str):
    """Server-Sent Events stream for real-time updates."""
    if research_id not in research_store:
        raise HTTPException(status_code=404, detail="Research not found")

    queue = event_bus.subscribe(research_id)

    async def generate():
        try:
            while True:
                try:
                    event: AgentEvent = await asyncio.wait_for(queue.get(), timeout=300)
                    yield event.to_sse()
                    if event.event_type == "research_complete":
                        yield f"event: done\ndata: {{}}\n\n"
                        break
                except asyncio.TimeoutError:
                    yield f"event: ping\ndata: {{}}\n\n"
        finally:
            event_bus.unsubscribe(research_id, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
async def get_config():
    return config.to_ui_dict()


@app.put("/api/config")
async def update_config(update: ConfigUpdate):
    """Update runtime config (in-memory, not persisted to file)."""
    global config

    if update.orchestrator:
        for k, v in update.orchestrator.items():
            if hasattr(config.orchestrator, k):
                setattr(config.orchestrator, k, v)

    if update.subagent:
        for k, v in update.subagent.items():
            if hasattr(config.subagent, k):
                setattr(config.subagent, k, v)

    if update.models:
        for tier, mdata in update.models.items():
            if tier in config.models:
                for k, v in mdata.items():
                    if hasattr(config.models[tier], k):
                        setattr(config.models[tier], k, v)

    if update.output:
        for k, v in update.output.items():
            if hasattr(config.output, k):
                setattr(config.output, k, v)

    if update.logging_config:
        for k, v in update.logging_config.items():
            if hasattr(config.logging, k):
                setattr(config.logging, k, v)
        # Re-apply logging config
        setup_logging(
            level=config.logging.level,
            fmt=config.logging.format,
            log_to_console=config.logging.log_to_console,
            log_to_file=config.logging.log_to_file,
            log_file=config.logging.log_file,
            service_name=config.logging.service_name,
        )

    logger.info("Config updated via API")
    return {"status": "updated", "config": config.to_ui_dict()}
