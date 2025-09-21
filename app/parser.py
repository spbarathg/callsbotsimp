import re
from typing import List, Set
import base58

# Base58 without 0,O,I,l and length 32-48 chars (allow pump-style endings)
SOLANA_CA_PATTERN = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,48})\b")

# Common noise words to ignore (symbols, tickers, keywords)
_NOISE: Set[str] = {
    "CA", "Ca", "CA:", "Ca:", "SOL", "Sol", "PUMP", "pump",
    "BUY", "buy", "Chart", "chart", "Link", "link",
}

_INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff\u00a0"


def _strip_invisible(token: str) -> str:
    return token.translate({ord(c): None for c in _INVISIBLE})


def _is_valid_solana(addr: str) -> bool:
    try:
        raw = base58.b58decode(addr)
    except Exception:
        return False
    return len(raw) == 32


def _normalize_candidate(token: str) -> str:
    t = _strip_invisible(token.strip())
    # Accept pump-style mints as-is (Rugcheck and PF support these)
    if t.endswith("pump"):
        return t
    # Standard 32-byte mint as-is
    if _is_valid_solana(t):
        return t
    return ""


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
        norm = _normalize_candidate(token)
        if norm:
            addresses.append(norm)

    # Deduplicate preserving order
    seen: Set[str] = set()
    unique: List[str] = []
    for a in addresses:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique
