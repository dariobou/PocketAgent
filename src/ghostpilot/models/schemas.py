"""Pydantic schemas for GhostPilot IPC, HITL requests, agent events, and sessions."""

from typing import Any

from pydantic import BaseModel, Field

from ghostpilot.models.enums import AgentType, ApprovalDecision, RiskLevel, SessionStatus


class HITLConfirmRequest(BaseModel):
    request_id: str
    agent: AgentType = AgentType.CLAUDE
    tool_name: str
    summary: str
    risk_level: RiskLevel = RiskLevel.MEDIUM
    tool_input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ApprovalResponse(BaseModel):
    request_id: str
    decision: ApprovalDecision
    reason: str | None = None


class AgentStreamEvent(BaseModel):
    event_type: str  # text, tool_use, result, error, info
    content: str
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    session_id: str | None = None
    status: SessionStatus = SessionStatus.STARTING
    current_agent: AgentType = AgentType.CLAUDE
    target_repo: str | None = None
    current_branch: str | None = None
    workspace_path: str = "/tmp/workspace"
    session_trusted: bool = False
    active_prompt: str | None = None


class HookInputPayload(BaseModel):
    session_id: str | None = None
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    agent: AgentType
    session_trusted: bool
    pending_approvals_count: int
