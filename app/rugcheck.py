import asyncio
import sys
from typing import Optional, Tuple
import aiohttp
import json as _json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Fix for Windows aiodns issue
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class RugcheckClient:
    def __init__(self, timeout_ms: int) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000.0)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "accept": "application/json",
                    "user-agent": "callsbotsimp/1.0 (+local)"
                },
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_report(self, mint: str) -> Optional[dict]:
        try:
            session = await self._get_session()
            raw = (mint or "").strip()
            candidates = [raw]
            if raw.endswith("pump"):
                # Try stripped version as fallback
                candidates.append(raw[:-4])
            elif len(raw) == 44:  # Might be a base mint that needs "pump" suffix
                candidates.append(raw + "pump")

            for candidate in candidates:
                url = f"https://api.rugcheck.xyz/v1/tokens/{candidate}/report"
                # Retry a few times for transient limits/outages
                attempts = 0
                while attempts < 3:
                    attempts += 1
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 200:
                                # Some servers return content-type variations; be lenient.
                                try:
                                    return await resp.json(content_type=None)
                                except Exception:
                                    text = await resp.text()
                                    try:
                                        return _json.loads(text)
                                    except Exception:
                                        return None
                            # Backoff for transient errors and 429
                            if resp.status in (429, 500, 502, 503, 504):
                                await asyncio.sleep(0.4 * attempts)
                                continue
                            # Other 4xx: give up on this candidate
                            break
                    except asyncio.TimeoutError:
                        await asyncio.sleep(0.2 * attempts)
                        continue
                    except Exception:
                        # Unknown error: try next attempt/candidate
                        await asyncio.sleep(0.2 * attempts)
                        continue
                # Fallback: try a simple synchronous GET via urllib in a thread
                try:
                    def _sync_get_json(u: str):
                        req = Request(u, headers={
                            "accept": "application/json",
                            "user-agent": "callsbotsimp/1.0 (+local)"
                        })
                        with urlopen(req, timeout=max(1, int(self.timeout.total))) as r:  # type: ignore[attr-defined]
                            data = r.read()
                            try:
                                return _json.loads(data.decode("utf-8", "ignore"))
                            except Exception:
                                return None
                    j = await asyncio.to_thread(_sync_get_json, url)
                    if isinstance(j, dict):
                        return j
                except (URLError, HTTPError, Exception):
                    pass
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