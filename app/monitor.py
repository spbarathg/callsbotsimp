import asyncio
import logging
from typing import List

from telethon import TelegramClient, events

from .config import Settings
from .parser import extract_solana_addresses
from .tracker import HotTracker


class Monitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TelegramClient(settings.session, settings.api_id, settings.api_hash)
        self.tracker_fast = HotTracker(settings.fast_ttl_seconds)
        self.tracker_slow = HotTracker(settings.hot_ttl_seconds)

    async def start(self) -> None:
        logging.info("🚀 Starting Telegram client…")
        await self.client.start()
        logging.info("✅ Client started. Monitoring groups…")

        self.client.add_event_handler(self._on_message, events.NewMessage(chats=self.settings.monitored_groups))
        await self.client.run_until_disconnected()

    def _signal_message_fast(self, ca: str) -> str:
        return f"⚡ Fast signal {self.settings.hot_threshold}x — {ca}"

    def _signal_message_slow(self, ca: str) -> str:
        return f"🔥 Signal {self.settings.hot_threshold}x — {ca}"

    async def _on_message(self, event) -> None:
        try:
            chat = await event.get_chat()
            group_name = getattr(chat, "title", "Unknown Group")
            group_id = getattr(chat, "id", 0)

            if event.is_reply or event.fwd_from:
                logging.info(f"↩️  Skip (reply/forward) in {group_name}")
                return

            text = event.raw_text or ""

            # Include entity text (e.g., copyable CA blocks)
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

                # Update both trackers (per-group dedupe already handled here)
                ug_fast = self.tracker_fast.add_hit(ca, group_id)
                ug_slow = self.tracker_slow.add_hit(ca, group_id)
                logging.info(f"👀 {group_name} → {ca} (fast {ug_fast}/{self.settings.hot_threshold}, slow {ug_slow}/{self.settings.hot_threshold})")

                # Fast tier
                if self.tracker_fast.should_alert(ca, self.settings.hot_threshold):
                    msg = self._signal_message_fast(ca)
                    logging.info(msg)
                    await self.client.send_message(self.settings.target_group, msg)
                    logging.info("📣 Fast signal sent")

                # Slow tier
                if self.tracker_slow.should_alert(ca, self.settings.hot_threshold):
                    msg = self._signal_message_slow(ca)
                    logging.info(msg)
                    await self.client.send_message(self.settings.target_group, msg)
                    logging.info("📣 Signal sent")

        except Exception as exc:
            logging.exception(f"Handler error: {exc}")
            await asyncio.sleep(0)
