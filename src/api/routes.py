"""
FastAPI routes with SSE streaming.

Flow:
  POST   /api/review              → start a new review (returns review_id)
  GET    /api/review/{id}/stream  → SSE stream of agent progress
  POST   /api/review/{id}/verdict → submit human review verdicts, resume graph
  GET    /api/review/{id}/report  → fetch final report
  GET    /api/review/{id}/status  → check review status

Key: the LangGraph graph is compiled with interrupt_before=["human_review"],
so execution pauses after supervisor_summary. The client submits verdicts
and we resume with Command(resume=...).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from src.agents.graph import get_graph
from src.models.review import (
    HumanVerdict,
    ReviewReport,
    ReviewRequest,
    ReviewStatus,
)
from src.config import settings

router = APIRouter()

# In-memory store for active reviews (production would use Redis/DB)
_active_reviews: dict[str, dict[str, Any]] = {}
# Event queues for SSE consumers
_event_queues: dict[str, asyncio.Queue] = {}


def _get_or_create_queue(review_id: str) -> asyncio.Queue:
    if review_id not in _event_queues:
        _event_queues[review_id] = asyncio.Queue()
    return _event_queues[review_id]


async def _push_event(review_id: str, event_type: str, data: dict):
    """Push an SSE event to all listeners."""
    queue = _get_or_create_queue(review_id)
    await queue.put({"event": event_type, "data": json.dumps(data)})


# ---------------------------------------------------------------------------
# POST /api/review — start review
# ---------------------------------------------------------------------------

@router.post("/review")
async def start_review(req: ReviewRequest):
    review_id = uuid.uuid4().hex[:12]

    graph = get_graph()
    config = {
        "configurable": {"thread_id": review_id},
        "stream_mode": ["updates", "custom"],
    }

    # Initialize state
    initial_state = {
        "review_id": review_id,
        "code": req.code,
        "language": req.language or "",
        "title": req.title or "Code Review",
        "structure_json": "",
        "structural_findings": {},
        "agents_to_run": [],
        "has_api_routes": False,
        "security_findings": [],
        "performance_findings": [],
        "maintainability_findings": [],
        "api_design_findings": [],
        "confirmed_findings": [],
        "deduplicated_findings": [],
        "review_summary": "",
        "final_report_json": "",
        "status": ReviewStatus.PARSING.value,
        "error": "",
        "progress_messages": [],
    }

    _active_reviews[review_id] = {
        "status": ReviewStatus.PARSING.value,
        "config": config,
        "state": initial_state,
        "report": None,
        "pending_events": [],
    }

    # Launch graph execution as background task
    asyncio.create_task(_execute_graph(review_id, config, initial_state))

    return {"review_id": review_id, "status": ReviewStatus.PARSING.value}


async def _execute_graph(review_id: str, config: dict, state: dict):
    """Run the LangGraph in background, pumping events to SSE queue."""
    graph = get_graph()

    try:
        async for stream_mode, chunk in graph.astream(state, config, stream_mode=["updates", "custom"]):
            if stream_mode == "custom":
                # Custom events from agents: status updates, agent progress
                await _push_event(review_id, "agent_event", chunk)

            elif stream_mode == "updates":
                # State updates after each node completes
                node_name = list(chunk.keys())[0] if chunk else "unknown"
                node_data = chunk.get(node_name, {})

                # Update stored state
                if node_data:
                    _active_reviews[review_id]["state"].update(node_data)

                # Map node completion to status
                status_map = {
                    "security": ReviewStatus.REVIEWING.value,
                    "performance": ReviewStatus.REVIEWING.value,
                    "maintainability": ReviewStatus.REVIEWING.value,
                    "api_design": ReviewStatus.REVIEWING.value,
                    "supervisor_summary": ReviewStatus.AWAITING_HUMAN.value,
                    "generate_report": ReviewStatus.COMPLETED.value,
                }

                new_status = status_map.get(node_name)
                if new_status:
                    _active_reviews[review_id]["status"] = new_status

                await _push_event(review_id, "node_complete", {
                    "node": node_name,
                    "status": new_status or _active_reviews[review_id]["status"],
                    "summary": {
                        k: len(v) if isinstance(v, list) else str(v)[:100]
                        for k, v in (node_data or {}).items()
                    },
                })

        # Graph completed normally (no interrupt, or resumed and finished)
        current_state = _active_reviews[review_id]["state"]
        report_json = current_state.get("final_report_json", "")
        if report_json:
            _active_reviews[review_id]["report"] = json.loads(report_json)
        _active_reviews[review_id]["status"] = ReviewStatus.COMPLETED.value
        await _push_event(review_id, "review_complete", {
            "message": "Review completed",
            "report": _active_reviews[review_id]["report"],
        })

    except Exception as e:
        _active_reviews[review_id]["status"] = ReviewStatus.ERROR.value
        _active_reviews[review_id]["error"] = str(e)
        await _push_event(review_id, "review_error", {"error": str(e)})


# ---------------------------------------------------------------------------
# GET /api/review/{id}/stream — SSE streaming endpoint
# ---------------------------------------------------------------------------

@router.get("/review/{review_id}/stream")
async def stream_review(review_id: str):
    """SSE endpoint that streams agent progress in real time."""

    if review_id not in _active_reviews:
        raise HTTPException(status_code=404, detail="Review not found")

    async def event_generator():
        queue = _get_or_create_queue(review_id)

        # Send initial state
        yield {
            "event": "connected",
            "data": json.dumps({"review_id": review_id, "status": _active_reviews[review_id]["status"]}),
        }

        # Replay any buffered events
        for evt in _active_reviews[review_id].get("pending_events", []):
            yield evt

        # Stream live events
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=30)
                yield evt

                # If review is complete or errored, close after sending the final event
                if evt.get("event") in ("review_complete", "review_error"):
                    break

            except asyncio.TimeoutError:
                # Send heartbeat
                yield {"event": "heartbeat", "data": json.dumps({"ts": datetime.now().isoformat()})}

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# POST /api/review/{id}/verdict — human review submission
# ---------------------------------------------------------------------------

@router.post("/review/{review_id}/verdict")
async def submit_verdict(review_id: str, verdicts: list[HumanVerdict]):
    """
    Submit human review verdicts and resume the LangGraph.

    This is the Human-in-the-Loop resume point: the graph was interrupted
    before human_review node. We filter findings based on user verdicts,
    then resume the graph with Command(resume=...).
    """
    if review_id not in _active_reviews:
        raise HTTPException(status_code=404, detail="Review not found")

    review = _active_reviews[review_id]
    if review["status"] != ReviewStatus.AWAITING_HUMAN.value:
        raise HTTPException(status_code=400, detail=f"Review is in status '{review['status']}', not awaiting human")

    # Apply verdicts: filter findings
    state = review["state"]
    all_findings = state.get("confirmed_findings", state.get("deduplicated_findings", []))
    verdict_map = {v.finding_id: v.action for v in verdicts}

    confirmed = [f for f in all_findings if verdict_map.get(f.get("id", "")) != "dismiss"]

    # Update state with filtered findings
    state["confirmed_findings"] = confirmed

    # Resume graph execution with Command(resume=...)
    from langgraph.types import Command

    graph = get_graph()
    config = review["config"]

    # The graph was interrupted before "human_review" node.
    # Command(resume=...) passes through that node with the updated state.
    await _push_event(review_id, "agent_event", {
        "event": "human_review_applied",
        "original_count": len(all_findings),
        "confirmed_count": len(confirmed),
        "dismissed_count": len(all_findings) - len(confirmed),
    })

    review["status"] = ReviewStatus.GENERATING_REPORT.value

    try:
        async for stream_mode, chunk in graph.astream(
            Command(resume=state),
            config,
            stream_mode=["updates", "custom"],
        ):
            if stream_mode == "custom":
                await _push_event(review_id, "agent_event", chunk)
            elif stream_mode == "updates":
                node_name = list(chunk.keys())[0] if chunk else "unknown"
                node_data = chunk.get(node_name, {})

                if node_data:
                    review["state"].update(node_data)

                if node_name == "generate_report":
                    report_json = node_data.get("final_report_json", "")
                    if report_json:
                        review["report"] = json.loads(report_json)
                    review["status"] = ReviewStatus.COMPLETED.value

                await _push_event(review_id, "node_complete", {
                    "node": node_name,
                    "status": review["status"],
                })

        await _push_event(review_id, "review_complete", {
            "message": "Review completed after human verdict",
            "report": review["report"],
        })

    except Exception as e:
        review["status"] = ReviewStatus.ERROR.value
        review["error"] = str(e)
        await _push_event(review_id, "review_error", {"error": str(e)})

    return {"status": review["status"], "confirmed_count": len(confirmed)}


# ---------------------------------------------------------------------------
# GET /api/review/{id}/report — final report
# ---------------------------------------------------------------------------

@router.get("/review/{review_id}/report")
async def get_report(review_id: str) -> dict:
    if review_id not in _active_reviews:
        raise HTTPException(status_code=404, detail="Review not found")

    review = _active_reviews[review_id]
    report = review.get("report")
    if not report:
        state = review["state"]
        report_json = state.get("final_report_json", "")
        if report_json:
            report = json.loads(report_json)

    if not report:
        raise HTTPException(status_code=404, detail="Report not yet generated")

    return report


# ---------------------------------------------------------------------------
# GET /api/review/{id}/status — current status
# ---------------------------------------------------------------------------

@router.get("/review/{review_id}/status")
async def get_status(review_id: str):
    if review_id not in _active_reviews:
        raise HTTPException(status_code=404, detail="Review not found")

    review = _active_reviews[review_id]
    state = review["state"]

    return {
        "review_id": review_id,
        "status": review["status"],
        "title": state.get("title", ""),
        "language": state.get("language", ""),
        "agents": state.get("agents_to_run", []),
        "findings_count": {
            "security": len(state.get("security_findings", [])),
            "performance": len(state.get("performance_findings", [])),
            "maintainability": len(state.get("maintainability_findings", [])),
            "api_design": len(state.get("api_design_findings", [])),
        },
        "error": review.get("error"),
    }
