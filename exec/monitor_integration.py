"""
Integration bridge to connect existing monitor system to executor
Add this to your existing app/monitor.py to enable auto-trading
"""

import asyncio
import logging
import time
from typing import Optional

# Import from the executor module
from exec.queue_bridge import FastSignalQueue, SignalProcessor
from exec.models import SignalData


class ExecutorBridge:
    """Bridge to connect monitor signals to executor"""
    
    def __init__(self, signal_queue_path: str = "data/executor_queue.json"):
        self.signal_queue = FastSignalQueue(signal_queue_path)
        self.signal_processor = SignalProcessor(self.signal_queue)
        self.enabled = True
    
    async def send_trade_intent(self, trade_intent_event) -> None:
        """Convert TradeIntentEvent to executor signal"""
        
        if not self.enabled:
            return
            
        try:
            # Convert your TradeIntentEvent to SignalData format
            trade_intent_data = {
                'ca': trade_intent_event.ca,
                'kind': trade_intent_event.kind,
                'ug_fast': trade_intent_event.ug_fast,
                'ug_slow': trade_intent_event.ug_slow,
                'velocity_mpm': trade_intent_event.velocity_mpm,
                'first_seen_ts': trade_intent_event.first_seen_ts,
                'rc_score': trade_intent_event.rc_score,
                'rc_risk_text': trade_intent_event.rc_risk_text,
                'rc_lp_text': trade_intent_event.rc_lp_text,
            }
            
            # Process and queue the signal
            await self.signal_processor.process_trade_intent(trade_intent_data)
            
        except Exception as e:
            logging.error(f"Failed to send trade intent to executor: {e}")
    
    def enable_trading(self):
        """Enable auto-trading"""
        self.enabled = True
        logging.info("🤖 Auto-trading ENABLED")
    
    def disable_trading(self):
        """Disable auto-trading"""
        self.enabled = False
        logging.info("🛑 Auto-trading DISABLED")


# INTEGRATION INSTRUCTIONS:
# 
# 1. Add this to your app/monitor.py imports:
#    from exec.monitor_integration import ExecutorBridge
#
# 2. Add this to your Monitor.__init__():
#    self.executor_bridge = ExecutorBridge()
#
# 3. Modify your _consume_intents() method to include:
#    await self.executor_bridge.send_trade_intent(ev)
#
# Here's the exact modification for your _consume_intents method:

"""
async def _consume_intents(self) -> None:
    while True:
        ev = await self._intent_queue.get()
        try:
            # Your existing recording
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
            await self.executor_bridge.send_trade_intent(ev)
            
        except Exception as exc:
            logging.exception(f"record_trade_intent failed: {exc}")
        finally:
            self._intent_queue.task_done()
"""
