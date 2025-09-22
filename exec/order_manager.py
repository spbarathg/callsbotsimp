import asyncio
import enum
import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis

from .metrics import LatencyTracker
from .models import SignalData


class OrderState(enum.Enum):
    PENDING = "pending"
    QUOTED = "quoted"
    SIGNED = "signed"
    BUNDLED_SUBMITTED = "bundled_submitted"
    CONFIRMED = "confirmed"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass
class OrderRecord:
    signal_id: str
    ca: str
    state: str
    created_at: float
    updated_at: float


class OrderManager:
    """Finite-state machine with Redis locks and minimal persistence hooks.

    Full SQLite persistence of transitions can be added by integrating with the idempotency DB.
    """

    def __init__(self, redis_url: Optional[str]):
        self._redis = aioredis.from_url(redis_url, decode_responses=True) if redis_url else None

    async def acquire_lock(self, key: str, ttl_ms: int = 30000) -> bool:
        if not self._redis:
            return True
        return await self._redis.set(name=f"lock:{key}", value="1", nx=True, px=ttl_ms) is True

    async def release_lock(self, key: str) -> None:
        if not self._redis:
            return
        try:
            await self._redis.delete(f"lock:{key}")
        except Exception:
            pass


