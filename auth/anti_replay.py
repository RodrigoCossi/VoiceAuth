"""Replay attack prevention via SHA-256 hash deque."""

from __future__ import annotations

import hashlib
from collections import deque


class AntiReplayCache:
    """Tracks hashes of the last N processed audio files to detect replays."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._seen: deque[str] = deque(maxlen=maxsize)

    def hash(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def is_replay(self, audio_bytes: bytes) -> tuple[bool, str]:
        """Returns (is_replay, hash). Call register() only if not a replay."""
        h = self.hash(audio_bytes)
        return h in self._seen, h

    def register(self, audio_hash: str) -> None:
        self._seen.append(audio_hash)
