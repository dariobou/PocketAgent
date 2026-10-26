"""Tests for BYOS auth hydration and secret log masking."""

import base64
import json
import logging
import os
from pathlib import Path

from ghostpilot.config import Settings
from ghostpilot.workspace.auth_hydrator import AuthHydrator, SecretMaskingFilter


def test_secret_masking_filter():
    filter_instance = SecretMaskingFilter(
        additional_secrets=["my_custom_secret_12345", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"]
    )

    log_record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Found Anthropic key sk-ant-abc123456 and OpenAI sk-1234567890abcdef1234 and PAT ghp_12345678901234567890123456",
        args=(),
        exc_info=None,
    )

    filter_instance.filter(log_record)
    assert "sk-ant-abc123456" not in log_record.msg
    assert "sk-1234567890abcdef1234" not in log_record.msg
    assert "ghp_12345678901234567890123456" not in log_record.msg
    assert "***REDACTED***" in log_record.msg

    # Test literal secret masking
    log_record2 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=11,
        msg="Bot token is 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 with my_custom_secret_12345",
        args=(),
        exc_info=None,
    )
    filter_instance.filter(log_record2)
    assert "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" not in log_record2.msg
    assert "my_custom_secret_12345" not in log_record2.msg


def test_hydrate_claude_bypass_and_token(tmp_path: Path):
    settings = Settings(claude_code_oauth_token="sk-ant-custom-token")
    hydrator = AuthHydrator(settings=settings, home_dir=tmp_path)

    env_updates = hydrator.hydrate_claude()
    claude_json = tmp_path / ".claude.json"

    assert claude_json.exists()
    data = json.loads(claude_json.read_text(encoding="utf-8"))
    assert data["hasCompletedOnboarding"] is True
    assert data["bypassPermissionsModeAccepted"] is False
    assert env_updates["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-custom-token"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-custom-token"


def test_hydrate_codex_base64_decoding(tmp_path: Path):
    raw_json = json.dumps({"session": "codex-test-session", "key": "abc"})
    b64_str = base64.b64encode(raw_json.encode()).decode()

    settings = Settings(codex_auth_json_b64=b64_str)
    hydrator = AuthHydrator(settings=settings, home_dir=tmp_path)

    auth_file = hydrator.hydrate_codex()
    assert auth_file is not None
    assert auth_file.exists()
    assert auth_file == tmp_path / ".codex" / "auth.json"

    content = json.loads(auth_file.read_text(encoding="utf-8"))
    assert content["session"] == "codex-test-session"


def test_hydrate_gemini_base64_decoding(tmp_path: Path):
    raw_json = json.dumps({"client_id": "gemini-client-id", "refresh_token": "rt123"})
    b64_str = base64.b64encode(raw_json.encode()).decode()

    settings = Settings(gemini_oauth_b64=b64_str)
    hydrator = AuthHydrator(settings=settings, home_dir=tmp_path)

    creds_file = hydrator.hydrate_gemini()
    assert creds_file is not None
    assert creds_file.exists()
    assert creds_file == tmp_path / ".gemini" / "oauth_creds.json"

    content = json.loads(creds_file.read_text(encoding="utf-8"))
    assert content["client_id"] == "gemini-client-id"


def test_hydrate_all(tmp_path: Path):
    settings = Settings(
        claude_code_oauth_token="sk-ant-test",
        codex_auth_json_b64=base64.b64encode(b"{}").decode(),
        gemini_oauth_b64=base64.b64encode(b"{}").decode(),
    )
    hydrator = AuthHydrator(settings=settings, home_dir=tmp_path)
    env_updates = hydrator.hydrate_all()

    assert "CLAUDE_CODE_OAUTH_TOKEN" in env_updates
    assert (tmp_path / ".claude.json").exists()
    assert (tmp_path / ".codex" / "auth.json").exists()
    assert (tmp_path / ".gemini" / "oauth_creds.json").exists()
