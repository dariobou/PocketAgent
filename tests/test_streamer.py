"""Unit tests for Telegram streamer, 3800-char splitting, and rate limit handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ghostpilot.bot.streamer import (
    TelegramStreamBuffer,
    escape_markdown,
    split_text_into_chunks,
)


def test_escape_markdown():
    raw = "Hello_world*test`foo[bar]"
    escaped = escape_markdown(raw)
    assert r"\_" in escaped
    assert r"\*" in escaped
    assert r"\`" in escaped
    assert r"\[" in escaped


def test_split_text_into_chunks():
    short_text = "Hello world"
    assert split_text_into_chunks(short_text, chunk_size=100) == ["Hello world"]

    # Exceeding chunk size
    lines = [f"Line {i}: {'a' * 50}" for i in range(10)]
    full_text = "\n".join(lines)
    chunks = split_text_into_chunks(full_text, chunk_size=200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 200


@pytest.mark.asyncio
async def test_streamer_debounce_and_chunking():
    mock_client = MagicMock()
    mock_client.send_message = AsyncMock(return_value={"message_id": 1001})
    mock_client.edit_message_text = AsyncMock(return_value=True)

    buffer = TelegramStreamBuffer(
        client=mock_client,
        chat_id=12345,
        debounce_interval=0.1,  # Short interval for test
        initial_prefix="Prefix:\n",
    )

    # 1. First append creates message
    await buffer.append("Token 1 ")
    assert mock_client.send_message.call_count == 1
    assert buffer.current_message_id == 1001

    # 2. Rapid append within debounce window should NOT immediately call edit
    await buffer.append("Token 2 ")
    assert mock_client.edit_message_text.call_count == 0

    # 3. Wait past debounce interval and append again -> triggers edit
    await asyncio.sleep(0.12)
    await buffer.append("Token 3 ")
    assert mock_client.edit_message_text.call_count == 1

    # 4. Finalize flushes everything
    await buffer.finalize("Done.")
    assert mock_client.edit_message_text.call_count == 2
    assert buffer.is_finalized is True


@pytest.mark.asyncio
async def test_streamer_rollover_on_large_text():
    mock_client = MagicMock()
    # First message_id 101, second 102
    mock_client.send_message = AsyncMock(side_effect=[{"message_id": 101}, {"message_id": 102}])
    mock_client.edit_message_text = AsyncMock(return_value=True)

    buffer = TelegramStreamBuffer(
        client=mock_client,
        chat_id=12345,
        debounce_interval=0.01,
        initial_prefix="",
    )

    # Append 3000 chars
    await buffer.append("A" * 3000)
    assert buffer.current_message_id == 101

    # Append another 1000 chars -> total 4000 > MAX_MESSAGE_CHARS (3800) -> triggers rollover
    await buffer.append("B" * 1000)
    assert mock_client.send_message.call_count == 2
    assert buffer.current_message_id == 102
