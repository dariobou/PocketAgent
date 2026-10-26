"""Abstract Base Agent Adapter."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from pathlib import Path

from ghostpilot.config import Settings
from ghostpilot.models.enums import AgentType
from ghostpilot.models.schemas import AgentStreamEvent


class BaseAgentAdapter(ABC):
    """Abstract interface for coding agent CLIs."""

    def __init__(self, settings: Settings, workspace_dir: Path):
        self.settings = settings
        self.workspace_dir = workspace_dir
        self.active_process: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None

    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        """The agent enum type."""

    @abstractmethod
    async def prepare_workspace(self) -> None:
        """Perform pre-execution setup (e.g. write hooks or settings)."""

    @abstractmethod
    async def stream_prompt(self, prompt: str) -> AsyncGenerator[AgentStreamEvent, None]:
        """Execute a prompt and yield structured stream events."""

    async def cancel(self) -> None:
        """Terminate the active subprocess gracefully with SIGTERM then SIGKILL."""
        if self.active_process and self.active_process.returncode is None:
            try:
                self.active_process.terminate()
                await asyncio.wait_for(self.active_process.wait(), timeout=3.0)
            except (TimeoutError, ProcessLookupError):
                try:
                    self.active_process.kill()
                    await self.active_process.wait()
                except ProcessLookupError:
                    pass
            finally:
                self.active_process = None
