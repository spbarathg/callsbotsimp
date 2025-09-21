import re
from typing import List, Set
import base58

# Base58 without 0,O,I,l and length 32-44 chars (Solana pubkey range)
SOLANA_CA_PATTERN = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})(?:\b|$)")

# Common noise words to ignore (symbols, tickers, keywords)
_NOISE: Set[str] = {
    "CA", "Ca", "CA:", "Ca:", "SOL", "Sol", "PUMP", "pump",
    "BUY", "buy", "Chart", "chart", "Link", "link",
}

_SUFFIXES: List[str] = ["pump", "\u200bpump"]


def _normalize(token: str) -> str:
    t = token.strip()
    for suf in _SUFFIXES:
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def _is_valid_solana(addr: str) -> bool:
    try:
        raw = base58.b58decode(addr)
    except Exception:
        return False
    # Solana public key is 32 bytes
    return len(raw) == 32


def extract_solana_addresses(text: str) -> List[str]:
    if not text:
        return []

    matches = SOLANA_CA_PATTERN.findall(text)

    addresses: List[str] = []
    for token in matches:
        if token in _NOISE:
            continue
        if token.startswith("0x"):
            continue
        norm = _normalize(token)
        if _is_valid_solana(norm):
            addresses.append(norm)

    # Deduplicate preserving order
    seen: Set[str] = set()
    unique: List[str] = []
    for a in addresses:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique
