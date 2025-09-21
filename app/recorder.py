import asyncio
import aiosqlite
from typing import Any, Dict, Optional


class SignalRecorder:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ca TEXT NOT NULL,
                        group_id INTEGER NOT NULL,
                        group_name TEXT,
                        kind TEXT NOT NULL, -- fast|slow
                        unique_groups_fast INTEGER,
                        unique_groups_slow INTEGER,
                        hot_threshold INTEGER,
                        sent_message_id INTEGER,
                        extra JSON
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rugcheck (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ca TEXT NOT NULL,
                        score TEXT,
                        risk_text TEXT,
                        lp_text TEXT,
                        upd_short TEXT
                    )
                    """
                )
                await db.commit()
            self._initialized = True

    async def record_signal(
        self,
        *,
        ts: int,
        ca: str,
        group_id: int,
        group_name: Optional[str],
        kind: str,
        ug_fast: Optional[int],
        ug_slow: Optional[int],
        hot_threshold: int,
        sent_message_id: Optional[int],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO signals (ts, ca, group_id, group_name, kind,
                                     unique_groups_fast, unique_groups_slow,
                                     hot_threshold, sent_message_id, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, json(?))
                """,
                (
                    ts,
                    ca,
                    group_id,
                    group_name,
                    kind,
                    ug_fast,
                    ug_slow,
                    hot_threshold,
                    sent_message_id,
                    (None if extra is None else __import__("json").dumps(extra)),
                ),
            )
            await db.commit()

    async def record_rugcheck(self, *, ts: int, ca: str, score: str, risk_text: str, lp_text: str, upd_short: str) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO rugcheck (ts, ca, score, risk_text, lp_text, upd_short)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, ca, score, risk_text, lp_text, upd_short),
            )
            await db.commit()


