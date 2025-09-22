import os
import threading
import time
from typing import Optional

from prometheus_client import Histogram, Counter, start_http_server


_server_started = False
_server_lock = threading.Lock()


def start_metrics_server() -> None:
    global _server_started
    if _server_started:
        return
    with _server_lock:
        if _server_started:
            return
        port = int(os.getenv("METRICS_PORT", "9109"))
        try:
            start_http_server(port)
            _server_started = True
        except Exception:
            # Best-effort; metrics are optional in dev
            _server_started = True


HOT_PATH_MS = Histogram(
    "executor_hot_path_ms",
    "End-to-end latency from signal to tx submit (ms)",
    buckets=(5, 10, 20, 50, 75, 100, 150, 200, 300, 500, 1000),
)

QUOTE_MS = Histogram(
    "executor_quote_latency_ms",
    "Latency from quote request to response (ms)",
    buckets=(5, 10, 20, 50, 75, 100, 150, 200, 300, 500, 1000),
)

SIGN_MS = Histogram(
    "executor_sign_latency_ms",
    "Latency to sign the transaction (ms)",
    buckets=(1, 2, 5, 10, 20, 50, 75, 100, 150, 200),
)

SUBMIT_MS = Histogram(
    "executor_submit_latency_ms",
    "Latency from sign to RPC submit (ms)",
    buckets=(1, 2, 5, 10, 20, 50, 75, 100, 150, 200),
)

CONFIRM_MS = Histogram(
    "executor_confirm_latency_ms",
    "Latency from submit to confirmation (ms)",
    buckets=(100, 200, 300, 500, 800, 1200, 2000, 3000, 5000, 10000),
)

ORDERS_ABORTED_LATENCY = Counter(
    "executor_orders_aborted_latency_total",
    "Orders aborted due to hot-path latency > threshold",
)

ORDERS_STARTED = Counter("executor_orders_started_total", "Orders started")
ORDERS_CONFIRMED = Counter("executor_orders_confirmed_total", "Orders confirmed")
ORDERS_FAILED = Counter("executor_orders_failed_total", "Orders failed")
BOTS_TRADES_TOTAL = Counter("executor_trades_total", "Total trades completed (terminal state)")
BOTS_TRADES_WON = Counter("executor_trades_won_total", "Winning trades (pnl>0)")

BUNDLE_SUCCESS = Counter("executor_bundle_success_total", "Successful Jito bundle sends")
BUNDLE_FALLBACK = Counter("executor_bundle_fallback_total", "Fallbacks to normal submit after bundle failure")


class LatencyTracker:
    """High-resolution latency tracker for a single order."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self.signal_received_ts = self._now()
        self.quote_requested_ts: Optional[float] = None
        self.quote_received_ts: Optional[float] = None
        self.tx_signed_ts: Optional[float] = None
        self.tx_submitted_ts: Optional[float] = None
        self.tx_confirmed_ts: Optional[float] = None

    @staticmethod
    def _now() -> float:
        return time.perf_counter()

    def mark_quote_requested(self) -> None:
        self.quote_requested_ts = self._now()

    def mark_quote_received(self) -> None:
        self.quote_received_ts = self._now()
        if self.quote_requested_ts is not None:
            QUOTE_MS.observe((self.quote_received_ts - self.quote_requested_ts) * 1000.0)

    def mark_signed(self) -> None:
        self.tx_signed_ts = self._now()
        if self.quote_received_ts is not None:
            SIGN_MS.observe((self.tx_signed_ts - self.quote_received_ts) * 1000.0)

    def mark_submitted(self) -> None:
        self.tx_submitted_ts = self._now()
        if self.tx_signed_ts is not None:
            SUBMIT_MS.observe((self.tx_submitted_ts - self.tx_signed_ts) * 1000.0)
        HOT_PATH_MS.observe((self.tx_submitted_ts - self.signal_received_ts) * 1000.0)

    def mark_confirmed(self) -> None:
        self.tx_confirmed_ts = self._now()
        if self.tx_submitted_ts is not None:
            CONFIRM_MS.observe((self.tx_confirmed_ts - self.tx_submitted_ts) * 1000.0)

    def hot_path_ms_so_far(self) -> float:
        return (self._now() - self.signal_received_ts) * 1000.0


