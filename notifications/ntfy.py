"""Push notification sender via ntfy.sh (or self-hosted ntfy)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0


class NtfyNotifier:
    def __init__(self, config) -> None:
        self._topic = config.ntfy_topic
        self._server = config.ntfy_server_url.rstrip("/")

    async def _post(self, title: str, body: str, priority: str, tags: str) -> None:
        if not self._topic:
            return
        url = f"{self._server}/{self._topic}"
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": tags,
        }
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status < 300:
                            return
                        logger.warning("ntfy returned %d on attempt %d", resp.status, attempt)
            except Exception as exc:
                logger.warning("ntfy error attempt %d: %s", attempt, exc)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_BACKOFF_BASE ** attempt)

    async def unknown_user(self, user_id: int, username: Optional[str], chat_id: int, msg_type: str) -> None:
        body = (
            f"User ID: {user_id}\n"
            f"Username: @{username or 'unknown'}\n"
            f"Chat ID: {chat_id}\n"
            f"Message type: {msg_type}"
        )
        await self._post("🚨 Unknown user attempted access", body, "high", "warning")

    async def failed_voice(
        self,
        user_id: int,
        username: Optional[str],
        chat_id: int,
        similarity: float,
        attempt_num: int,
        timestamp: str,
    ) -> None:
        body = (
            f"Timestamp: {timestamp}\n"
            f"User ID: {user_id}\n"
            f"Username: @{username or 'unknown'}\n"
            f"Chat ID: {chat_id}\n"
            f"Similarity score: {similarity:.4f}\n"
            f"Attempt #: {attempt_num}"
        )
        await self._post("🚨 Unauthorized Voice Attempt", body, "high", "warning")

    async def replay_attack(self, user_id: int, username: Optional[str], chat_id: int) -> None:
        body = (
            f"User ID: {user_id}\n"
            f"Username: @{username or 'unknown'}\n"
            f"Chat ID: {chat_id}"
        )
        await self._post("⚠️ Replay Attack Detected", body, "urgent", "rotating_light")

    async def pin_lockout(self, user_id: int, chat_id: int) -> None:
        body = f"User ID: {user_id}\nChat ID: {chat_id}\nPIN rate limit exceeded — locked out."
        await self._post("🚨 PIN Lockout", body, "high", "warning")
