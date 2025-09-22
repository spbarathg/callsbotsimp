import asyncio
import math
from typing import Optional, Tuple, Dict, Any

import aiohttp


class OnchainAnalyzer:
    """Lightweight on-chain analyzer using Solana JSON-RPC.

    Fetches token supply and largest accounts to estimate holder concentration.
    Designed to be best-effort, fast, and non-blocking.
    """

    def __init__(self, rpc_url: str, timeout_ms: int = 800) -> None:
        self.rpc_url = rpc_url
        self.timeout = aiohttp.ClientTimeout(total=max(0.2, timeout_ms / 1000.0))
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(
                        timeout=self.timeout,
                        headers={
                            "content-type": "application/json",
                            "accept": "application/json",
                            "user-agent": "callsbotsimp/1.0 (+local)"
                        },
                    )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _rpc(self, method: str, params: list[Any]) -> Optional[dict]:
        try:
            session = await self._get()
            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            async with session.post(self.rpc_url, json=payload) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)
        except Exception:
            return None

    @staticmethod
    def _to_float(amount_str: str, decimals: int) -> float:
        try:
            raw = int(amount_str)
            return raw / float(10 ** max(0, decimals))
        except Exception:
            return 0.0

    async def fetch_token_supply(self, mint: str) -> Tuple[float, int]:
        """Return (total_supply, decimals) in human units."""
        res = await self._rpc("getTokenSupply", [mint])
        try:
            val = (res or {}).get("result", {}).get("value", {})
            amount = val.get("amount")
            decimals = int(val.get("decimals", 0))
            if isinstance(amount, str):
                return (self._to_float(amount, decimals), decimals)
        except Exception:
            pass
        return (0.0, 0)

    async def fetch_top_concentration(self, mint: str, decimals: int) -> Tuple[float, float, int]:
        """Return (top1_pct, top10_pct, holders_sampled)."""
        res = await self._rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        try:
            values = ((res or {}).get("result", {}) or {}).get("value", [])
            if not isinstance(values, list) or not values:
                return (0.0, 0.0, 0)
            amounts = []
            for v in values:
                amt = v.get("amount")
                if isinstance(amt, str):
                    try:
                        amounts.append(self._to_float(amt, decimals))
                    except Exception:
                        continue
            if not amounts:
                return (0.0, 0.0, 0)
            holders_sampled = len(amounts)
            top1 = max(amounts) if amounts else 0.0
            top10 = sum(sorted(amounts, reverse=True)[: min(10, len(amounts))])
            return (top1, top10, holders_sampled)
        except Exception:
            return (0.0, 0.0, 0)

    async def analyze(self, mint: str) -> Optional[Dict[str, Any]]:
        """Best-effort combined analysis.

        Returns dict with: supply_total, decimals, top1_pct, top10_pct, holders_sampled.
        Percentages are relative to total supply.
        """
        try:
            supply_total, decimals = await self.fetch_token_supply(mint)
            if supply_total <= 0.0:
                return None
            top1_raw, top10_raw, holders_sampled = await self.fetch_top_concentration(mint, decimals)
            top1_pct = (top1_raw / supply_total) * 100.0 if supply_total > 0 else 0.0
            top10_pct = (top10_raw / supply_total) * 100.0 if supply_total > 0 else 0.0
            # Clamp for safety
            top1_pct = max(0.0, min(100.0, top1_pct))
            top10_pct = max(0.0, min(100.0, top10_pct))
            return {
                "supply_total": supply_total,
                "decimals": decimals,
                "top1_pct": top1_pct,
                "top10_pct": top10_pct,
                "holders_sampled": holders_sampled,
            }
        except Exception:
            return None


