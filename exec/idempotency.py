import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Optional


class IdempotencyStore:
    """SQLite-backed idempotency and state store.

    Guarantees we process a signal_id at most once. Safe for single-writer process.
    """

    def __init__(self, db_path: str = "data/executor_state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._lock = asyncio.Lock()

    def _init_db(self) -> None:
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_signals (
                    signal_id TEXT PRIMARY KEY,
                    processed_at REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS order_transitions (
                    signal_id TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    ts REAL NOT NULL,
                    PRIMARY KEY (signal_id, state)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS exits (
                    signal_id TEXT NOT NULL,
                    mint TEXT NOT NULL,
                    pct REAL NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    signal_id TEXT PRIMARY KEY,
                    mint TEXT NOT NULL,
                    entry_signature TEXT,
                    entry_time REAL,
                    size_usd REAL,
                    size_tokens REAL,
                    token_decimals INTEGER,
                    entry_price REAL,
                    status TEXT
                )
                """
            )
            con.commit()
        finally:
            con.close()

    async def has_processed(self, signal_id: str) -> bool:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute("SELECT 1 FROM processed_signals WHERE signal_id=?", (signal_id,))
                row = cur.fetchone()
                return row is not None
            finally:
                con.close()

    async def mark_processed(self, signal_id: str) -> None:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute(
                    "INSERT OR IGNORE INTO processed_signals(signal_id, processed_at) VALUES (?, ?)",
                    (signal_id, time.time()),
                )
                con.commit()
            finally:
                con.close()

    async def record_transition(self, signal_id: str, mint: str, state: str) -> None:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO order_transitions(signal_id, mint, state, ts) VALUES (?, ?, ?, ?)",
                    (signal_id, mint, state, time.time()),
                )
                con.commit()
            finally:
                con.close()

    async def last_state(self, signal_id: str) -> Optional[str]:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute(
                    "SELECT state FROM order_transitions WHERE signal_id=? ORDER BY ts DESC LIMIT 1",
                    (signal_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
            finally:
                con.close()

    async def record_exit(self, signal_id: str, mint: str, pct: float) -> None:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute(
                    "INSERT INTO exits(signal_id, mint, pct, ts) VALUES (?, ?, ?, ?)",
                    (signal_id, mint, pct, time.time()),
                )
                con.commit()
            finally:
                con.close()

    async def get_executed_exit_pct(self, signal_id: str) -> float:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute("SELECT COALESCE(SUM(pct),0) FROM exits WHERE signal_id=?", (signal_id,))
                row = cur.fetchone()
                return float(row[0] or 0.0)
            finally:
                con.close()

    async def upsert_position(
        self,
        signal_id: str,
        mint: str,
        entry_signature: str | None,
        entry_time: float | None,
        size_usd: float | None,
        size_tokens: float | None,
        token_decimals: int | None,
        entry_price: float | None,
        status: str | None,
    ) -> None:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            try:
                cur = con.cursor()
                cur.execute(
                    """
                    INSERT INTO positions(signal_id, mint, entry_signature, entry_time, size_usd, size_tokens, token_decimals, entry_price, status)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(signal_id) DO UPDATE SET
                        mint=excluded.mint,
                        entry_signature=COALESCE(excluded.entry_signature, positions.entry_signature),
                        entry_time=COALESCE(excluded.entry_time, positions.entry_time),
                        size_usd=COALESCE(excluded.size_usd, positions.size_usd),
                        size_tokens=COALESCE(excluded.size_tokens, positions.size_tokens),
                        token_decimals=COALESCE(excluded.token_decimals, positions.token_decimals),
                        entry_price=COALESCE(excluded.entry_price, positions.entry_price),
                        status=COALESCE(excluded.status, positions.status)
                    """,
                    (
                        signal_id,
                        mint,
                        entry_signature,
                        entry_time,
                        size_usd,
                        size_tokens,
                        token_decimals,
                        entry_price,
                        status,
                    ),
                )
                con.commit()
            finally:
                con.close()

    async def load_positions_by_status(self, status: str) -> list[dict]:
        async with self._lock:
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            try:
                cur = con.cursor()
                cur.execute("SELECT * FROM positions WHERE status=?", (status,))
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                con.close()


