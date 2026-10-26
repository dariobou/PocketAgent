"""Enums for GhostPilot runtime, agents, and HITL decisions."""

from enum import Enum


class AgentType(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    GEMINI = "gemini"


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    DENY = "DENY"
    TRUST_SESSION = "TRUST_SESSION"


class SessionStatus(str, Enum):
    STARTING = "STARTING"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    STOPPING = "STOPPING"
    TERMINATED = "TERMINATED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
