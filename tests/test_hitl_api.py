"""Unit tests for FastAPI HITL control plane and hook classification."""

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from ghostpilot.api.server import create_app
from ghostpilot.config import Settings
from ghostpilot.hooks.hitl_hook import classify_risk
from ghostpilot.models.enums import ApprovalDecision, RiskLevel


def test_classify_risk_readonly():
    risk, summary = classify_risk("Bash", {"command": "git status"})
    assert risk == RiskLevel.LOW
    assert "read-only" in summary.lower()

    risk_ruff, _ = classify_risk("Bash", {"command": "ruff check ."})
    assert risk_ruff == RiskLevel.LOW

    risk_read, _ = classify_risk("Read", {"file_path": "README.md"})
    assert risk_read == RiskLevel.LOW


def test_classify_risk_mutating():
    risk_rm, _ = classify_risk("Bash", {"command": "rm -rf node_modules"})
    assert risk_rm == RiskLevel.HIGH

    risk_write, _ = classify_risk("Write", {"file_path": "src/main.py"})
    assert risk_write == RiskLevel.MEDIUM

    risk_crit, _ = classify_risk("Bash", {"command": "rm -rf /"})
    assert risk_crit == RiskLevel.CRITICAL


@pytest.mark.asyncio
async def test_hitl_confirm_approval_flow(mock_settings: Settings):
    telegram_dispatched = []

    async def mock_notifier(req: Any):
        telegram_dispatched.append(req.request_id)

    app = create_app(mock_settings, telegram_notifier=mock_notifier)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Task to post confirmation request
        req_payload = {
            "request_id": "req-1234",
            "agent": "claude",
            "tool_name": "Bash",
            "summary": "Run mutating script",
            "risk_level": "HIGH",
            "tool_input": {"command": "rm file.txt"},
        }

        async def send_post():
            return await client.post("/internal/hitl/confirm", json=req_payload)

        post_task = asyncio.create_task(send_post())

        # Wait briefly for request to hit the server and register future
        await asyncio.sleep(0.05)
        assert "req-1234" in telegram_dispatched
        assert "req-1234" in app.state.hitl_state.pending_approvals

        # Simulate user tapping [Approve] on mobile
        resolved = app.state.hitl_state.resolve_approval(
            request_id="req-1234",
            decision=ApprovalDecision.APPROVE,
        )
        assert resolved is True

        resp = await post_task
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "APPROVE"
        assert data["request_id"] == "req-1234"


@pytest.mark.asyncio
async def test_hitl_confirm_deny_flow(mock_settings: Settings):
    app = create_app(mock_settings)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        req_payload = {
            "request_id": "req-deny-1",
            "agent": "claude",
            "tool_name": "Bash",
            "summary": "Dangerous command",
            "risk_level": "HIGH",
        }

        post_task = asyncio.create_task(
            client.post("/internal/hitl/confirm", json=req_payload)
        )
        await asyncio.sleep(0.05)

        # Simulate user tapping [Deny]
        app.state.hitl_state.resolve_approval(
            request_id="req-deny-1",
            decision=ApprovalDecision.DENY,
            reason="User rejected this change.",
        )

        resp = await post_task
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "DENY"
        assert data["reason"] == "User rejected this change."


@pytest.mark.asyncio
async def test_hitl_trust_session_flow(mock_settings: Settings):
    app = create_app(mock_settings)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        req_payload1 = {
            "request_id": "req-trust-1",
            "agent": "claude",
            "tool_name": "Bash",
            "summary": "Step 1",
            "risk_level": "MEDIUM",
        }

        post_task = asyncio.create_task(
            client.post("/internal/hitl/confirm", json=req_payload1)
        )
        await asyncio.sleep(0.05)

        # Simulate user tapping [Trust Session]
        app.state.hitl_state.resolve_approval(
            request_id="req-trust-1",
            decision=ApprovalDecision.TRUST_SESSION,
        )

        resp1 = await post_task
        assert resp1.status_code == 200
        assert app.state.hitl_state.session_trusted is True

        # Subsequent medium/high requests must now auto-approve immediately
        req_payload2 = {
            "request_id": "req-trust-2",
            "agent": "claude",
            "tool_name": "Write",
            "summary": "Step 2",
            "risk_level": "MEDIUM",
        }
        resp2 = await client.post("/internal/hitl/confirm", json=req_payload2)
        assert resp2.status_code == 200
        assert resp2.json()["decision"] == "APPROVE"
        assert "Session trusted" in resp2.json()["reason"]


@pytest.mark.asyncio
async def test_hitl_timeout_flow(mock_settings: Settings):
    # Set short timeout for testing
    mock_settings.hitl_timeout_seconds = 0.1
    app = create_app(mock_settings)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        req_payload = {
            "request_id": "req-timeout-1",
            "agent": "claude",
            "tool_name": "Bash",
            "summary": "Ignored prompt",
            "risk_level": "HIGH",
        }

        resp = await client.post("/internal/hitl/confirm", json=req_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "DENY"
        assert "timed out" in data["reason"].lower()
