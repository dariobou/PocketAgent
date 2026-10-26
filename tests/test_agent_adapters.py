"""Unit tests for agent adapters and stream parsing."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ghostpilot.agents.claude_adapter import ClaudeAdapter
from ghostpilot.agents.codex_adapter import CodexAdapter
from ghostpilot.agents.gemini_adapter import GeminiAdapter
from ghostpilot.agents.registry import get_agent_adapter
from ghostpilot.config import Settings
from ghostpilot.models.enums import AgentType


def test_registry_factory(mock_settings: Settings, tmp_path: Path):
    claude = get_agent_adapter(AgentType.CLAUDE, mock_settings, tmp_path)
    assert isinstance(claude, ClaudeAdapter)
    assert claude.agent_type == AgentType.CLAUDE

    codex = get_agent_adapter(AgentType.CODEX, mock_settings, tmp_path)
    assert isinstance(codex, CodexAdapter)

    gemini = get_agent_adapter(AgentType.GEMINI, mock_settings, tmp_path)
    assert isinstance(gemini, GeminiAdapter)


@pytest.mark.asyncio
async def test_claude_adapter_prepare_workspace(mock_settings: Settings, tmp_path: Path):
    adapter = ClaudeAdapter(settings=mock_settings, workspace_dir=tmp_path)
    await adapter.prepare_workspace()

    settings_file = tmp_path / ".claude" / "settings.json"
    assert settings_file.exists()
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    hooks = data["hooks"]["PreToolUse"]
    assert len(hooks) == 1
    assert hooks[0]["matcher"] == "Bash|Write|Edit|MultiEdit"
    assert "ghostpilot.hooks.hitl_hook" in hooks[0]["command"]


@pytest.mark.asyncio
async def test_claude_adapter_stream_parsing(mock_settings: Settings, tmp_path: Path):
    adapter = ClaudeAdapter(settings=mock_settings, workspace_dir=tmp_path)

    claude_jsonl = [
        json.dumps({
            "type": "assistant",
            "session_id": "sess-claude-999",
            "message": {
                "content": [
                    {"type": "text", "text": "I will examine the directory."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                ]
            },
        }),
        json.dumps({
            "type": "result",
            "session_id": "sess-claude-999",
            "result": "Finished analyzing files.",
            "metrics": {"duration_ms": 1200},
        }),
    ]

    mock_stdout = asyncio.StreamReader()
    for line in claude_jsonl:
        mock_stdout.feed_data((line + "\n").encode("utf-8"))
    mock_stdout.feed_eof()

    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        events = []
        async for event in adapter.stream_prompt("Inspect workspace"):
            events.append(event)

    assert len(events) == 3
    assert events[0].event_type == "text"
    assert "examine the directory" in events[0].content
    assert events[1].event_type == "tool_use"
    assert "Calling `Bash`" in events[1].content
    assert events[2].event_type == "result"
    assert "Finished analyzing files" in events[2].content
    assert adapter.session_id == "sess-claude-999"


@pytest.mark.asyncio
async def test_codex_adapter_stream_parsing(mock_settings: Settings, tmp_path: Path):
    adapter = CodexAdapter(settings=mock_settings, workspace_dir=tmp_path)

    codex_jsonl = [
        json.dumps({"type": "reasoning", "content": "Thinking about the bug..."}),
        json.dumps({"type": "tool_call", "name": "shell", "args": {"cmd": "pytest"}}),
        json.dumps({"type": "complete"}),
    ]

    mock_stdout = asyncio.StreamReader()
    for line in codex_jsonl:
        mock_stdout.feed_data((line + "\n").encode("utf-8"))
    mock_stdout.feed_eof()

    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        events = []
        async for event in adapter.stream_prompt("Fix the bug"):
            events.append(event)

    assert len(events) == 3
    assert events[0].content == "Thinking about the bug..."
    assert events[1].event_type == "tool_use"
    assert events[2].event_type == "result"


@pytest.mark.asyncio
async def test_gemini_adapter_stream_parsing(mock_settings: Settings, tmp_path: Path):
    adapter = GeminiAdapter(settings=mock_settings, workspace_dir=tmp_path)

    gemini_lines = [
        "Analyzing code structure...",
        "Found 2 potential optimizations.",
    ]

    mock_stdout = asyncio.StreamReader()
    for line in gemini_lines:
        mock_stdout.feed_data((line + "\n").encode("utf-8"))
    mock_stdout.feed_eof()

    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        events = []
        async for event in adapter.stream_prompt("Review code"):
            events.append(event)

    assert len(events) == 3
    assert events[0].content == "Analyzing code structure..."
    assert events[1].content == "Found 2 potential optimizations."
    assert events[2].event_type == "result"


@pytest.mark.asyncio
async def test_adapter_cancel(mock_settings: Settings, tmp_path: Path):
    adapter = ClaudeAdapter(settings=mock_settings, workspace_dir=tmp_path)
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)

    adapter.active_process = mock_proc
    await adapter.cancel()
    mock_proc.terminate.assert_called_once()
    assert adapter.active_process is None
