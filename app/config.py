import os
from dataclasses import dataclass
from typing import List


ALLOWED_GROUPS: List[str] = [
    '@MooDengPresidentCallers',
    '@Bot_NovaX',
    '@Ranma_Calls_Solana',
    '@MarksGems',
    '@Alphakollswithins',
    '@mattprintalphacalls',
    '@ReVoX_Academy',
    '@pfultimate',
    '@pumpfunvolumeby4AM',
    '@SouthParkCall',
    '@batman_gem',
    '@wifechangingcallss',
    '@SAVANNAHCALLS',
]


def _parse_groups(value: str) -> List[str]:
    """Parse MONITORED_GROUPS env and restrict to ALLOWED_GROUPS.

    If env is empty, default to ALLOWED_GROUPS.
    """
    if not value:
        return list(ALLOWED_GROUPS)
    parsed = [g.strip() for g in value.split(",") if g.strip()]
    # Restrict to allowed set while preserving order of parsed
    allowed_set = set(ALLOWED_GROUPS)
    filtered = [g for g in parsed if g in allowed_set]
    # If user provided none of the allowed, fall back to default allowed
    return filtered or list(ALLOWED_GROUPS)


@dataclass
class Settings:
    api_id: int
    api_hash: str
    session: str
    session_path: str
    data_dir: str
    target_group: str
    hot_threshold: int
    hot_ttl_seconds: int
    fast_ttl_seconds: int
    rc_timeout_ms: int
    monitored_groups: List[str]
    db_path: str
    alert_coalesce_seconds: int
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    rpc_timeout_ms: int = 800


def load_settings() -> Settings:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "")
    session = os.getenv("SESSION", "memecoin_session")
    data_dir = os.getenv("DATA_DIR", "data")
    session_path = os.path.join(data_dir, session)
    db_path = os.path.join(data_dir, os.getenv("DB_FILE", "signals.db"))
    target_group = os.getenv("TARGET_GROUP", "@callbotmemecoin")
    hot_threshold = int(os.getenv("HOT_THRESHOLD", "4"))
    hot_ttl_seconds = int(os.getenv("HOT_TTL_SECONDS", "43200"))  # 12h
    fast_ttl_seconds = int(os.getenv("FAST_TTL_SECONDS", "1800"))  # 30m
    rc_timeout_ms = int(os.getenv("RC_TIMEOUT_MS", "400"))
    alert_coalesce_seconds = int(os.getenv("ALERT_COALESCE_SECONDS", "3600"))
    rpc_url = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
    rpc_timeout_ms = int(os.getenv("RPC_TIMEOUT_MS", "800"))
    monitored_groups = _parse_groups(os.getenv("MONITORED_GROUPS", ""))

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        session=session,
        session_path=session_path,
        data_dir=data_dir,
        target_group=target_group,
        hot_threshold=hot_threshold,
        hot_ttl_seconds=hot_ttl_seconds,
        fast_ttl_seconds=fast_ttl_seconds,
        rc_timeout_ms=rc_timeout_ms,
        monitored_groups=monitored_groups,
        db_path=db_path,
        alert_coalesce_seconds=alert_coalesce_seconds,
        rpc_url=rpc_url,
        rpc_timeout_ms=rpc_timeout_ms,
    )
