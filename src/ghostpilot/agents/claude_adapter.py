"""Claude Code CLI Adapter."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

from ghostpilot.agents.base import BaseAgentAdapter
from ghostpilot.models.enums import AgentType
from ghostpilot.models.schemas import AgentStreamEvent

logger = logging.getLogger(__name__)


class ClaudeAdapter(BaseAgentAdapter):
    """Adapter for Anthropic's Claude Code CLI tool."""

    @property
    def agent_type(self) -> AgentType:
        return AgentType.CLAUDE

    async def prepare_workspace(self) -> None:
        """
        Configure PreToolUse hook inside <workspace>/.claude/settings.json
        matching 'Bash|Write|Edit|MultiEdit' to 'python3 -m ghostpilot.hooks.hitl_hook'.
        """
        claude_dir = self.workspace_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = claude_dir / "settings.json"

        hook_config = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash|Write|Edit|MultiEdit",
                        "command": "python3 -m ghostpilot.hooks.hitl_hook",
                    }
                ]
            }
        }
        settings_file.write_text(json.dumps(hook_config, indent=2), encoding="utf-8")

    async def stream_prompt(self, prompt: str) -> AsyncGenerator[AgentStreamEvent, None]:
        await self.prepare_workspace()

        # Build exact CLI flags:
        # Initial turn: claude -p "<prompt>" --output-format stream-json --verbose
        # Subsequent: claude -p "<prompt>" --resume "<session_id>" --output-format stream-json --verbose
        cmd = ["claude", "-p", prompt]
        if self.session_id:
            cmd.extend(["--resume", self.session_id])
        cmd.extend(["--output-format", "stream-json", "--verbose"])

        env = os.environ.copy()
        if self.settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.settings.claude_code_oauth_token
        elif self.settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self.settings.anthropic_api_key

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
                # Non-JSON line from verbose output or logs
                yield AgentStreamEvent(event_type="text", content=line, session_id=self.session_id)
                continue

            event_type = data.get("type", "unknown")
            msg_session_id = data.get("session_id")
            if msg_session_id:
                self.session_id = msg_session_id

            if event_type == "assistant":
                # Extract text blocks or tool use summaries
                message = data.get("message", {})
                content_blocks = message.get("content", [])
                for block in content_blocks:
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            yield AgentStreamEvent(
                                event_type="text",
                                content=text,
                                session_id=self.session_id,
                            )
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "tool")
                        tool_input = block.get("input", {})
                        summary = f"🔧 Calling `{tool_name}`: {json.dumps(tool_input)[:120]}"
                        yield AgentStreamEvent(
                            event_type="tool_use",
                            content=summary,
                            session_id=self.session_id,
                            metadata={"tool_name": tool_name, "input": tool_input},
                        )

            elif event_type == "result":
                output_summary = data.get("result", "")
                metrics = data.get("metrics", {})
                content = f"✅ Task finished: {output_summary}" if output_summary else "✅ Task completed."
                yield AgentStreamEvent(
                    event_type="result",
                    content=content,
                    session_id=self.session_id,
                    metadata={"metrics": metrics},
                )
            elif event_type == "error":
                err_msg = data.get("error", "Unknown error")
                yield AgentStreamEvent(
                    event_type="error",
                    content=f"❌ Error: {err_msg}",
                    session_id=self.session_id,
                )

        await proc.wait()
        self.active_process = None
