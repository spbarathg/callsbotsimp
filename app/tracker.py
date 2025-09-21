import time
from typing import Dict, Set, Tuple


class HotTracker:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        # ca -> (group_ids_set, last_updated)
        self._by_ca: Dict[str, Tuple[Set[int], float]] = {}
        # ca -> alerted flag timestamp
        self._alerted_at: Dict[str, float] = {}

    def _expired(self, ts: float, now: float) -> bool:
        return now - ts > self.ttl_seconds

    def add_hit(self, ca: str, group_id: int) -> int:
        now = time.time()
        groups, ts = self._by_ca.get(ca, (set(), 0.0))
        if self._expired(ts, now):
            groups = set()
        groups.add(group_id)
        self._by_ca[ca] = (groups, now)
        return len(groups)

    def should_alert(self, ca: str, threshold: int) -> bool:
        now = time.time()
        groups, ts = self._by_ca.get(ca, (set(), 0.0))
        if self._expired(ts, now):
            return False
        if len(groups) < threshold:
            return False
        alerted_ts = self._alerted_at.get(ca)
        if alerted_ts and not self._expired(alerted_ts, now):
            return False
        self._alerted_at[ca] = now
        return True

    def clear_expired(self) -> None:
        now = time.time()
        # Expired counts
        for ca, (_, ts) in list(self._by_ca.items()):
            if self._expired(ts, now):
                self._by_ca.pop(ca, None)
        # Expired alert markers
        for ca, ts in list(self._alerted_at.items()):
            if self._expired(ts, now):
                self._alerted_at.pop(ca, None)