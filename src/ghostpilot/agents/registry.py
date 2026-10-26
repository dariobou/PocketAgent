"""Factory registry for agent adapters."""

from pathlib import Path

from ghostpilot.agents.base import BaseAgentAdapter
from ghostpilot.agents.claude_adapter import ClaudeAdapter
from ghostpilot.agents.codex_adapter import CodexAdapter
from ghostpilot.agents.gemini_adapter import GeminiAdapter
from ghostpilot.config import Settings
from ghostpilot.models.enums import AgentType


def get_agent_adapter(
    agent_type: AgentType,
    settings: Settings,
    workspace_dir: Path | None = None,
) -> BaseAgentAdapter:
    """Return an instantiated agent adapter for the given AgentType."""
    target_dir = workspace_dir or Path(settings.workspace_dir)

    if agent_type == AgentType.CLAUDE:
        return ClaudeAdapter(settings=settings, workspace_dir=target_dir)
    elif agent_type == AgentType.CODEX:
        return CodexAdapter(settings=settings, workspace_dir=target_dir)
    elif agent_type == AgentType.GEMINI:
        return GeminiAdapter(settings=settings, workspace_dir=target_dir)
    else:
        raise ValueError(f"Unsupported agent type: {agent_type}")
