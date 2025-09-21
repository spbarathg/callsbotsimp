import os
from dataclasses import dataclass
from typing import List


def _parse_groups(value: str) -> List[str]:
    if not value:
        return []
    return [g.strip() for g in value.split(",") if g.strip()]


@dataclass
class Settings:
    api_id: int
    api_hash: str
    session: str
    target_group: str
    hot_threshold: int
    hot_ttl_seconds: int
    fast_ttl_seconds: int
    monitored_groups: List[str]


def load_settings() -> Settings:
    api_id = int(os.getenv("API_ID", "0"))
    api_hash = os.getenv("API_HASH", "")
    session = os.getenv("SESSION", "memecoin_session")
    target_group = os.getenv("TARGET_GROUP", "@callbotmemecoin")
    hot_threshold = int(os.getenv("HOT_THRESHOLD", "4"))
    hot_ttl_seconds = int(os.getenv("HOT_TTL_SECONDS", "43200"))  # 12h
    fast_ttl_seconds = int(os.getenv("FAST_TTL_SECONDS", "1800"))  # 30m
    monitored_groups = _parse_groups(os.getenv("MONITORED_GROUPS", ""))

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        session=session,
        target_group=target_group,
        hot_threshold=hot_threshold,
        hot_ttl_seconds=hot_ttl_seconds,
        fast_ttl_seconds=fast_ttl_seconds,
        monitored_groups=monitored_groups,
    )
