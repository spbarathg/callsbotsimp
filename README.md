## Simple Solana CA Watcher (Telethon)

Watches 30+ Telegram groups for Solana contract addresses (CA) and alerts in a target channel when a CA is mentioned N times within a time window. Fast, modular, and Windows-friendly.

### Setup
1. Install Python 3.10+.
2. Create `.env` from the example below.
3. Install deps: `pip install -r requirements.txt`.
4. Run: `python -m app`.

### .env
```
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
```

- `MONITORED_GROUPS` is a comma-separated list.
- `HOT_TTL_SECONDS` is how long we remember a CA hit (prevents stale counts).

### Notes
- Only Solana Base58 addresses (32–48 chars, excluding `0`, `O`, `I`, `l`), with support for pump-style mints (`...pump`).
- Skips replies and forwarded messages to reduce noise.
- Dedupes per-message and per-group bursts.

### Run on Windows
```
py -m app
```

### License
MIT
