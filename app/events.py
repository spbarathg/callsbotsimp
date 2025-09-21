from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class SignalEvent:
    ts: int
    ca: str
    group_id: int
    group_name: Optional[str]
    kind: str  # "fast" | "slow"
    ug_fast: Optional[int]
    ug_slow: Optional[int]
    hot_threshold: int
    sent_message_id: Optional[int]
    extra: Optional[Dict[str, Any]] = None


@dataclass
class RugcheckEvent:
    ts: int
    ca: str
    score: str
    risk_text: str
    lp_text: str
    upd_short: str


