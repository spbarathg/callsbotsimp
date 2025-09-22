import asyncio
import aiosqlite
import json
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
                # Performance and concurrency pragmas for WAL
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=NORMAL")
                await db.execute("PRAGMA temp_store=MEMORY")
                await db.execute("PRAGMA mmap_size=268435456")
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
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mentions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ca TEXT NOT NULL,
                        group_id INTEGER NOT NULL,
                        group_name TEXT,
                        message_id INTEGER
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trade_intents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ca TEXT NOT NULL,
                        kind TEXT NOT NULL, -- fast|slow
                        ug_fast INTEGER,
                        ug_slow INTEGER,
                        velocity_mpm REAL,
                        first_seen_ts INTEGER,
                        last_seen_ts INTEGER,
                        rc_score TEXT,
                        rc_risk_text TEXT,
                        rc_lp_text TEXT,
                        rc_upd_short TEXT
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS onchain (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        ca TEXT NOT NULL,
                        supply_total REAL,
                        decimals INTEGER,
                        top1_pct REAL,
                        top10_pct REAL,
                        holders_sampled INTEGER
                    )
                    """
                )
                # Indexes for efficiency
                await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_signals_ca ON signals(ca)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_mentions_ca_ts ON mentions(ca, ts)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_intents_ts ON trade_intents(ts)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_intents_ca ON trade_intents(ca)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_onchain_ca_ts ON onchain(ca, ts)")
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
                    (None if extra is None else json.dumps(extra)),
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

    async def record_mention(self, *, ts: int, ca: str, group_id: int, group_name: str | None, message_id: int | None) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO mentions (ts, ca, group_id, group_name, message_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, ca, group_id, group_name, message_id),
            )
            await db.commit()

    async def record_trade_intent(
        self,
        *,
        ts: int,
        ca: str,
        kind: str,
        ug_fast: int | None,
        ug_slow: int | None,
        velocity_mpm: float,
        first_seen_ts: int | None,
        last_seen_ts: int | None,
        rc_score: str,
        rc_risk_text: str,
        rc_lp_text: str,
        rc_upd_short: str,
    ) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO trade_intents (ts, ca, kind, ug_fast, ug_slow, velocity_mpm,
                                           first_seen_ts, last_seen_ts, rc_score, rc_risk_text, rc_lp_text, rc_upd_short)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ca,
                    kind,
                    ug_fast,
                    ug_slow,
                    velocity_mpm,
                    first_seen_ts,
                    last_seen_ts,
                    rc_score,
                    rc_risk_text,
                    rc_lp_text,
                    rc_upd_short,
                ),
            )
            await db.commit()

    async def record_onchain(
        self,
        *,
        ts: int,
        ca: str,
        supply_total: float,
        decimals: int,
        top1_pct: float,
        top10_pct: float,
        holders_sampled: int,
    ) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO onchain (ts, ca, supply_total, decimals, top1_pct, top10_pct, holders_sampled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ca,
                    supply_total,
                    decimals,
                    top1_pct,
                    top10_pct,
                    holders_sampled,
                ),
            )
            await db.commit()


