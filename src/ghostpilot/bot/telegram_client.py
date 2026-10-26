"""Async Telegram Bot API Client with throttling and rate-limit handling."""

import asyncio
import logging
from typing import Any

import httpx

from ghostpilot.config import Settings

logger = logging.getLogger(__name__)


class TelegramClient:
    """Lightweight, resilient Telegram Bot API client using httpx."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self.http_client = httpx.AsyncClient(timeout=35.0)

    async def close(self):
        await self.http_client.aclose()

    async def _post(self, endpoint: str, json_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        max_retries = 3

        for attempt in range(max_retries):
            try:
                resp = await self.http_client.post(url, json=json_data)
                if resp.status_code == 429:
                    # Rate limited: extract retry_after
                    try:
                        retry_after = resp.json().get("parameters", {}).get("retry_after", 2)
                    except Exception:
                        retry_after = 2
                    logger.warning("Telegram rate-limit hit. Sleeping %s seconds", retry_after)
                    await asyncio.sleep(float(retry_after))
                    continue

                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.error("Telegram API returned not ok: %s", data)
                return data

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    await asyncio.sleep(2.0)
                    continue
                logger.error("HTTP error calling Telegram %s: %s", endpoint, exc)
                raise
            except httpx.RequestError as exc:
                logger.warning("Network error calling Telegram (attempt %s): %s", attempt + 1, exc)
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(1.0)

        return {"ok": False, "error": "Max retries exceeded"}

    async def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        """Call deleteWebhook before entering long-polling mode."""
        res = await self._post("deleteWebhook", {"drop_pending_updates": drop_pending_updates})
        ok = bool(res.get("ok"))
        logger.info("deleteWebhook result: %s", ok)
        return ok

    async def set_webhook(self, url: str, secret_token: str | None = None) -> bool:
        """Call setWebhook to restore Cloudflare Worker gateway on teardown."""
        payload: dict[str, Any] = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token
        res = await self._post("setWebhook", payload)
        ok = bool(res.get("ok"))
        logger.info("setWebhook to %s result: %s", url, ok)
        return ok

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 25,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Long-polling for updates."""
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": allowed_updates or ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset

        try:
            res = await self._post("getUpdates", payload)
            if res.get("ok"):
                return res.get("result", [])
        except Exception as e:
            logger.warning("Error fetching updates: %s", e)
        return []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = "Markdown",
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send a message to a chat."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        res = await self._post("sendMessage", payload)
        if res.get("ok"):
            return res.get("result")
        elif parse_mode == "Markdown":
            # Fallback without markdown if parsing fails
            payload.pop("parse_mode")
            fallback_res = await self._post("sendMessage", payload)
            return fallback_res.get("result") if fallback_res.get("ok") else None
        return None

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str | None = "Markdown",
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Edit an existing message's text."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        res = await self._post("editMessageText", payload)
        if res.get("ok"):
            return True
        elif parse_mode == "Markdown":
            payload.pop("parse_mode")
            fallback_res = await self._post("editMessageText", payload)
            return bool(fallback_res.get("ok"))
        return False

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge a callback button press."""
        payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        res = await self._post("answerCallbackQuery", payload)
        return bool(res.get("ok"))

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        file_bytes: bytes,
        caption: str | None = None,
    ) -> bool:
        """Upload a file (e.g. large git diff)."""
        url = f"{self.base_url}/sendDocument"
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        files = {"document": (filename, file_bytes, "text/plain")}

        try:
            resp = await self.http_client.post(url, data=data, files=files, timeout=60.0)
            return resp.status_code == 200 and resp.json().get("ok", False)
        except Exception as exc:
            logger.error("Failed to send document: %s", exc)
            return False
