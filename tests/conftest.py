"""Pytest shared fixtures."""

from pathlib import Path

import pytest

from ghostpilot.config import Settings


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    """Provide isolated Settings pointing to tmp_path."""
    return Settings(
        telegram_bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        telegram_allowed_user_ids=[123456789],
        telegram_chat_id=123456789,
        telegram_webhook_secret="test_webhook_secret_999",
        cloudflare_worker_url="https://ghostpilot.workers.dev",
        workspace_dir=str(tmp_path / "workspace"),
        target_repo="test-owner/test-repo",
        default_branch="main",
        github_pat="ghp_testpat12345678901234567890123456",
        claude_code_oauth_token="sk-ant-test-oauth-token-12345",
        codex_auth_json_b64="eyJ0b2tlbiI6ICJ0ZXN0X2NvZGV4X3Rva2VuIn0=",  # {"token": "test_codex_token"}
        gemini_oauth_b64="eyJjbGllbnRfaWQiOiAidGVzdF9nZW1pbmkifQ==",    # {"client_id": "test_gemini"}
    )
