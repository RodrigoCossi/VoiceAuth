"""PIN authentication — first factor of the two-factor gate (PIN, then voice).

State machine per user_id:
  idle → waiting_for_pin (60 s timeout) → pin_verified (5 min TTL) → idle

A correct PIN does not start a session by itself; it only unlocks the voice
step. `pin_verified` is consumed the moment voice authentication succeeds, or
it simply expires if no voice message follows in time.

Rate limit: max 3 attempts per 15 minutes per user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt

logger = logging.getLogger(__name__)

_RATE_WINDOW = timedelta(minutes=15)
_RATE_MAX = 3
_PIN_TIMEOUT = timedelta(seconds=60)
_PIN_VERIFIED_TTL = timedelta(minutes=5)


@dataclass
class _PinState:
    prompt_message_id: int
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.started_at + _PIN_TIMEOUT


@dataclass
class _RateEntry:
    attempts: int = 0
    window_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def reset_if_stale(self) -> None:
        if datetime.now(timezone.utc) >= self.window_start + _RATE_WINDOW:
            self.attempts = 0
            self.window_start = datetime.now(timezone.utc)


class PinAuth:
    def __init__(self, pin_hash_path: Path) -> None:
        self._path = pin_hash_path
        self._pending: dict[int, _PinState] = {}
        self._rate: dict[int, _RateEntry] = {}
        self._pin_verified: dict[int, datetime] = {}

    # ── Hash helpers ──────────────────────────────────────────────────────────

    def hash_exists(self) -> bool:
        return self._path.exists() and self._path.stat().st_size > 0

    def verify_pin(self, pin: str) -> bool:
        if not self.hash_exists():
            return False
        stored = self._path.read_bytes()
        return bcrypt.checkpw(pin.encode(), stored)

    # ── State machine ─────────────────────────────────────────────────────────

    def start(self, user_id: int, prompt_message_id: int) -> None:
        self._pending[user_id] = _PinState(prompt_message_id=prompt_message_id)

    def cancel(self, user_id: int) -> Optional[int]:
        """Cancel pending PIN entry. Returns prompt_message_id for deletion."""
        state = self._pending.pop(user_id, None)
        return state.prompt_message_id if state else None

    def is_waiting(self, user_id: int) -> bool:
        state = self._pending.get(user_id)
        if state and state.expired:
            self._pending.pop(user_id, None)
            return False
        return state is not None

    def is_timed_out(self, user_id: int) -> bool:
        state = self._pending.get(user_id)
        return state is not None and state.expired

    def get_prompt_message_id(self, user_id: int) -> Optional[int]:
        state = self._pending.get(user_id)
        return state.prompt_message_id if state else None

    def consume(self, user_id: int) -> Optional[int]:
        """Remove the pending state. Returns prompt_message_id."""
        state = self._pending.pop(user_id, None)
        return state.prompt_message_id if state else None

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def is_rate_limited(self, user_id: int) -> bool:
        entry = self._rate.setdefault(user_id, _RateEntry())
        entry.reset_if_stale()
        return entry.attempts >= _RATE_MAX

    def record_attempt(self, user_id: int) -> None:
        entry = self._rate.setdefault(user_id, _RateEntry())
        entry.reset_if_stale()
        entry.attempts += 1

    def reset_rate(self, user_id: int) -> None:
        self._rate.pop(user_id, None)

    # ── Timeout check (called by session monitor) ─────────────────────────────

    def timed_out_users(self) -> list[tuple[int, int]]:
        """Returns list of (user_id, prompt_message_id) for expired PIN sessions."""
        result = []
        for uid, state in list(self._pending.items()):
            if state.expired:
                result.append((uid, state.prompt_message_id))
                del self._pending[uid]
        return result

    # ── PIN-verified state (first factor, waiting on voice) ─────────────────────

    def mark_pin_verified(self, user_id: int) -> None:
        self._pin_verified[user_id] = datetime.now(timezone.utc) + _PIN_VERIFIED_TTL

    def has_pin_verified(self, user_id: int) -> bool:
        expires = self._pin_verified.get(user_id)
        if expires is None:
            return False
        if datetime.now(timezone.utc) >= expires:
            self._pin_verified.pop(user_id, None)
            return False
        return True

    def consume_pin_verified(self, user_id: int) -> None:
        self._pin_verified.pop(user_id, None)
