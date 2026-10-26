"""OpenAI Codex CLI Adapter."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

from ghostpilot.agents.base import BaseAgentAdapter
from ghostpilot.models.enums import AgentType
from ghostpilot.models.schemas import AgentStreamEvent

logger = logging.getLogger(__name__)


class CodexAdapter(BaseAgentAdapter):
    """Adapter for OpenAI Codex CLI tool."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CODEX

    async def prepare_workspace(self) -> None:
        """Codex credentials already hydrated into ~/.codex/auth.json."""

    async def stream_prompt(self, prompt: str) -> AsyncGenerator[AgentStreamEvent, None]:
        await self.prepare_workspace()

        # Build exact CLI flags:
        # codex exec "<prompt>" --json
        cmd = ["codex", "exec", prompt, "--json"]

        env = os.environ.copy()
        if self.settings.openai_api_key:
            env["OPENAI_API_KEY"] = self.settings.openai_api_key

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

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                yield AgentStreamEvent(event_type="text", content=line, session_id=self.session_id)
                continue

            event_type = data.get("type", "update")
            msg_session_id = data.get("session_id")
            if msg_session_id:
                self.session_id = msg_session_id

            if event_type in ("message", "reasoning", "text"):
                text = data.get("content") or data.get("text") or ""
                if text:
                    yield AgentStreamEvent(event_type="text", content=text, session_id=self.session_id)
            elif event_type == "tool_call":
                tool_name = data.get("name", "tool")
                args = data.get("args", {})
                yield AgentStreamEvent(
                    event_type="tool_use",
                    content=f"🔧 Codex executing `{tool_name}`: {str(args)[:100]}",
                    session_id=self.session_id,
                    metadata={"tool": tool_name, "args": args},
                )
            elif event_type in ("complete", "done"):
                yield AgentStreamEvent(
                    event_type="result",
                    content="✅ Codex task finished.",
                    session_id=self.session_id,
                )

        await proc.wait()
        self.active_process = None
