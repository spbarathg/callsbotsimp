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



@dataclass
class MentionEvent:
    ts: int
    ca: str
    group_id: int
    group_name: Optional[str]
    message_id: Optional[int]


@dataclass
class TradeIntentEvent:
    ts: int
    ca: str
    kind: str  # "fast" | "slow"
    ug_fast: Optional[int]
    ug_slow: Optional[int]
    velocity_mpm: float
    first_seen_ts: Optional[int]
    last_seen_ts: Optional[int]
    rc_score: str
    rc_risk_text: str
    rc_lp_text: str
    rc_upd_short: str


@dataclass
class OnchainEvent:
    ts: int
    ca: str
    supply_total: float
    decimals: int
    top1_pct: float
    top10_pct: float
    holders_sampled: int