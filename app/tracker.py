import time
from typing import Dict, Set, Tuple


class HotTracker:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        # ca -> (group_ids_set, last_updated)
        self._by_ca: Dict[str, Tuple[Set[int], float]] = {}
        # ca -> alerted flag timestamp
        self._alerted_at: Dict[str, float] = {}
        # ca -> (first_seen_ts, last_seen_ts, count)
        self._meta: Dict[str, Tuple[float, float, int]] = {}

    def _expired(self, ts: float, now: float) -> bool:
        return now - ts > self.ttl_seconds

    def add_hit(self, ca: str, group_id: int) -> int:
        now = time.time()
        groups, ts = self._by_ca.get(ca, (set(), 0.0))
        if self._expired(ts, now):
            groups = set()
        groups.add(group_id)
        self._by_ca[ca] = (groups, now)
        first, last, cnt = self._meta.get(ca, (now, 0.0, 0))
        if self._expired(last or now, now):
            first, cnt = now, 0
        self._meta[ca] = (first, now, cnt + 1)
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
        # Expired meta
        for ca, (_, last, _) in list(self._meta.items()):
            if self._expired(last, now):
                self._meta.pop(ca, None)

    def get_velocity_mpm(self, ca: str) -> float:
        first, last, cnt = self._meta.get(ca, (0.0, 0.0, 0))
        if cnt < 2 or last <= first:
            return 0.0
        minutes = (last - first) / 60.0
        return cnt / minutes if minutes > 0 else 0.0

    def get_first_last_seen(self, ca: str) -> Tuple[int | None, int | None]:
        first, last, _ = self._meta.get(ca, (0.0, 0.0, 0))
        return (int(first) if first else None, int(last) if last else None)