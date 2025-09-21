import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum


class PositionStatus(Enum):
    ACTIVE = "active"
    STOPPED = "stopped"
    COMPLETED = "completed"


class ExitReason(Enum):
    STOP_LOSS = "stop_loss"
    TIME_STOP = "time_stop"
    TRAILING_STOP = "trailing_stop"
    PROFIT_TAKE = "profit_take"
    MANUAL = "manual"
    RUG_DETECTED = "rug_detected"


@dataclass
class Position:
    ca: str
    entry_price: float  # Stored in USD/token
    entry_time: float
    size_usd: float
    size_tokens: float
    entry_signature: str
    
    # Rugcheck data for risk adjustment
    rugcheck_score: str = "pending"
    rugcheck_risks: str = "pending"
    rugcheck_lp_locked: bool = False
    
    # Position tracking
    remaining_tokens: float = 0.0
    realized_pnl: float = 0.0
    status: PositionStatus = PositionStatus.ACTIVE
    
    # Exit management
    trailing_stop: float = 0.0
    stop_loss_price: float = 0.0
    
    # "Capture the Runner" strategy flags
    is_derisked: bool = False          # Has the 33% been sold at 3x?
    derisked_at_price: float = 0.0     # Price when de-risked
    derisked_amount: float = 0.0       # Amount sold to de-risk
    runner_tokens: float = 0.0         # Remaining "risk-free" tokens
    runner_peak_price: float = 0.0     # Highest price for trailing stop
    
    # Performance tracking
    peak_price: float = 0.0
    peak_multiple: float = 1.0
    last_price_check: float = 0.0

    # Tiered profit-taking tracking
    tiers_hit: set[int] = field(default_factory=set)
    last_partial_sell_time: float = 0.0

    # Token metadata
    token_decimals: int = 9
    
    def __post_init__(self):
        if self.remaining_tokens == 0.0:
            self.remaining_tokens = self.size_tokens
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price
        if self.last_price_check == 0.0:
            self.last_price_check = time.time()


@dataclass
class TradeResult:
    ca: str
    entry_time: float
    exit_time: float
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: ExitReason
    duration_minutes: float
    peak_multiple: float


@dataclass
class PortfolioStats:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    daily_realized_pnl: float = 0.0
    consecutive_losses: int = 0
    active_positions: int = 0
    total_exposure_usd: float = 0.0
    last_reset_time: float = field(default_factory=time.time)
    trading_halted_until: float = 0.0
    
    @property
    def win_rate(self) -> float:
        return self.winning_trades / max(1, self.total_trades)
    
    @property 
    def is_trading_halted(self) -> bool:
        return time.time() < self.trading_halted_until
    
    def reset_daily_stats(self):
        """Reset daily counters at midnight or after long breaks"""
        self.daily_realized_pnl = 0.0
        self.consecutive_losses = 0
        self.last_reset_time = time.time()
    
    def should_reset_daily(self) -> bool:
        """Check if we should reset daily stats (new day or long gap)"""
        hours_since_reset = (time.time() - self.last_reset_time) / 3600
        return hours_since_reset >= 24  # Reset after 24 hours


@dataclass 
class SignalData:
    """Fast signal data structure from monitor"""
    ca: str
    timestamp: float
    kind: str  # "fast" | "slow"
    ug_fast: int
    ug_slow: int
    velocity_mpm: float
    first_seen_ts: Optional[int]
    rugcheck_score: str
    rugcheck_risks: str
    rugcheck_lp: str
    
    # Computed quality score
    quality_score: float = 0.0
