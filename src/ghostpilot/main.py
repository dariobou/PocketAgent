"""GhostPilot Main Async Entrypoint orchestrating Control Plane, Telegram Polling, and Handover."""

import asyncio
import logging
import sys
import time

import uvicorn

from ghostpilot.api.server import create_app
from ghostpilot.bot.handlers import TelegramBotHandler
from ghostpilot.bot.telegram_client import TelegramClient
from ghostpilot.config import settings
from ghostpilot.models.enums import SessionStatus
from ghostpilot.models.schemas import SessionState
from ghostpilot.workspace.auth_hydrator import AuthHydrator
from ghostpilot.workspace.git_manager import GitManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ghostpilot")


async def polling_loop(
    telegram_client: TelegramClient,
    bot_handler: TelegramBotHandler,
    shutdown_event: asyncio.Event,
    idle_tracker: dict[str, float],
):
    """Long-polling update loop for Telegram updates."""
    offset = None
    logger.info("Starting Telegram long-polling loop...")

    while not shutdown_event.is_set():
        try:
            updates = await telegram_client.get_updates(offset=offset, timeout=10)
            for update in updates:
                offset = update["update_id"] + 1
                idle_tracker["last_activity"] = time.time()
                await bot_handler.handle_update(update)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in polling loop: %s", e)
            await asyncio.sleep(2.0)


async def watchdog_task(
    shutdown_event: asyncio.Event,
    idle_tracker: dict[str, float],
    session_start_time: float,
    idle_timeout_seconds: int,
    max_session_minutes: int,
):
    """Monitors idle inactivity and max budget limits."""
    max_seconds = max_session_minutes * 60
    while not shutdown_event.is_set():
        await asyncio.sleep(5.0)
        now = time.time()

        # Check total budget limit
        if (now - session_start_time) >= max_seconds:
            logger.warning("Max session budget reached (%s minutes). Initiating teardown.", max_session_minutes)
            shutdown_event.set()
            break

        # Check idle inactivity
        idle_duration = now - idle_tracker["last_activity"]
        if idle_duration >= idle_timeout_seconds:
            logger.warning("Idle timeout reached (%s seconds). Initiating teardown.", idle_timeout_seconds)
            shutdown_event.set()
            break


async def run_ghostpilot():
    """Main async runner execution."""
    session_start_time = time.time()
    idle_tracker = {"last_activity": time.time()}
    shutdown_event = asyncio.Event()

    # 1. Initialize Security Scrubber & Auth Hydrator
    hydrator = AuthHydrator(settings)
    hydrator.configure_logging_scrubber()
    logger.info("Hydrating BYOS credentials and setting file permissions...")
    hydrator.hydrate_all()

    # 2. Setup Git workspace & Session State
    session_state = SessionState(
        current_agent=settings.active_agent,
        target_repo=settings.target_repo,
        default_branch=settings.default_branch,
    )
    git_manager = GitManager(settings)

    if settings.target_repo:
        logger.info("Cloning target repository: %s", settings.target_repo)
        try:
            target_dir = await git_manager.clone_or_switch_repo(settings.target_repo, settings.default_branch)
            session_state.workspace_path = str(target_dir)
        except Exception as e:
            logger.error("Failed to clone initial repo %s: %s", settings.target_repo, e)

    # 3. Instantiate Telegram Client & Control Plane App
    telegram_client = TelegramClient(settings)
    app = create_app(
        settings=settings,
        telegram_notifier=None,  # Will attach below
        session_state=session_state,
    )
    hitl_state_manager = app.state.hitl_state

    # 4. Instantiate Bot Handler & link HITL notifier
    bot_handler = TelegramBotHandler(
        settings=settings,
        telegram_client=telegram_client,
        git_manager=git_manager,
        session_state=session_state,
        hitl_state_manager=hitl_state_manager,
        shutdown_event=shutdown_event,
    )
    hitl_state_manager.telegram_notifier = bot_handler.send_hitl_notification

    # 5. CRITICAL HANDOVER: Delete Webhook before long-polling
    logger.info("Executing Webhook-to-Polling handover (deleteWebhook)...")
    await telegram_client.delete_webhook(drop_pending_updates=False)

    # 6. Start Uvicorn FastAPI Server on 127.0.0.1:8765
    config = uvicorn.Config(
        app=app,
        host=settings.host,
        port=settings.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    # 7. Start Polling Loop & Watchdog Tasks
    polling_task = asyncio.create_task(
        polling_loop(telegram_client, bot_handler, shutdown_event, idle_tracker)
    )
    watchdog = asyncio.create_task(
        watchdog_task(
            shutdown_event,
            idle_tracker,
            session_start_time,
            settings.idle_timeout_seconds,
            settings.max_session_minutes,
        )
    )

    session_state.status = SessionStatus.IDLE

    # 8. Notify user of startup and execute initial prompt if provided
    initial_chat_id = settings.telegram_chat_id
    if not initial_chat_id and settings.telegram_allowed_user_ids:
        initial_chat_id = settings.telegram_allowed_user_ids[0]

    if initial_chat_id:
        branch = await git_manager.get_current_branch()
        await telegram_client.send_message(
            initial_chat_id,
            f"⚡ **GhostPilot Online**\n"
            f"• Agent: `{session_state.current_agent.value}`\n"
            f"• Repo: `{session_state.target_repo or 'None'}` (Branch: `{branch}`)\n"
            f"Ready for your prompts or `/help`.",
        )

        if settings.initial_prompt:
            logger.info("Executing initial prompt: %s", settings.initial_prompt)
            await bot_handler.execute_prompt(initial_chat_id, settings.initial_prompt)

    try:
        # Await shutdown signal
        await shutdown_event.wait()
        logger.info("Shutdown event received, terminating runtime tasks...")
    finally:
        # 9. CRITICAL TEARDOWN: Restore Cloudflare Worker Webhook
        logger.info("Initiating teardown: Restoring Cloudflare Worker Webhook...")
        if settings.cloudflare_worker_url:
            try:
                await telegram_client.set_webhook(
                    url=settings.cloudflare_worker_url,
                    secret_token=settings.telegram_webhook_secret or None,
                )
                logger.info("Cloudflare Worker webhook restored successfully.")
            except Exception as e:
                logger.error("Failed to restore Cloudflare Worker webhook: %s", e)

        # Cancel tasks
        polling_task.cancel()
        watchdog.cancel()
        server.should_exit = True
        await asyncio.gather(polling_task, watchdog, server_task, return_exceptions=True)
        await telegram_client.close()
        logger.info("GhostPilot runtime successfully torn down.")


def main():
    """Main CLI entrypoint."""
    try:
        asyncio.run(run_ghostpilot())
    except KeyboardInterrupt:
        logger.info("GhostPilot interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
