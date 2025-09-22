import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutorSettings:
    # Wallet & RPC
    private_key: str
    rpc_url: str
    backup_rpc_url: Optional[str]
    
    # Jupiter/Jito
    jupiter_api_url: str
    jito_bundle_url: Optional[str]
    jito_tip_lamports: int
    
    # Risk Management
    base_position_size_usd: float
    max_slippage_bps: int
    max_impact_bps: int
    stop_loss_base_pct: float
    daily_loss_limit_pct: float
    consecutive_loss_limit: int
    
    # No entry gates needed - monitor system provides excellent signal quality
    
    # Position Management - "Capture the Runner" Strategy
    disaster_stop_pct: float          # -80% hard stop
    time_stop_minutes: int            # 60 minutes if not +50%
    time_stop_profit_target_pct: float # Need +50% to avoid time stop
    derisking_multiple: float         # 3x to de-risk position
    derisking_sell_pct: float         # Sell 33% at 3x
    runner_trailing_stop_pct: float   # 30% trailing stop on runner
    
    # Extended scaling-out configuration (never-ending profit taking)
    # CSV of multiple:sell_pct, e.g. "5:0.10,8:0.10,13:0.10"
    profit_tiers_csv: str
    # CSV of multiple:trail_pct used to ratchet trailing stop
    trailing_zones_csv: str
    # Keep this fraction as permanent runner (never sell via tiers)
    min_runner_pct: float
    # Cooldown between partial sells in seconds
    partial_sell_cooldown_sec: int
    
    # Performance
    price_check_interval_ms: int
    max_concurrent_positions: int
    
    # Shared queue path for legacy file signals (fallback)
    signal_queue_path: str
    
    # Redis queue configuration (preferred)
    redis_url: Optional[str]
    redis_stream_key: str
    redis_consumer_group: str
    redis_consumer_name: str


def load_executor_settings() -> ExecutorSettings:
    return ExecutorSettings(
        # Wallet & RPC
        private_key=os.getenv("EXECUTOR_PRIVATE_KEY", ""),
        rpc_url=os.getenv("EXECUTOR_RPC_URL", "https://mainnet.helius-rpc.com"),
        backup_rpc_url=os.getenv("EXECUTOR_BACKUP_RPC_URL"),
        
        # Jupiter/Jito  
        jupiter_api_url=os.getenv("JUPITER_API_URL", "https://quote-api.jup.ag/v6"),
        jito_bundle_url=os.getenv("JITO_BUNDLE_URL"),
        jito_tip_lamports=int(os.getenv("JITO_TIP_LAMPORTS", "10000")),
        
        # Risk Management
        base_position_size_usd=float(os.getenv("BASE_POSITION_SIZE_USD", "10.0")),
        max_slippage_bps=int(os.getenv("MAX_SLIPPAGE_BPS", "150")),  # 1.5%
        max_impact_bps=int(os.getenv("MAX_IMPACT_BPS", "250")),     # 2.5%
        stop_loss_base_pct=float(os.getenv("STOP_LOSS_BASE_PCT", "0.50")),  # 50% base stop
        daily_loss_limit_pct=float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.04")),  # 4%
        consecutive_loss_limit=int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "4")),
        
        # "Capture the Runner" Strategy
        disaster_stop_pct=float(os.getenv("DISASTER_STOP_PCT", "0.80")),  # -80%
        time_stop_minutes=int(os.getenv("TIME_STOP_MINUTES", "60")),      # 60 minutes
        time_stop_profit_target_pct=float(os.getenv("TIME_STOP_PROFIT_TARGET_PCT", "0.50")),  # +50%
        derisking_multiple=float(os.getenv("DERISKING_MULTIPLE", "3.0")),  # 3x
        derisking_sell_pct=float(os.getenv("DERISKING_SELL_PCT", "0.33")),  # 33%
        runner_trailing_stop_pct=float(os.getenv("RUNNER_TRAILING_STOP_PCT", "0.30")),  # 30%
        
        # Extended scaling-out config (defaults designed for 3000x+ capability)
        profit_tiers_csv=os.getenv(
            "PROFIT_TIERS_CSV",
            "5:0.10,8:0.10,13:0.10,21:0.10,34:0.10,55:0.10,89:0.10,144:0.10,233:0.10,377:0.10,610:0.10,987:0.10,1597:0.10"
        ),
        trailing_zones_csv=os.getenv(
            "TRAILING_ZONES_CSV",
            "0:0.30,5:0.25,10:0.22,20:0.20,50:0.15,100:0.12,500:0.10,3000:0.08"
        ),
        min_runner_pct=float(os.getenv("MIN_RUNNER_PCT", "0.07")),  # Keep at least 7%
        partial_sell_cooldown_sec=int(os.getenv("PARTIAL_SELL_COOLDOWN_SEC", "180")),  # 3 minutes
        
        # Performance
        price_check_interval_ms=int(os.getenv("PRICE_CHECK_INTERVAL_MS", "5000")),  # 5s
        max_concurrent_positions=int(os.getenv("MAX_CONCURRENT_POSITIONS", "8")),
        
        # Shared queue
        signal_queue_path=os.getenv("SIGNAL_QUEUE_PATH", "data/executor_queue.json"),
        
        # Redis queue
        redis_url=os.getenv("REDIS_URL"),
        redis_stream_key=os.getenv("REDIS_STREAM_KEY", "exec_signals"),
        redis_consumer_group=os.getenv("REDIS_CONSUMER_GROUP", "executor"),
        redis_consumer_name=os.getenv("REDIS_CONSUMER_NAME", "worker-1"),
    )
