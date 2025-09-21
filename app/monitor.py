import asyncio
import logging
from typing import List

from telethon import TelegramClient, events

from .config import Settings
from .parser import extract_solana_addresses
from .tracker import HotTracker
from .rugcheck import RugcheckClient


class Monitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Store session file under data directory
        self.client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)
        self.tracker_fast = HotTracker(settings.fast_ttl_seconds)
        self.tracker_slow = HotTracker(settings.hot_ttl_seconds)
        self.rugcheck = RugcheckClient(settings.rc_timeout_ms)

    async def start(self) -> None:
        logging.info("🚀 Starting Telegram client…")
        await self.client.start()
        logging.info("✅ Client started. Monitoring groups…")

        self.client.add_event_handler(self._on_message, events.NewMessage(chats=self.settings.monitored_groups))
        try:
            await self.client.run_until_disconnected()
        finally:
            await self.rugcheck.close()

    def _format_rc(self, ca: str, report: dict | None) -> str:
        if not report:
            return "RC: pending"
        score, risk_text, lp_text, upd_short = RugcheckClient.summarize(report)
        return f"RC: score {score} | risks: {risk_text} | LP {lp_text} | updAuth {upd_short}"

    def _signal_message_fast(self, ca: str, rc_tail: str | None = None) -> str:
        base = f"⚡ {self.settings.hot_threshold}x — {ca}"
        return f"{base} | {rc_tail}" if rc_tail else base

    def _signal_message_slow(self, ca: str, rc_tail: str | None = None) -> str:
        base = f"🔥 {self.settings.hot_threshold}x — {ca}"
        return f"{base} | {rc_tail}" if rc_tail else base

    async def _append_rc_and_edit(self, message, ca: str) -> None:
        try:
            report = await self.rugcheck.fetch_report(ca)
            rc_tail = self._format_rc(ca, report)
            await message.edit(f"{message.raw_text} | {rc_tail}")
        except Exception:
            pass

    async def _on_message(self, event) -> None:
        try:
            chat = await event.get_chat()
            group_name = getattr(chat, "title", "Unknown Group")
            group_id = getattr(chat, "id", 0)

            if event.is_reply or event.fwd_from:
                logging.info(f"↩️  Skip (reply/forward) in {group_name}")
                return

            text = event.raw_text or ""

            if event.message and event.message.entities:
                for ent in event.message.entities:
                    if hasattr(ent, "offset") and hasattr(ent, "length"):
                        part = text[ent.offset: ent.offset + ent.length]
                        if part and part not in text:
                            text += f" {part}"

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
                logging.info(f"👀 {group_name} → {ca} (fast {ug_fast}/{self.settings.hot_threshold}, slow {ug_slow}/{self.settings.hot_threshold})")

                if self.tracker_fast.should_alert(ca, self.settings.hot_threshold):
                    msg = self._signal_message_fast(ca)
                    sent = await self.client.send_message(self.settings.target_group, msg)
                    logging.info("📣 Fast signal sent")
                    asyncio.create_task(self._append_rc_and_edit(sent, ca))

                if self.tracker_slow.should_alert(ca, self.settings.hot_threshold):
                    msg = self._signal_message_slow(ca)
                    sent = await self.client.send_message(self.settings.target_group, msg)
                    logging.info("📣 Signal sent")
                    asyncio.create_task(self._append_rc_and_edit(sent, ca))

        except Exception as exc:
            logging.exception(f"Handler error: {exc}")
            await asyncio.sleep(0)
