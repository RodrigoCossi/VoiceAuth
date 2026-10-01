"""Session lifecycle management.

Sessions are held in memory only — they don't survive a process restart,
which is intentional (restart forces re-authentication).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Session:
    user_id: int
    chat_id: int
    started_at: datetime
    expires_at: datetime
    auth_method: str  # always 'pin+voice' — both factors are required to reach here
    warning_sent: bool = False


class SessionManager:
    def __init__(self, config) -> None:
        self._cfg = config
        self._sessions: dict[int, Session] = {}

    def create(self, user_id: int, chat_id: int, auth_method: str) -> Session:
        timeout = timedelta(minutes=self._cfg.session_timeout_minutes)
        session = Session(
            user_id=user_id,
            chat_id=chat_id,
            started_at=_now(),
            expires_at=_now() + timeout,
            auth_method=auth_method,
        )
        self._sessions[user_id] = session
        return session

    def get(self, user_id: int) -> Optional[Session]:
        session = self._sessions.get(user_id)
        if session and _now() >= session.expires_at:
            del self._sessions[user_id]
            return None
        return session

    def is_active(self, user_id: int) -> bool:
        return self.get(user_id) is not None

    def extend(self, user_id: int) -> None:
        session = self.get(user_id)
        if session:
            session.expires_at = _now() + timedelta(minutes=self._cfg.session_timeout_minutes)
            session.warning_sent = False

    def end(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)

    def time_remaining(self, user_id: int) -> Optional[timedelta]:
        session = self.get(user_id)
        if not session:
            return None
        remaining = session.expires_at - _now()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)

    def needs_warning(self, user_id: int) -> bool:
        """True if the session is within the warning window and warning not yet sent."""
        session = self.get(user_id)
        if not session or session.warning_sent:
            return False
        warning_at = session.expires_at - timedelta(minutes=self._cfg.session_warning_minutes)
        return _now() >= warning_at

    def mark_warning_sent(self, user_id: int) -> None:
        session = self.get(user_id)
        if session:
            session.warning_sent = True

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())
