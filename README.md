## Solana Social Signal → Auto-Trade Bot (End-to-End)

This system watches Telegram groups for Solana token mentions, filters/validates them, and automatically trades promising tokens using a disciplined "Capture the Runner" strategy. It’s designed to be reliable, low-latency, and cost-efficient.

### What it does
- Monitors selected Telegram groups for potential token mints (Solana CAs)
- Parses and validates addresses; ignores noise and duplicates
- Tracks unique-group mentions over time (fast: 30m, slow: 12h)
- When mentions cross a threshold, emits a signal and records metadata
- Bridges signals to an executor that buys with a small fixed size (default $10)
- Manages exits asymmetrically: de-risk at 3x, then 30% trailing stop on the runner
- Enforces portfolio risk limits (daily loss limit, position limits, circuit breakers)

### Architecture overview
```
Telegram → app/parser.py → app/tracker.py → app/monitor.py
  → TradeIntentEvent → exec/monitor_integration.py → exec/queue_bridge.py (JSON queue)
  → exec/executor.py → exec/jupiter_client.py + exec/wallet.py → trades
```

### Quick start
1) Install Python 3.10+
2) Create and fill `.env` in repo root (both monitor and executor use it)
3) Install dependencies
```
pip install -r requirements.txt         # app deps
pip install -r exec/requirements.txt    # executor deps
```
4) Run the two processes (separate terminals)
```
python -m app               # starts Telegram monitor
python exec/run_executor.py # starts auto-executor
```

### .env (example)
```
# Telegram monitor
API_ID=123456
API_HASH=your_api_hash
SESSION=memecoin_session
TARGET_GROUP=@callbotmemecoin
HOT_THRESHOLD=4
HOT_TTL_SECONDS=43200
FAST_TTL_SECONDS=1800
MONITORED_GROUPS=@group1,@group2
RC_TIMEOUT_MS=400
DATA_DIR=data
DB_FILE=signals.db

# Executor
EXECUTOR_PRIVATE_KEY=base58_private_key
EXECUTOR_RPC_URL=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
EXECUTOR_BACKUP_RPC_URL=https://api.mainnet-beta.solana.com
JUPITER_API_URL=https://quote-api.jup.ag/v6

# Risk & performance
BASE_POSITION_SIZE_USD=10.0
MAX_SLIPPAGE_BPS=150
MAX_IMPACT_BPS=250
STOP_LOSS_BASE_PCT=0.50
DAILY_LOSS_LIMIT_PCT=0.04
CONSECUTIVE_LOSS_LIMIT=4
DISASTER_STOP_PCT=0.80
TIME_STOP_MINUTES=60
TIME_STOP_PROFIT_TARGET_PCT=0.50
DERISKING_MULTIPLE=3.0
DERISKING_SELL_PCT=0.33
RUNNER_TRAILING_STOP_PCT=0.30
PRICE_CHECK_INTERVAL_MS=5000
MAX_CONCURRENT_POSITIONS=8
SIGNAL_QUEUE_PATH=data/executor_queue.json
```

### Key design choices (why it’s fast and cheap)
- File-based queue for handoff (no broker): near-zero cost, sub-second latency
- Separation of concerns keeps each part simple and resilient
- Rugcheck and quality scoring leverage data you already have from social signals
- All hot-path logic is async and lightweight

### Safety and risk management
- Daily loss and streak circuit breakers
- De-risking at 3x recovers initial stake
- Runner sold with trailing stop to capture big moves while limiting giveback
- Hard disaster stop (-80%) backstop

### Troubleshooting
- No signals showing up: verify `MONITORED_GROUPS` and Telegram creds
- Executor not trading: check `data/executor_queue.json`, wallet balance, and logs
- Quote errors: ensure Jupiter URL works; token may lack liquidity

### License
MIT
