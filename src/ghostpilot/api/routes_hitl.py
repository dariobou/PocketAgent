"""State manager and routes for Human-in-the-Loop (HITL) confirmation."""

import asyncio
import logging

from fastapi import APIRouter, Request

from ghostpilot.models.enums import ApprovalDecision, RiskLevel
from ghostpilot.models.schemas import ApprovalResponse, HealthResponse, HITLConfirmRequest

logger = logging.getLogger(__name__)


class HITLStateManager:
    """In-memory state holding pending approvals and session-trust flag."""

    def __init__(self, telegram_notifier=None, default_timeout: float = 290.0):
        self.pending_approvals: dict[str, asyncio.Future[ApprovalResponse]] = {}
        self.session_trusted: bool = False
        self.telegram_notifier = telegram_notifier
        self.default_timeout = default_timeout

    def register_approval(self, request_id: str) -> asyncio.Future[ApprovalResponse]:
        """Create and register a future for this request_id."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        self.pending_approvals[request_id] = future
        return future

    def resolve_approval(
        self,
        request_id: str,
        decision: ApprovalDecision,
        reason: str | None = None,
    ) -> bool:
        """Resolve pending approval Future from Telegram callback."""
        future = self.pending_approvals.pop(request_id, None)
        if future and not future.done():
            if decision == ApprovalDecision.TRUST_SESSION:
                self.session_trusted = True
            future.set_result(
                ApprovalResponse(request_id=request_id, decision=decision, reason=reason)
            )
            return True
        return False


router = APIRouter(prefix="/internal", tags=["HITL Control Plane"])


@router.post("/hitl/confirm", response_model=ApprovalResponse)
async def confirm_action(req: HITLConfirmRequest, request: Request):
    """
    Called by pre-execution hooks. Blocks until mobile user responds on Telegram
    or timeout expires.
    """
    state_manager: HITLStateManager = request.app.state.hitl_state

    # 1. Check if whole session is already trusted (unless CRITICAL)
    if state_manager.session_trusted and req.risk_level != RiskLevel.CRITICAL:
        logger.info("Auto-approving request %s (Session is trusted)", req.request_id)
        return ApprovalResponse(
            request_id=req.request_id,
            decision=ApprovalDecision.APPROVE,
            reason="Session trusted by user",
        )

    # 2. Register future
    future = state_manager.register_approval(req.request_id)

    # 3. Notify user via Telegram
    if state_manager.telegram_notifier:
        try:
            await state_manager.telegram_notifier(req)
        except Exception as e:
            logger.error("Failed to dispatch Telegram HITL prompt: %s", e)

    # 4. Await user response or timeout
    try:
        response = await asyncio.wait_for(future, timeout=state_manager.default_timeout)
        return response
    except TimeoutError:
        state_manager.pending_approvals.pop(req.request_id, None)
        logger.warning("HITL request %s timed out waiting for approval", req.request_id)
        return ApprovalResponse(
            request_id=req.request_id,
            decision=ApprovalDecision.DENY,
            reason="Approval timed out on mobile device",
        )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Health status of the control plane."""
    state_manager: HITLStateManager = request.app.state.hitl_state
    session_state = getattr(request.app.state, "session_state", None)
    agent = session_state.current_agent if session_state else "claude"

    return HealthResponse(
        status="running",
        agent=agent,
        session_trusted=state_manager.session_trusted,
        pending_approvals_count=len(state_manager.pending_approvals),
    )
