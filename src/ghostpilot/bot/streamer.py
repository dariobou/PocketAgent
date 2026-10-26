"""Debounced live message streamer to handle Telegram rate limits and message chunking."""

import asyncio
import logging
import time

from ghostpilot.bot.telegram_client import TelegramClient

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 3800
DEFAULT_DEBOUNCE_INTERVAL = 1.5


def escape_markdown(text: str) -> str:
    """Escapes Telegram markdown characters to prevent formatting crashes."""
    # Telegram standard markdown (v1) escapes
    chars_to_escape = ["_", "*", "`", "["]
    for char in chars_to_escape:
        text = text.replace(char, f"\\{char}")
    return text


def split_text_into_chunks(text: str, chunk_size: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Splits text cleanly along newlines or character limits."""
    if not text:
        return [""]
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    current_chunk = []
    current_len = 0

    lines = text.split("\n")
    for line in lines:
        line_len = len(line) + 1  # count newline
        if current_len + line_len > chunk_size:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            # If a single line exceeds chunk_size, slice it
            while len(line) > chunk_size:
                chunks.append(line[:chunk_size])
                line = line[chunk_size:]

            if line:
                current_chunk.append(line)
                current_len = len(line) + 1
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


class TelegramStreamBuffer:
    """
    Accumulates streaming output from agents and updates Telegram messages
    at most once every 1.5 seconds, automatically rolling over into a new message
    when exceeding 3800 characters.
    """

    def __init__(
        self,
        client: TelegramClient,
        chat_id: int,
        debounce_interval: float = DEFAULT_DEBOUNCE_INTERVAL,
        initial_prefix: str = "🤖 **GhostPilot Agent**:\n\n",
    ):
        self.client = client
        self.chat_id = chat_id
        self.debounce_interval = debounce_interval
        self.prefix = initial_prefix

        self.current_message_id: int | None = None
        self.current_buffer: str = ""
        self.last_sent_text: str = ""
        self.last_edit_time: float = 0.0
        self.is_finalized: bool = False
        self._lock = asyncio.Lock()

    async def append(self, text: str) -> None:
        """Append text token/line and trigger debounced flush."""
        async with self._lock:
            if self.is_finalized:
                return

            self.current_buffer += text
            full_content = self.prefix + self.current_buffer

            # Check if current message needs to roll over (> 3800 chars)
            if len(full_content) > MAX_MESSAGE_CHARS:
                # Flush and finalize current message
                await self._force_flush_current()
                # Start new message with remaining text
                self.current_message_id = None
                self.prefix = "🤖 *(continuation)*:\n\n"
                self.current_buffer = ""
                self.last_sent_text = ""
                # Put the remaining portion in new buffer
                chunks = split_text_into_chunks(full_content, MAX_MESSAGE_CHARS)
                self.current_buffer = chunks[-1] if len(chunks) > 1 else ""
                # Reset last_edit_time so the continuation message sends immediately
                self.last_edit_time = 0.0

            # Check debounce time
            now = time.time()
            if (now - self.last_edit_time) >= self.debounce_interval:
                await self._flush()

    async def _flush(self) -> None:
        """Internal flush without lock (caller must hold lock or use finalize)."""
        content = (self.prefix + self.current_buffer).strip()
        if not content or content == self.last_sent_text:
            return

        if self.current_message_id is None:
            # Send initial message
            msg = await self.client.send_message(
                chat_id=self.chat_id,
                text=content,
                parse_mode=None,  # Send plain to avoid formatting parse crashes mid-stream
            )
            if msg and "message_id" in msg:
                self.current_message_id = msg["message_id"]
                self.last_sent_text = content
                self.last_edit_time = time.time()
        else:
            # Edit existing message
            ok = await self.client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.current_message_id,
                text=content,
                parse_mode=None,
            )
            if ok:
                self.last_sent_text = content
                self.last_edit_time = time.time()

    async def _force_flush_current(self) -> None:
        """Flush whatever is in the active buffer immediately."""
        content = (self.prefix + self.current_buffer).strip()
        if content and self.current_message_id is not None and content != self.last_sent_text:
            await self.client.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.current_message_id,
                text=content,
                parse_mode=None,
            )
            self.last_sent_text = content
            self.last_edit_time = time.time()

    async def finalize(self, final_text: str | None = None) -> None:
        """Finalize buffer, append final text if provided, and do immediate flush."""
        async with self._lock:
            if final_text:
                self.current_buffer += f"\n\n{final_text}"
            await self._flush()
            self.is_finalized = True
