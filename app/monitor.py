import asyncio
import logging
import os
from typing import List

from telethon import TelegramClient, events

import time
from .config import Settings
from .parser import extract_solana_addresses
from .tracker import HotTracker
from .rugcheck import RugcheckClient
from .recorder import SignalRecorder
from .events import SignalEvent, RugcheckEvent, MentionEvent, TradeIntentEvent, OnchainEvent
from .onchain import OnchainAnalyzer

# Import executor bridge for auto-trading
try:
    from exec.monitor_integration import ExecutorBridge
    EXECUTOR_AVAILABLE = True
except ImportError:
    EXECUTOR_AVAILABLE = False
    logging.warning("🤖 Executor not available - auto-trading disabled")


class Monitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Store session file under data directory
        self.client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)
        self.tracker_fast = HotTracker(settings.fast_ttl_seconds)
        self.tracker_slow = HotTracker(settings.hot_ttl_seconds)
        self.rugcheck = RugcheckClient(settings.rc_timeout_ms)
        self.recorder = SignalRecorder(settings.db_path)
        self.onchain = OnchainAnalyzer(settings.rpc_url, getattr(settings, "rpc_timeout_ms", 800))
        
        # Initialize executor bridge for auto-trading with explicit env guard
        self.executor_bridge = None
        if EXECUTOR_AVAILABLE and os.getenv("REDIS_URL"):
            try:
                self.executor_bridge = ExecutorBridge()
                logging.info("Auto-trading bridge initialized")
            except Exception as e:
                self.executor_bridge = None
                logging.warning(f"Auto-trading disabled: {e}")
        else:
            logging.info("Auto-trading disabled: REDIS_URL not set or executor unavailable")
        # In-process queues so recording never blocks Telegram handler
        self._signal_queue: asyncio.Queue[SignalEvent] = asyncio.Queue()
        self._rc_queue: asyncio.Queue[RugcheckEvent] = asyncio.Queue()
        self._mention_queue: asyncio.Queue[MentionEvent] = asyncio.Queue()
        self._intent_queue: asyncio.Queue[TradeIntentEvent] = asyncio.Queue()
        self._onchain_queue: asyncio.Queue[OnchainEvent] = asyncio.Queue()
        # Coalesce duplicate fast/slow alerts for the same CA within this window (seconds)
        self._coalesce_seconds: int = getattr(settings, "alert_coalesce_seconds", 3600)
        self._any_alerted_at: dict[str, float] = {}

    async def start(self) -> None:
        logging.info("🚀 Starting Telegram client…")
        await self.client.start()
        logging.info("✅ Client started. Monitoring groups…")

        self.client.add_event_handler(self._on_message, events.NewMessage(chats=self.settings.monitored_groups))
        # Background consumers for persistence
        consumers = [
            asyncio.create_task(self._consume_signals()),
            asyncio.create_task(self._consume_rugcheck()),
            asyncio.create_task(self._consume_mentions()),
            asyncio.create_task(self._consume_intents()),
            asyncio.create_task(self._consume_onchain()),
            asyncio.create_task(self._maintenance_task()),
        ]
        try:
            await self.client.run_until_disconnected()
        finally:
            for c in consumers:
                c.cancel()
            await self.rugcheck.close()
            try:
                await self.onchain.close()
            except Exception:
                pass

    async def _maintenance_task(self) -> None:
        try:
            while True:
                await asyncio.sleep(300)
                self.tracker_fast.clear_expired()
                self.tracker_slow.clear_expired()
                # prune coalescing map
                now = time.time()
                for ca, ts in list(self._any_alerted_at.items()):
                    if now - ts > max(self._coalesce_seconds, self.settings.hot_ttl_seconds):
                        self._any_alerted_at.pop(ca, None)
        except asyncio.CancelledError:
            return

    def _signal_message_fast(self, ca: str) -> str:
        return f"⚡ {self.settings.hot_threshold}x — {ca}"

    def _signal_message_slow(self, ca: str) -> str:
        return f"🔥 {self.settings.hot_threshold}x — {ca}"

    async def _append_rc_and_edit(self, message, ca: str) -> None:
        try:
            # Try a few times in case Rugcheck is rate-limiting (429)
            attempts = 0
            score = risk_text = lp_text = upd_short = "pending"
            while attempts < 4:
                attempts += 1
                try:
                    report = await self.rugcheck.fetch_report(ca)
                    score, risk_text, lp_text, upd_short = RugcheckClient.summarize(report or {})
                    # If we got a concrete score (not pending), stop retrying
                    if score != "pending":
                        break
                except Exception:
                    pass
                # Back off a bit longer on each retry to respect rate limits
                await asyncio.sleep(0.7 * attempts)

            # score is normalized to 0–10 string; display as /10
            rc_tail = f"RC: score {score}/10 | risks: {risk_text} | LP {lp_text} | updAuth {upd_short}"
            await message.edit(f"{message.raw_text} | {rc_tail}")
            # Record summarized RC
            await self._rc_queue.put(
                RugcheckEvent(
                    ts=int(time.time()),
                    ca=ca,
                    score=score,
                    risk_text=risk_text,
                    lp_text=lp_text,
                    upd_short=upd_short,
                )
            )
            # Kick off on-chain analysis (does not block)
            asyncio.create_task(self._append_onchain_and_record(message, ca))
        except Exception:
            pass

    async def _append_onchain_and_record(self, message, ca: str) -> None:
        try:
            analysis = await self.onchain.analyze(ca)
            if not analysis:
                return
            top1 = round(analysis["top1_pct"], 1)
            top10 = round(analysis["top10_pct"], 1)
            holders = analysis["holders_sampled"]
            oc_tail = f"Holders: top1 {top1}% | top10 {top10}% (n={holders})"
            try:
                await message.edit(f"{message.raw_text} | {oc_tail}")
            except Exception:
                pass
            await self._onchain_queue.put(
                OnchainEvent(
                    ts=int(time.time()),
                    ca=ca,
                    supply_total=float(analysis["supply_total"]),
                    decimals=int(analysis["decimals"]),
                    top1_pct=float(analysis["top1_pct"]),
                    top10_pct=float(analysis["top10_pct"]),
                    holders_sampled=int(analysis["holders_sampled"]),
                )
            )
        except Exception:
            pass

    async def _send_signal_and_snapshot(self, *, kind: str, ca: str, ug_fast: int, ug_slow: int, tracker: HotTracker, group_id: int, group_name: str | None) -> None:
        if kind == "fast":
            msg_text = self._signal_message_fast(ca)
        else:
            msg_text = self._signal_message_slow(ca)
        sent = await self.client.send_message(self.settings.target_group, msg_text)
        logging.info("📣 %s signal sent" % ("Fast" if kind == "fast" else "Signal"))
        await self._signal_queue.put(
            SignalEvent(
                ts=int(time.time()),
                ca=ca,
                group_id=group_id,
                group_name=group_name,
                kind=kind,
                ug_fast=ug_fast,
                ug_slow=ug_slow,
                hot_threshold=self.settings.hot_threshold,
                sent_message_id=getattr(sent, "id", None),
                extra=None,
            )
        )
        asyncio.create_task(self._append_rc_and_edit(sent, ca))
        vel = tracker.get_velocity_mpm(ca)
        first_ts, last_ts = tracker.get_first_last_seen(ca)
        await self._intent_queue.put(
            TradeIntentEvent(
                ts=int(time.time()),
                ca=ca,
                kind=kind,
                ug_fast=ug_fast,
                ug_slow=ug_slow,
                velocity_mpm=vel,
                first_seen_ts=first_ts,
                last_seen_ts=last_ts,
                rc_score="pending",
                rc_risk_text="pending",
                rc_lp_text="pending",
                rc_upd_short="pending",
            )
        )
        # Mark as alerted for coalescing
        self._any_alerted_at[ca] = time.time()

    async def _consume_signals(self) -> None:
        while True:
            ev = await self._signal_queue.get()
            try:
                await self.recorder.record_signal(
                    ts=ev.ts,
                    ca=ev.ca,
                    group_id=ev.group_id,
                    group_name=ev.group_name,
                    kind=ev.kind,
                    ug_fast=ev.ug_fast,
                    ug_slow=ev.ug_slow,
                    hot_threshold=ev.hot_threshold,
                    sent_message_id=ev.sent_message_id,
                    extra=ev.extra,
                )
            except Exception as exc:
                logging.exception(f"record_signal failed: {exc}")
            finally:
                self._signal_queue.task_done()

    async def _consume_rugcheck(self) -> None:
        while True:
            ev = await self._rc_queue.get()
            try:
                await self.recorder.record_rugcheck(
                    ts=ev.ts,
                    ca=ev.ca,
                    score=ev.score,
                    risk_text=ev.risk_text,
                    lp_text=ev.lp_text,
                    upd_short=ev.upd_short,
                )
            except Exception as exc:
                logging.exception(f"record_rugcheck failed: {exc}")
            finally:
                self._rc_queue.task_done()

    async def _consume_onchain(self) -> None:
        while True:
            ev = await self._onchain_queue.get()
            try:
                await self.recorder.record_onchain(
                    ts=ev.ts,
                    ca=ev.ca,
                    supply_total=ev.supply_total,
                    decimals=ev.decimals,
                    top1_pct=ev.top1_pct,
                    top10_pct=ev.top10_pct,
                    holders_sampled=ev.holders_sampled,
                )
            except Exception as exc:
                logging.exception(f"record_onchain failed: {exc}")
            finally:
                self._onchain_queue.task_done()

    async def _consume_mentions(self) -> None:
        while True:
            ev = await self._mention_queue.get()
            try:
                await self.recorder.record_mention(
                    ts=ev.ts,
                    ca=ev.ca,
                    group_id=ev.group_id,
                    group_name=ev.group_name,
                    message_id=ev.message_id,
                )
            except Exception as exc:
                logging.exception(f"record_mention failed: {exc}")
            finally:
                self._mention_queue.task_done()

    async def _consume_intents(self) -> None:
        while True:
            ev = await self._intent_queue.get()
            try:
                # Record trade intent as before
                await self.recorder.record_trade_intent(
                    ts=ev.ts,
                    ca=ev.ca,
                    kind=ev.kind,
                    ug_fast=ev.ug_fast,
                    ug_slow=ev.ug_slow,
                    velocity_mpm=ev.velocity_mpm,
                    first_seen_ts=ev.first_seen_ts,
                    last_seen_ts=ev.last_seen_ts,
                    rc_score=ev.rc_score,
                    rc_risk_text=ev.rc_risk_text,
                    rc_lp_text=ev.rc_lp_text,
                    rc_upd_short=ev.rc_upd_short,
                )
                
                # NEW: Send to executor for auto-trading
                if self.executor_bridge:
                    await self.executor_bridge.send_trade_intent(ev)
                    
            except Exception as exc:
                logging.exception(f"record_trade_intent failed: {exc}")
            finally:
                self._intent_queue.task_done()

    async def _on_message(self, event) -> None:
        try:
            chat = await event.get_chat()
            group_name = getattr(chat, "title", "Unknown Group")
            group_id = getattr(chat, "id", 0)

            if event.is_reply or event.fwd_from:
                logging.info(f"↩️  Skip (reply/forward) in {group_name}")
                return

            text = event.raw_text or ""

            # Redundant entity re-appending removed; raw_text already contains entity ranges

            addresses: List[str] = extract_solana_addresses(text)
            if not addresses:
                return

            seen = set()
            for ca in addresses:
                if ca in seen:
                    continue
                seen.add(ca)

                ug_fast = self.tracker_fast.add_hit(ca, group_id)
                ug_slow = self.tracker_slow.add_hit(ca, group_id)
                # persist mention for auto-trade analytics
                await self._mention_queue.put(
                    MentionEvent(
                        ts=int(time.time()),
                        ca=ca,
                        group_id=group_id,
                        group_name=group_name,
                        message_id=getattr(event.message, "id", None),
                    )
                )
                logging.info(f"👀 {group_name} → {ca} (fast {ug_fast}/{self.settings.hot_threshold}, slow {ug_slow}/{self.settings.hot_threshold})")

                if self.tracker_fast.should_alert(ca, self.settings.hot_threshold):
                    await self._send_signal_and_snapshot(
                        kind="fast",
                        ca=ca,
                        ug_fast=ug_fast,
                        ug_slow=ug_slow,
                        tracker=self.tracker_fast,
                        group_id=group_id,
                        group_name=group_name,
                    )

                # Suppress slow alert if a fast/slow alert for same CA was sent recently
                last_any = self._any_alerted_at.get(ca)
                coalesce_ok = True
                if last_any is not None and time.time() - last_any < self._coalesce_seconds:
                    coalesce_ok = False
                if coalesce_ok and self.tracker_slow.should_alert(ca, self.settings.hot_threshold):
                    await self._send_signal_and_snapshot(
                        kind="slow",
                        ca=ca,
                        ug_fast=ug_fast,
                        ug_slow=ug_slow,
                        tracker=self.tracker_slow,
                        group_id=group_id,
                        group_name=group_name,
                    )

        except Exception as exc:
            logging.exception(f"Handler error: {exc}")
            await asyncio.sleep(0)
