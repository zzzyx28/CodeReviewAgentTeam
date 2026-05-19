from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AgentRole(str, Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    API_DESIGN = "api_design"


class ReviewStatus(str, Enum):
    PARSING = "parsing"
    DISPATCHING = "dispatching"
    REVIEWING = "reviewing"
    AWAITING_HUMAN = "awaiting_human"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    ERROR = "error"


class CodeInput(BaseModel):
    code: str = Field(..., min_length=1, description="Source code to review")
    language: str | None = Field(None, description="Programming language hint")
    title: str | None = Field(None, description="Optional title for this review")


class ReviewRequest(BaseModel):
    code: str = Field(..., min_length=1)
    language: str | None = None
    title: str | None = None


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    agent: AgentRole
    severity: Severity
    title: str
    description: str
    line_start: int | None = None
    line_end: int | None = None
    file_path: str = ""
    code_snippet: str = ""
    suggestion: str = ""
    cwe_id: str | None = None

    def summary(self) -> str:
        lines = f" (lines {self.line_start}-{self.line_end})" if self.line_start else ""
        return f"[{self.severity.value.upper()}] {self.title}{lines}"


class AgentProgress(BaseModel):
    agent: AgentRole
    status: Literal["started", "running", "completed", "error"]
    message: str = ""
    findings: list[Finding] = []
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ReviewReport(BaseModel):
    review_id: str
    title: str
    status: ReviewStatus
    language: str
    code_snippet: str
    findings: list[Finding]
    summary: str
    created_at: str
    completed_at: str | None = None


class HumanVerdict(BaseModel):
    finding_id: str
    action: Literal["confirm", "dismiss"]


# LangGraph State type annotation helper
ReviewState = dict[str, Any]
