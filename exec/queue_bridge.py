import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional
import aiofiles
import aiofiles.os

from .models import SignalData


class FastSignalQueue:
    """Ultra-low latency queue bridge between monitor and executor"""
    
    def __init__(self, queue_file_path: str):
        self.queue_file = Path(queue_file_path)
        self.queue_file.parent.mkdir(exist_ok=True)
        self._ensure_queue_file()
        # In-process I/O lock to prevent lost updates on concurrent writes
        self._io_lock = asyncio.Lock()
        
    def _ensure_queue_file(self):
        """Create queue file if it doesn't exist"""
        if not self.queue_file.exists():
            with open(self.queue_file, 'w') as f:
                json.dump([], f)
    
    async def put_signal(self, signal: SignalData) -> None:
        """Add signal to queue (called by monitor)"""
        try:
            signal_dict = {
                'ca': signal.ca,
                'timestamp': signal.timestamp,
                'kind': signal.kind,
                'ug_fast': signal.ug_fast,
                'ug_slow': signal.ug_slow,
                'velocity_mpm': signal.velocity_mpm,
                'first_seen_ts': signal.first_seen_ts,
                'rugcheck_score': signal.rugcheck_score,
                'rugcheck_risks': signal.rugcheck_risks,
                'rugcheck_lp': signal.rugcheck_lp,
                'quality_score': signal.quality_score
            }
            
            async with self._io_lock:
                # Read current queue
                async with aiofiles.open(self.queue_file, 'r') as f:
                    content = await f.read()
                    queue = json.loads(content) if content.strip() else []
                
                # Add new signal
                queue.append(signal_dict)
                
                # Keep only last 100 signals to prevent file bloat
                if len(queue) > 100:
                    queue = queue[-100:]
                
                # Write back atomically using replace to avoid Windows rename semantics
                temp_file = self.queue_file.with_suffix('.tmp')
                async with aiofiles.open(temp_file, 'w') as f:
                    await f.write(json.dumps(queue, indent=2))
                
                # Prefer os.replace for atomic overwrite across platforms
                if hasattr(aiofiles.os, 'replace'):
                    await aiofiles.os.replace(temp_file, self.queue_file)
                else:
                    await aiofiles.os.rename(temp_file, self.queue_file)
            
        except Exception as e:
            logging.error(f"Failed to put signal in queue: {e}")
    
    async def get_new_signals(self, last_processed_timestamp: float = 0.0) -> list[SignalData]:
        """Get all signals newer than timestamp (called by executor)"""
        try:
            # Retry on transient JSON decode during atomic replace
            queue = []
            for attempt in range(3):
                try:
                    async with aiofiles.open(self.queue_file, 'r') as f:
                        content = await f.read()
                    queue = json.loads(content) if content.strip() else []
                    break
                except json.JSONDecodeError:
                    await asyncio.sleep(0.02 * (attempt + 1))
            
            # Filter for new signals
            new_signals = []
            for signal_dict in queue:
                if signal_dict['timestamp'] > last_processed_timestamp:
                    signal = SignalData(
                        ca=signal_dict['ca'],
                        timestamp=signal_dict['timestamp'],
                        kind=signal_dict['kind'],
                        ug_fast=signal_dict['ug_fast'],
                        ug_slow=signal_dict['ug_slow'],
                        velocity_mpm=signal_dict['velocity_mpm'],
                        first_seen_ts=signal_dict.get('first_seen_ts'),
                        rugcheck_score=signal_dict['rugcheck_score'],
                        rugcheck_risks=signal_dict['rugcheck_risks'],
                        rugcheck_lp=signal_dict['rugcheck_lp'],
                        quality_score=signal_dict.get('quality_score', 0.0)
                    )
                    new_signals.append(signal)
            
            return sorted(new_signals, key=lambda s: s.timestamp)
            
        except Exception as e:
            logging.error(f"Failed to get signals from queue: {e}")
            return []
    
    async def cleanup_old_signals(self, max_age_hours: float = 24.0) -> None:
        """Remove signals older than max_age_hours"""
        try:
            cutoff_time = time.time() - (max_age_hours * 3600)
            
            async with self._io_lock:
                async with aiofiles.open(self.queue_file, 'r') as f:
                    content = await f.read()
                    queue = json.loads(content) if content.strip() else []
                
                # Filter out old signals
                fresh_queue = [s for s in queue if s['timestamp'] > cutoff_time]
                
                if len(fresh_queue) != len(queue):
                    # Write cleaned queue
                    temp_file = self.queue_file.with_suffix('.tmp')
                    async with aiofiles.open(temp_file, 'w') as f:
                        await f.write(json.dumps(fresh_queue, indent=2))
                    
                    if hasattr(aiofiles.os, 'replace'):
                        await aiofiles.os.replace(temp_file, self.queue_file)
                    else:
                        await aiofiles.os.rename(temp_file, self.queue_file)
                    
                    logging.info(f"Cleaned {len(queue) - len(fresh_queue)} old signals from queue")
                
        except Exception as e:
            logging.error(f"Failed to cleanup old signals: {e}")


class SignalProcessor:
    """Processes signals from monitor and adds to executor queue"""
    
    def __init__(self, queue: FastSignalQueue):
        self.queue = queue
        
    def calculate_quality_score(self, signal: SignalData) -> float:
        """Quality scoring based on your existing signal data"""
        
        # Your system already has excellent signal quality!
        # The fact that it triggered fast/slow means it passed your thresholds
        
        # Base quality from signal strength
        base_quality = 0.6  # Already filtered by your hot threshold
        
        # Boost for multiple group mentions
        group_boost = min(0.2, (signal.ug_fast - 4) * 0.05)  # +5% per group above threshold
        
        # Boost for velocity
        velocity_boost = min(0.1, signal.velocity_mpm / 10.0)  # Up to 10% for high velocity
        
        # Age penalty (older = less exciting)
        age_penalty = 0.0
        if signal.first_seen_ts:
            age_minutes = (signal.timestamp - signal.first_seen_ts) / 60.0
            if age_minutes > 30:  # Penalty for tokens older than 30 min
                age_penalty = min(0.2, (age_minutes - 30) / 60.0)
        
        final_quality = base_quality + group_boost + velocity_boost - age_penalty
        return max(0.3, min(1.0, final_quality))  # Keep between 30-100%
    
    async def process_trade_intent(self, trade_intent_data: dict) -> None:
        """Convert trade intent from monitor to signal for executor"""
        
        signal = SignalData(
            ca=trade_intent_data['ca'],
            timestamp=time.time(),
            kind=trade_intent_data['kind'],
            ug_fast=trade_intent_data['ug_fast'],
            ug_slow=trade_intent_data['ug_slow'],
            velocity_mpm=trade_intent_data['velocity_mpm'],
            first_seen_ts=trade_intent_data.get('first_seen_ts'),
            rugcheck_score=trade_intent_data['rc_score'],
            rugcheck_risks=trade_intent_data['rc_risk_text'],
            rugcheck_lp=trade_intent_data['rc_lp_text']
        )
        
        # Calculate quality score
        signal.quality_score = self.calculate_quality_score(signal)
        
        # Add to executor queue
        await self.queue.put_signal(signal)
        
        logging.info(f"📤 Signal queued: {signal.ca} (Q={signal.quality_score:.3f})")
