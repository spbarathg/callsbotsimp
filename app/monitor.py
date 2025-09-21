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
        self.tracker = HotTracker(settings.hot_ttl_seconds)

    async def start(self) -> None:
        logging.info("🚀 Starting Telegram client…")
        await self.client.start()
        logging.info("✅ Client started. Monitoring groups…")

        self.client.add_event_handler(self._on_message, events.NewMessage(chats=self.settings.monitored_groups))
        await self.client.run_until_disconnected()

    def _signal_message(self, ca: str) -> str:
        return f"🔥 {self.settings.hot_threshold}x group signal — {ca}"

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
                unique_groups = self.tracker.add_hit(ca, group_id)
                logging.info(f"👀 {group_name} → {ca} ({unique_groups}/{self.settings.hot_threshold})")
                if self.tracker.should_alert(ca, self.settings.hot_threshold):
                    msg = self._signal_message(ca)
                    # Log the same clean message and send it
                    logging.info(msg)
                    await self.client.send_message(self.settings.target_group, msg)
                    logging.info("📣 Signal sent")

        except Exception as exc:
            logging.exception(f"Handler error: {exc}")
            await asyncio.sleep(0)
