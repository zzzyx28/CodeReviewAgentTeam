"""
LangGraph StateGraph definition for the multi-agent code review system.

Key LangGraph features demonstrated:
1. Conditional edge routing — dynamically decide which agents to dispatch
2. Send API — parallel fan-out to N agent nodes
3. Checkpoint persistence — SqliteSaver for state recovery
4. Human-in-the-loop — interrupt before report generation
5. Custom streaming events — per-agent progress reported via stream_mode="custom"
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import Send
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, StreamWriter

from src.config import settings
from src.llm.client import build_llm
from src.models.review import AgentRole, Finding, ReviewStatus, Severity
from src.tools.ast_tools import (
    CodeParser,
    CodeStructure,
    FindingResult,
    detect_language,
    run_all_analyzers,
)
from src.agents.prompts import (
    SECURITY_AGENT_PROMPT,
    PERFORMANCE_AGENT_PROMPT,
    MAINTAINABILITY_AGENT_PROMPT,
    API_DESIGN_AGENT_PROMPT,
    SUPERVISOR_PROMPT,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ReviewState(TypedDict):
    review_id: str
    code: str
    language: str
    title: str

    # Code analysis
    structure_json: str  # serialized CodeStructure
    structural_findings: dict[str, list[dict]]  # keyed by category
    agents_to_run: list[str]
    has_api_routes: bool

    # Per-agent outputs
    security_findings: list[dict[str, Any]]
    performance_findings: list[dict[str, Any]]
    maintainability_findings: list[dict[str, Any]]
    api_design_findings: list[dict[str, Any]]

    # Post-human-review filtered findings
    confirmed_findings: list[dict[str, Any]]

    # Supervisor
    deduplicated_findings: list[dict[str, Any]]
    review_summary: str

    # Final
    final_report_json: str
    status: str
    error: str
    progress_messages: Annotated[list, add_messages]  # for event streaming


# ---------------------------------------------------------------------------
# Node: Parse code
# ---------------------------------------------------------------------------

def parse_code_node(state: ReviewState, writer: StreamWriter) -> dict[str, Any]:
    """Parse code, detect language, extract structure, run structural analyzers."""
    writer("custom", {"event": "status", "status": ReviewStatus.PARSING.value, "message": "Parsing code..."})

    code = state["code"]
    language = detect_language(code, state.get("language"))

    parser = CodeParser(language)
    structure = parser.extract_structure(code)
    structural_findings_raw = run_all_analyzers(structure)

    # Serialize findings for JSON-safe state
    structural_findings: dict[str, list[dict]] = {}
    for cat, find_list in structural_findings_raw.items():
        structural_findings[cat] = [
            {
                "category": f.category,
                "title": f.title,
                "description": f.description,
                "lines": [{"line_start": loc.line_start, "snippet": loc.snippet} for loc in f.lines],
            }
            for f in find_list
        ]

    has_api = len(structure.api_routes) > 0

    writer("custom", {
        "event": "parsed",
        "language": language,
        "lines": structure.total_lines,
        "functions": len(structure.functions),
        "classes": len(structure.classes),
        "api_routes": len(structure.api_routes) if has_api else 0,
        "structural_hints": {
            cat: len(items) for cat, items in structural_findings.items()
        },
    })

    return {
        "language": language,
        "structure_json": json.dumps(structure.__dict__ if hasattr(structure, '__dict__') else {}, default=str),
        "structural_findings": structural_findings,
        "has_api_routes": has_api,
        "security_findings": [],
        "performance_findings": [],
        "maintainability_findings": [],
        "api_design_findings": [],
        "deduplicated_findings": [],
        "review_summary": "",
        "final_report_json": "",
        "error": "",
    }


# ---------------------------------------------------------------------------
# Node: Supervisor dispatch — decides which agents to run
# ---------------------------------------------------------------------------

def supervisor_dispatch(state: ReviewState, writer: StreamWriter) -> dict[str, Any]:
    """Conditionally decide which agents to launch based on code characteristics."""
    agents = []
    structural = state["structural_findings"]

    # Security: always run
    agents.append("security")
    # Performance: always run
    agents.append("performance")
    # Maintainability: always run
    agents.append("maintainability")
    # API Design: only if API routes detected
    if state["has_api_routes"]:
        agents.append("api_design")
    else:
        # Still run if structural analysis found enough API patterns
        if len(structural.get("api_design", [])) > 0:
            agents.append("api_design")

    writer("custom", {
        "event": "dispatch",
        "agents": agents,
        "message": f"Dispatching {len(agents)} agents: {', '.join(agents)}",
    })

    return {"agents_to_run": agents}


# ---------------------------------------------------------------------------
# Conditional edge: route to Send
# ---------------------------------------------------------------------------

def route_to_agents(state: ReviewState) -> list[Send]:
    """Conditional fan-out: use Send API to dispatch each agent in parallel."""
    agents = state["agents_to_run"]
    structural = state["structural_findings"]

    # Map agent name to its structural hints category and prompt
    agent_config = {
        "security": {
            "prompt": SECURITY_AGENT_PROMPT,
            "hints": structural.get("security", []),
            "hint_text": "No patterns flagged by static analysis.",
        },
        "performance": {
            "prompt": PERFORMANCE_AGENT_PROMPT,
            "hints": structural.get("performance", []),
            "hint_text": "No patterns flagged by static analysis.",
        },
        "maintainability": {
            "prompt": MAINTAINABILITY_AGENT_PROMPT,
            "hints": structural.get("maintainability", []),
            "hint_text": "No patterns flagged by static analysis.",
        },
        "api_design": {
            "prompt": API_DESIGN_AGENT_PROMPT,
            "hints": structural.get("api_design", []),
            "hint_text": "No API routes detected or no patterns flagged.",
        },
    }

    sends = []
    for agent_name in agents:
        config = agent_config[agent_name]
        hints = config["hints"]
        hint_text = config["hint_text"]
        if hints:
            hint_text = "\n".join(
                f"- [{h['category']}] Line {h['lines'][0]['line_start'] if h['lines'] else '?'}: {h['title']}"
                for h in hints[:10]  # Cap to avoid overwhelming the LLM
            )

        sends.append(Send(
            agent_name,
            {
                "agent_name": agent_name,
                "prompt": config["prompt"],
                "hint_text": hint_text,
                "code": state["code"],
                "language": state["language"],
            },
        ))

    return sends


# ---------------------------------------------------------------------------
# Agent nodes (one per agent type — invoked in parallel via Send)
# ---------------------------------------------------------------------------

async def _run_agent(
    agent_name: str,
    prompt_template: str,
    hint_text: str,
    code: str,
    language: str,
    writer: StreamWriter,
) -> list[dict[str, Any]]:
    """Common agent execution logic. Each agent runs independently."""
    writer("custom", {
        "event": "agent_started",
        "agent": agent_name,
        "timestamp": datetime.now().isoformat(),
    })

    system_prompt = prompt_template.format(structural_hints=hint_text)
    user_prompt = f"## Code ({language})\n\n```{language}\n{code}\n```\n\nAnalyze the code above and output your findings as a JSON array."

    llm = build_llm(temperature=0.1)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    # Stream LLM response for observability
    full_response = ""
    async for chunk in llm.astream(messages):
        content = chunk.content if hasattr(chunk, "content") else str(chunk)
        if content:
            full_response += content
            writer("custom", {
                "event": "agent_chunk",
                "agent": agent_name,
                "content": content,
            })

    # Parse JSON from the response
    try:
        findings = _extract_json_array(full_response)
    except Exception:
        findings = []

    # Validate and normalize
    normalized = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        normalized.append({
            "agent": agent_name,
            "severity": f.get("severity", "info").lower(),
            "title": f.get("title", "Untitled finding"),
            "description": f.get("description", ""),
            "line_start": f.get("line_start"),
            "line_end": f.get("line_end"),
            "code_snippet": f.get("code_snippet", ""),
            "suggestion": f.get("suggestion", ""),
            "cwe_id": f.get("cwe_id"),
        })

    writer("custom", {
        "event": "agent_completed",
        "agent": agent_name,
        "finding_count": len(normalized),
        "timestamp": datetime.now().isoformat(),
    })

    return normalized


def _extract_json_array(text: str) -> list[Any]:
    """Robust JSON array extraction from LLM output."""
    text = text.strip()
    # Try to find array in markdown code block first
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if m:
        return json.loads(m.group(1))
    # Then try direct
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        return json.loads(m.group(0))
    return []


# -- Individual agent node functions (called by Send) --

async def security_agent(state: dict, writer: StreamWriter) -> dict[str, Any]:
    findings = await _run_agent(
        "security", SECURITY_AGENT_PROMPT,
        state["hint_text"], state["code"], state["language"], writer,
    )
    return {"security_findings": findings}


async def performance_agent(state: dict, writer: StreamWriter) -> dict[str, Any]:
    findings = await _run_agent(
        "performance", PERFORMANCE_AGENT_PROMPT,
        state["hint_text"], state["code"], state["language"], writer,
    )
    return {"performance_findings": findings}


async def maintainability_agent(state: dict, writer: StreamWriter) -> dict[str, Any]:
    findings = await _run_agent(
        "maintainability", MAINTAINABILITY_AGENT_PROMPT,
        state["hint_text"], state["code"], state["language"], writer,
    )
    return {"maintainability_findings": findings}


async def api_design_agent(state: dict, writer: StreamWriter) -> dict[str, Any]:
    findings = await _run_agent(
        "api_design", API_DESIGN_AGENT_PROMPT,
        state["hint_text"], state["code"], state["language"], writer,
    )
    return {"api_design_findings": findings}


# ---------------------------------------------------------------------------
# Node: Supervisor summary — merge and deduplicate
# ---------------------------------------------------------------------------

async def supervisor_summary(state: ReviewState, writer: StreamWriter) -> dict[str, Any]:
    """Use LLM to deduplicate and rank findings from all agents."""
    writer("custom", {"event": "status", "status": "summarizing", "message": "Supervisor merging findings..."})

    all_findings: dict[str, list[dict]] = {
        "security": state.get("security_findings", []),
        "performance": state.get("performance_findings", []),
        "maintainability": state.get("maintainability_findings", []),
        "api_design": state.get("api_design_findings", []),
    }

    # If very few findings, skip LLM summarization and do simple dedup
    total = sum(len(v) for v in all_findings.values())
    if total == 0:
        writer("custom", {"event": "status", "status": "summarized", "message": "No issues found by any agent."})
        return {
            "confirmed_findings": [],
            "deduplicated_findings": [],
            "review_summary": "All agents completed review. No issues were identified.",
        }

    findings_text = json.dumps(all_findings, indent=2)
    prompt = SUPERVISOR_PROMPT.format(agent_findings=findings_text)

    llm = build_llm(temperature=0.1)
    messages = [
        SystemMessage(content="You are a lead architect. Output valid JSON only."),
        HumanMessage(content=prompt),
    ]

    full = ""
    async for chunk in llm.astream(messages):
        content = chunk.content if hasattr(chunk, "content") else str(chunk)
        if content:
            full += content

    # Parse supervisor output
    try:
        result = _extract_json_object(full)
        merged = result.get("merged_findings", [])
        summary = result.get("summary", "Review complete.")
    except Exception:
        # Fallback: concatenate all findings
        merged = []
        for cat, items in all_findings.items():
            for item in items:
                merged.append({**item, "source_agent": cat})
        summary = f"Review completed with {len(merged)} total findings."

    writer("custom", {
        "event": "summarized",
        "total_findings": len(merged),
        "summary": summary,
    })

    return {
        "confirmed_findings": merged,
        "deduplicated_findings": merged,
        "review_summary": summary,
    }


# ---------------------------------------------------------------------------
# Node: Human review (INTERRUPT point)
# ---------------------------------------------------------------------------

def human_review_node(state: ReviewState, writer: StreamWriter) -> dict[str, Any]:
    """Present findings to user for confirmation. This runs AFTER the interrupt."""
    # At this point, state contains user's verdicts (injected via Command resume)
    findings = state.get("confirmed_findings", [])
    # The state update from Command already filtered
    writer("custom", {
        "event": "human_review_applied",
        "confirmed_count": len(findings),
    })
    return {}


# ---------------------------------------------------------------------------
# Node: Generate final report
# ---------------------------------------------------------------------------

def generate_report(state: ReviewState, writer: StreamWriter) -> dict[str, Any]:
    """Generate the final structured review report."""
    writer("custom", {"event": "status", "status": "generating", "message": "Generating final report..."})

    findings = state.get("confirmed_findings", state.get("deduplicated_findings", []))

    report = {
        "review_id": state["review_id"],
        "title": state.get("title", "Code Review"),
        "status": ReviewStatus.COMPLETED.value,
        "language": state["language"],
        "code_snippet": state["code"][:500],  # Truncated for report
        "findings": findings,
        "summary": state.get("review_summary", ""),
        "stats": {
            "critical": sum(1 for f in findings if f.get("severity") == "critical"),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "medium": sum(1 for f in findings if f.get("severity") == "medium"),
            "low": sum(1 for f in findings if f.get("severity") == "low"),
            "info": sum(1 for f in findings if f.get("severity") == "info"),
        },
        "agents_involved": state.get("agents_to_run", []),
        "created_at": datetime.now().isoformat(),
        "completed_at": datetime.now().isoformat(),
    }

    writer("custom", {"event": "report_generated", "report": report})

    return {
        "final_report_json": json.dumps(report),
        "status": ReviewStatus.COMPLETED.value,
    }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(checkpointer: SqliteSaver | None = None) -> StateGraph:
    """Build the LangGraph StateGraph for multi-agent code review.

    Graph flow:
        parse_code → supervisor_dispatch → [Send: 4 agents in parallel]
                     ↑                              │
                     └──────── END ←─ generate_report ← human_review ← supervisor_summary

    Key patterns:
    - Conditional edge: supervisor_dispatch → route_to_agents (Send API)
    - Parallel fan-out: Send to security/performance/maintainability/api_design
    - Interrupt: after supervisor_summary, pause for human review
    """
    graph = StateGraph(ReviewState)

    # Register nodes
    graph.add_node("parse_code", parse_code_node)
    graph.add_node("supervisor_dispatch", supervisor_dispatch)
    graph.add_node("security", security_agent)
    graph.add_node("performance", performance_agent)
    graph.add_node("maintainability", maintainability_agent)
    graph.add_node("api_design", api_design_agent)
    graph.add_node("supervisor_summary", supervisor_summary)
    graph.add_node("human_review", human_review_node)
    graph.add_node("generate_report", generate_report)

    # Edges
    graph.set_entry_point("parse_code")
    graph.add_edge("parse_code", "supervisor_dispatch")

    # Conditional fan-out: dispatch → Send to N agents in parallel
    graph.add_conditional_edges(
        "supervisor_dispatch",
        route_to_agents,
        path_map=["security", "performance", "maintainability", "api_design", END],
    )

    # Each agent returns to supervisor_summary
    graph.add_edge("security", "supervisor_summary")
    graph.add_edge("performance", "supervisor_summary")
    graph.add_edge("maintainability", "supervisor_summary")
    graph.add_edge("api_design", "supervisor_summary")

    # Human-in-the-loop: interrupt between summary and report
    graph.add_edge("supervisor_summary", "human_review")
    graph.add_edge("human_review", "generate_report")
    graph.add_edge("generate_report", END)

    # Compile with checkpointer and interrupt point
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(
        checkpointer=checkpointer,
        # Interrupt before human_review so user can review findings
        interrupt_before=["human_review"],
    )

    return compiled


# ---------------------------------------------------------------------------
# Factory: build the app graph
# ---------------------------------------------------------------------------

_graph_instance: StateGraph | None = None


def get_graph() -> StateGraph:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = build_graph()
    return _graph_instance
