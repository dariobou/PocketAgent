"""Telegram interactive command handlers and HITL callback routing."""

import asyncio
import logging
from typing import Any

from ghostpilot.agents.registry import get_agent_adapter
from ghostpilot.bot.streamer import TelegramStreamBuffer
from ghostpilot.bot.telegram_client import TelegramClient
from ghostpilot.config import Settings
from ghostpilot.models.enums import AgentType, ApprovalDecision, RiskLevel, SessionStatus
from ghostpilot.models.schemas import HITLConfirmRequest, SessionState
from ghostpilot.workspace.git_manager import GitManager

logger = logging.getLogger(__name__)


class TelegramBotHandler:
    """Dispatches Telegram updates to command handlers or HITL callback resolvers."""

    def __init__(
        self,
        settings: Settings,
        telegram_client: TelegramClient,
        git_manager: GitManager,
        session_state: SessionState,
        hitl_state_manager,
        shutdown_event: asyncio.Event,
    ):
        self.settings = settings
        self.client = telegram_client
        self.git = git_manager
        self.session_state = session_state
        self.hitl_state = hitl_state_manager
        self.shutdown_event = shutdown_event
        self.current_agent_adapter = get_agent_adapter(
            self.session_state.current_agent,
            self.settings,
            self.git.current_repo_path,
        )

    def is_authorized(self, user_id: int) -> bool:
        """Security Check: Verify user against authorized user IDs."""
        if not self.settings.telegram_allowed_user_ids:
            return False
        return user_id in self.settings.telegram_allowed_user_ids

    async def handle_update(self, update: dict[str, Any]) -> None:
        """Entry point for incoming Telegram updates."""
        # 1. Handle Callback Queries (HITL buttons)
        if "callback_query" in update:
            await self._handle_callback_query(update["callback_query"])
            return

        # 2. Handle Messages
        if "message" in update:
            await self._handle_message(update["message"])

    async def _handle_callback_query(self, query: dict[str, Any]) -> None:
        user = query.get("from", {})
        user_id = user.get("id")
        query_id = query.get("id")
        data = query.get("data", "")

        if not user_id or not self.is_authorized(user_id):
            logger.warning("Unauthorized callback query attempt by user_id: %s", user_id)
            if query_id:
                await self.client.answer_callback_query(query_id, text="Unauthorized access", show_alert=True)
            return

        # Pattern: hitl:<action>:<request_id>
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "hitl":
            action, request_id = parts[1], parts[2]
            decision = {
                "approve": ApprovalDecision.APPROVE,
                "deny": ApprovalDecision.DENY,
                "trust": ApprovalDecision.TRUST_SESSION,
            }.get(action, ApprovalDecision.DENY)

            resolved = self.hitl_state.resolve_approval(
                request_id=request_id,
                decision=decision,
                reason=f"Action chosen by mobile user ({action})",
            )

            ack_text = f"Action {decision.value} registered!" if resolved else "Request already resolved or expired."
            if query_id:
                await self.client.answer_callback_query(query_id, text=ack_text)

            # Update the original prompt message to indicate resolution
            msg = query.get("message")
            if msg and "message_id" in msg and "chat" in msg:
                chat_id = msg["chat"]["id"]
                orig_text = msg.get("text", "")
                await self.client.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg["message_id"],
                    text=f"{orig_text}\n\n👉 **Decision Registered:** `{decision.value}`",
                )

    async def _handle_message(self, message: dict[str, Any]) -> None:
        user = message.get("from", {})
        user_id = user.get("id")
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "").strip()

        if not user_id or not self.is_authorized(user_id):
            logger.warning("Unauthorized message received from user_id: %s", user_id)
            return

        if not text or not chat_id:
            return

        # Route commands
        if text.startswith("/"):
            await self._dispatch_command(chat_id, text)
        else:
            # Natural language prompt sent to active coding agent
            await self.execute_prompt(chat_id, text)

    async def _dispatch_command(self, chat_id: int, command_text: str) -> None:
        parts = command_text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/start", "/help"):
            branch = await self.git.get_current_branch()
            msg = (
                "👋 **Welcome to GhostPilot Control**\n\n"
                f"• **Active Agent:** `{self.session_state.current_agent.value}`\n"
                f"• **Target Repo:** `{self.session_state.target_repo or 'None'}`\n"
                f"• **Branch:** `{branch}`\n"
                f"• **Session Status:** `{self.session_state.status.value}`\n\n"
                "**Available Commands:**\n"
                "/repo <owner/repo> [branch] - Switch repository\n"
                "/agent <claude|codex|gemini> - Switch coding agent\n"
                "/diff - View git diff in workspace\n"
                "/pr [title] - Commit and open GitHub Pull Request\n"
                "/status - Check current sandbox status\n"
                "/stop - Terminate session & restore Cloudflare webhook\n"
            )
            await self.client.send_message(chat_id, msg)

        elif cmd == "/status":
            branch = await self.git.get_current_branch()
            msg = (
                f"📊 **GhostPilot Status:**\n"
                f"• Status: `{self.session_state.status.value}`\n"
                f"• Agent: `{self.session_state.current_agent.value}`\n"
                f"• Repo: `{self.session_state.target_repo}` (Branch: `{branch}`)\n"
                f"• Session Trusted: `{self.hitl_state.session_trusted}`\n"
                f"• Pending Approvals: `{len(self.hitl_state.pending_approvals)}`"
            )
            await self.client.send_message(chat_id, msg)

        elif cmd == "/repo":
            if not args:
                await self.client.send_message(chat_id, "Usage: `/repo <owner/repo> [branch]`")
                return
            repo_slug = args[0]
            branch = args[1] if len(args) > 1 else None
            await self.client.send_message(chat_id, f"Cloning / switching repo to `{repo_slug}`...")
            try:
                target_path = await self.git.clone_or_switch_repo(repo_slug, branch)
                self.session_state.target_repo = repo_slug
                self.session_state.workspace_path = str(target_path)
                # Reinitialize agent adapter for new workspace
                self.current_agent_adapter = get_agent_adapter(
                    self.session_state.current_agent,
                    self.settings,
                    target_path,
                )
                curr_branch = await self.git.get_current_branch()
                await self.client.send_message(
                    chat_id,
                    f"✅ Workspace ready at `{repo_slug}` (Branch: `{curr_branch}`)",
                )
            except Exception as e:
                await self.client.send_message(chat_id, f"❌ Failed to switch repo: {e!s}")

        elif cmd == "/agent":
            if not args or args[0].lower() not in ("claude", "codex", "gemini"):
                await self.client.send_message(chat_id, "Usage: `/agent <claude|codex|gemini>`")
                return
            chosen = AgentType(args[0].lower())
            self.session_state.current_agent = chosen
            self.current_agent_adapter = get_agent_adapter(
                chosen,
                self.settings,
                self.git.current_repo_path,
            )
            await self.client.send_message(chat_id, f"🤖 Active agent switched to `{chosen.value}`")

        elif cmd == "/diff":
            diff_text = await self.git.get_diff()
            if len(diff_text) > 3800:
                await self.client.send_document(
                    chat_id=chat_id,
                    filename="workspace.diff",
                    file_bytes=diff_text.encode("utf-8"),
                    caption="📄 Git diff exceeds 3800 characters (attached as file)",
                )
            else:
                await self.client.send_message(chat_id, f"```diff\n{diff_text}\n```")

        elif cmd == "/pr":
            title = " ".join(args) if args else None
            await self.client.send_message(chat_id, "Creating Pull Request...")
            try:
                result = await self.git.create_pull_request(title=title)
                await self.client.send_message(chat_id, f"🚀 **Pull Request Result:**\n{result}")
            except Exception as e:
                await self.client.send_message(chat_id, f"❌ PR creation failed: {e}")

        elif cmd == "/stop":
            await self.client.send_message(chat_id, "🛑 Stopping GhostPilot session and tearing down runner...")
            await self.current_agent_adapter.cancel()
            self.session_state.status = SessionStatus.STOPPING
            self.shutdown_event.set()

        else:
            await self.client.send_message(chat_id, f"Unknown command: `{cmd}`. Type `/help` for options.")

    async def execute_prompt(self, chat_id: int, prompt: str) -> None:
        """Stream an agent prompt to Telegram using TelegramStreamBuffer."""
        self.session_state.status = SessionStatus.RUNNING
        self.session_state.active_prompt = prompt

        stream_buffer = TelegramStreamBuffer(
            client=self.client,
            chat_id=chat_id,
            debounce_interval=1.5,
            initial_prefix=f"🤖 **GhostPilot ({self.session_state.current_agent.value})**:\n\n",
        )

        try:
            async for event in self.current_agent_adapter.stream_prompt(prompt):
                if event.event_type == "text":
                    await stream_buffer.append(f"{event.content}\n")
                elif event.event_type == "tool_use":
                    await stream_buffer.append(f"\n{event.content}\n")
                elif event.event_type == "result" or event.event_type == "error":
                    await stream_buffer.finalize(f"\n{event.content}")
        except Exception as exc:
            logger.error("Error streaming prompt: %s", exc)
            await stream_buffer.finalize(f"\n❌ Execution error: {exc}")
        finally:
            self.session_state.status = SessionStatus.IDLE

    async def send_hitl_notification(self, req: HITLConfirmRequest) -> None:
        """Send HITL inline keyboard prompt to the active Telegram chat."""
        chat_id = self.settings.telegram_chat_id
        if not chat_id and self.settings.telegram_allowed_user_ids:
            chat_id = self.settings.telegram_allowed_user_ids[0]

        if not chat_id:
            logger.error("No chat_id configured to deliver HITL prompt")
            return

        risk_emoji = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🔴",
            RiskLevel.CRITICAL: "🚨",
        }.get(req.risk_level, "⚠️")

        msg_text = (
            f"{risk_emoji} **Approval Required (Risk: {req.risk_level.value})**\n\n"
            f"• **Tool:** `{req.tool_name}`\n"
            f"• **Action:** {req.summary}\n"
            f"• **Request ID:** `{req.request_id}`\n\n"
            "Do you approve this operation?"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"hitl:approve:{req.request_id}"},
                    {"text": "❌ Deny", "callback_data": f"hitl:deny:{req.request_id}"},
                ],
                [
                    {"text": "🛡️ Trust Session", "callback_data": f"hitl:trust:{req.request_id}"},
                ],
            ]
        }

        await self.client.send_message(chat_id=chat_id, text=msg_text, reply_markup=reply_markup)
