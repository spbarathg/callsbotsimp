import asyncio
from typing import Optional, Tuple
import aiohttp


class RugcheckClient:
    def __init__(self, timeout_ms: int) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000.0)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_report(self, mint: str) -> Optional[dict]:
        try:
            session = await self._get_session()
            url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    @staticmethod
    def summarize(report: dict) -> Tuple[str, str, str, str]:
        if not report:
            return ("pending", "pending", "pending", "pending")
        score = report.get("score")
        risks = report.get("risks") or []
        risk_names = [r.get("name", "?") for r in risks][:2]
        risk_text = "none" if not risk_names else ",".join(risk_names)
        lp_pct = None
        try:
            markets = report.get("markets") or []
            if markets:
                lp_pct = markets[0].get("lp", {}).get("lpLockedPct")
        except Exception:
            lp_pct = None
        lp_text = f"{round(lp_pct)}%" if isinstance(lp_pct, (int, float)) else "n/a"
        upd = (report.get("tokenMeta") or {}).get("updateAuthority")
        upd_short = f"{upd[:4]}…{upd[-4:]}" if isinstance(upd, str) and len(upd) > 8 else (upd or "n/a")
        return (str(score) if score is not None else "n/a", risk_text, lp_text, upd_short)