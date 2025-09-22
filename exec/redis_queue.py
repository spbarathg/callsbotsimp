import asyncio
import json
import logging
import time
from typing import List, Optional, Tuple

import redis.asyncio as aioredis

from .models import SignalData


class RedisSignalQueue:
    """Redis Streams-backed signal queue compatible with FastSignalQueue API.

    Methods used by the codebase:
      - read_new() -> list[(msg_id, SignalData)]
      - ack(msg_id: str) -> None
      - cleanup_old_signals(max_age_hours: float) -> None

    Producer-side put is not used by the executor directly, but is handy for tests/integration.
    """

    def __init__(self, redis_url: str, stream_key: str, consumer_group: str, consumer: str):
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self.stream_key = stream_key
        self.consumer_group = consumer_group
        self.consumer = consumer
        self._ensure_group_lock = asyncio.Lock()

    async def _ensure_group(self) -> None:
        """Create the stream and consumer group if they do not exist."""
        async with self._ensure_group_lock:
            try:
                # Create stream with an empty entry if it does not exist
                exists = await self._redis.exists(self.stream_key)
                if not exists:
                    try:
                        await self._redis.xadd(self.stream_key, {"init": "1"})
                    except Exception:
                        pass
                # Create consumer group if missing
                try:
                    await self._redis.xgroup_create(name=self.stream_key, groupname=self.consumer_group, id="$", mkstream=True)
                except Exception as e:
                    # BUSYGROUP means it already exists
                    if "BUSYGROUP" not in str(e):
                        logging.debug(f"xgroup_create error (safe to ignore if exists): {e}")
            except Exception as e:
                logging.debug(f"Ensure group failed: {e}")

    async def put_signal(self, signal: SignalData) -> Optional[str]:
        """Producer helper: add a signal to the stream. Returns message id."""
        try:
            await self._ensure_group()
            payload = {
                "ca": signal.ca,
                "timestamp": str(signal.timestamp),
                "kind": signal.kind,
                "ug_fast": str(signal.ug_fast),
                "ug_slow": str(signal.ug_slow),
                "velocity_mpm": str(signal.velocity_mpm),
                "first_seen_ts": str(signal.first_seen_ts or ""),
                "rugcheck_score": signal.rugcheck_score,
                "rugcheck_risks": signal.rugcheck_risks,
                "rugcheck_lp": signal.rugcheck_lp,
                "quality_score": str(getattr(signal, "quality_score", 0.0) or 0.0),
                "signal_id": getattr(signal, "signal_id", "") or "",
            }
            msg_id = await self._redis.xadd(self.stream_key, payload)
            return msg_id
        except Exception as e:
            logging.error(f"Redis put_signal failed: {e}")
            return None

    async def read_new(self, count: int = 64, block_ms: int = 50) -> List[Tuple[str, SignalData]]:
        """Read new messages for this consumer group.

        Returns list of (msg_id, SignalData). Uses XREADGROUP starting from '>' (new only).
        """
        await self._ensure_group()
        try:
            streams = {self.stream_key: ">"}
            resp = await self._redis.xreadgroup(groupname=self.consumer_group, consumername=self.consumer, streams=streams, count=count, block=block_ms)
            results: List[Tuple[str, SignalData]] = []
            if not resp:
                return results
            # resp format: [(stream_key, [(msg_id, {field: value, ...}), ...])]
            for _stream, messages in resp:
                for msg_id, fields in messages:
                    try:
                        signal = SignalData(
                            ca=fields.get("ca"),
                            timestamp=float(fields.get("timestamp", 0) or 0),
                            kind=fields.get("kind", "fast"),
                            ug_fast=int(fields.get("ug_fast", 0) or 0),
                            ug_slow=int(fields.get("ug_slow", 0) or 0),
                            velocity_mpm=float(fields.get("velocity_mpm", 0) or 0.0),
                            first_seen_ts=int(fields.get("first_seen_ts", 0) or 0) or None,
                            rugcheck_score=fields.get("rugcheck_score", "pending"),
                            rugcheck_risks=fields.get("rugcheck_risks", "pending"),
                            rugcheck_lp=fields.get("rugcheck_lp", ""),
                        )
                        # Optional fields
                        try:
                            signal.quality_score = float(fields.get("quality_score", 0.0) or 0.0)
                        except Exception:
                            pass
                        sid = fields.get("signal_id")
                        if sid:
                            signal.signal_id = sid
                        results.append((msg_id, signal))
                    except Exception as e:
                        logging.error(f"Failed to parse signal from Redis: {e}")
            return results
        except Exception as e:
            logging.error(f"Redis read_new failed: {e}")
            return []

    async def ack(self, msg_id: str) -> None:
        try:
            await self._redis.xack(self.stream_key, self.consumer_group, msg_id)
            # Optionally, we can trim delivered messages to limit stream size
        except Exception as e:
            logging.debug(f"Redis ack failed: {e}")

    async def cleanup_old_signals(self, max_age_hours: float = 24.0) -> None:
        """Trim messages older than max_age_hours.

        We use approximate trimming by length to keep stream size bounded. A precise
        time-based trim would require scanning; we keep it lightweight.
        """
        try:
            # Heuristic: keep last N messages based on average expected volume
            # For safety, keep a generous window (e.g., 5000 messages)
            await self._redis.xtrim(self.stream_key, maxlen=5000, approximate=True)
        except Exception as e:
            logging.debug(f"Redis cleanup failed: {e}")




    async def close(self) -> None:
        """Close underlying Redis connection explicitly to avoid loop-finalizer warnings."""
        try:
            await self._redis.aclose()
        except Exception:
            pass
