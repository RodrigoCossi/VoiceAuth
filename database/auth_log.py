"""SQLite authentication attempt logger."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    telegram_user_id    INTEGER NOT NULL,
    telegram_username   TEXT,
    chat_id             INTEGER NOT NULL,
    similarity_score    REAL,
    result              TEXT NOT NULL,
    audio_hash          TEXT
);
"""


class AuthLogger:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def log(
        self,
        *,
        telegram_user_id: int,
        telegram_username: Optional[str],
        chat_id: int,
        result: str,
        similarity_score: Optional[float] = None,
        audio_hash: Optional[str] = None,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            async with aiosqlite.connect(self._path) as db:
                await db.execute(
                    """INSERT INTO auth_log
                       (timestamp, telegram_user_id, telegram_username, chat_id,
                        similarity_score, result, audio_hash)
                       VALUES (?,?,?,?,?,?,?)""",
                    (ts, telegram_user_id, telegram_username, chat_id,
                     similarity_score, result, audio_hash),
                )
                await db.commit()

    async def recent(self, limit: int = 10) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM auth_log ORDER BY id DESC LIMIT ?", (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def failed_last_24h(self, user_id: int) -> int:
        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM auth_log WHERE telegram_user_id=? AND result='FAIL' AND timestamp>=?",
                (user_id, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else 0
