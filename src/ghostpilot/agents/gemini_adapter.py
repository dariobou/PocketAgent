"""Google Gemini CLI Adapter."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator

from ghostpilot.agents.base import BaseAgentAdapter
from ghostpilot.models.enums import AgentType
from ghostpilot.models.schemas import AgentStreamEvent

logger = logging.getLogger(__name__)


class GeminiAdapter(BaseAgentAdapter):
    """Adapter for Google Gemini CLI tool."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.GEMINI

    async def prepare_workspace(self) -> None:
        """Gemini OAuth already hydrated into ~/.gemini/oauth_creds.json."""

    async def stream_prompt(self, prompt: str) -> AsyncGenerator[AgentStreamEvent, None]:
        await self.prepare_workspace()

        # Build exact CLI flags:
        # gemini -p "<prompt>"
        cmd = ["gemini", "-p", prompt]

        env = os.environ.copy()
        if self.settings.gemini_api_key:
            env["GEMINI_API_KEY"] = self.settings.gemini_api_key

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.workspace_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.active_process = proc

        if proc.stdout is None:
            return

        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break

            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            yield AgentStreamEvent(
                event_type="text",
                content=line,
                session_id=self.session_id,
            )

        await proc.wait()
        yield AgentStreamEvent(
            event_type="result",
            content="✅ Gemini task completed.",
            session_id=self.session_id,
        )
        self.active_process = None
