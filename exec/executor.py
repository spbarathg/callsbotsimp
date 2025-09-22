import asyncio
import logging
import time
from typing import Dict, Optional, Set

from .config import ExecutorSettings
from .models import Position, PositionStatus, ExitReason, SignalData, TradeResult
from .redis_queue import RedisSignalQueue
from .idempotency import IdempotencyStore
from .jupiter_client import JupiterClient, PriceMonitor
from .wallet import SolanaWallet
from .signer import EphemeralSigner
from .risk_manager import RiskManager
from .metrics import LatencyTracker, start_metrics_server, ORDERS_ABORTED_LATENCY, ORDERS_STARTED, ORDERS_CONFIRMED, ORDERS_FAILED, BOTS_TRADES_TOTAL, BOTS_TRADES_WON
from .order_manager import OrderManager
from app.onchain import OnchainAnalyzer
# EntryGates removed - your monitor system already provides excellent signal quality


class MemecoinExecutor:
    """Ultra-low latency memecoin execution engine"""
    
    def __init__(
        self,
        settings: ExecutorSettings,
        wallet: Optional["SolanaWallet"] = None,
        signer: Optional["EphemeralSigner"] = None,
        jupiter: Optional["JupiterClient"] = None,
        price_monitor: Optional["PriceMonitor"] = None,
        idempotency: Optional["IdempotencyStore"] = None,
        order_manager: Optional["OrderManager"] = None,
        signal_queue: Optional[object] = None,
    ):
        self.settings = settings
        
        # Core components
        self.wallet = wallet or SolanaWallet(
            settings.private_key,
            settings.rpc_url,
            settings.backup_rpc_url,
            settings.jito_bundle_url
        )
        self.signer = signer or EphemeralSigner(settings.private_key)
        self.jupiter = jupiter or JupiterClient(settings.jupiter_api_url)
        self.price_monitor = price_monitor or PriceMonitor(self.jupiter)
        self.risk_manager = RiskManager(settings)
        # Require Redis Streams queue for production
        if signal_queue is not None:
            self.signal_queue = signal_queue
        else:
            if not getattr(settings, 'redis_url', None):
                raise ValueError("Redis URL is required for executor signal queue")
            self.signal_queue = RedisSignalQueue(
                settings.redis_url,
                settings.redis_stream_key,
                settings.redis_consumer_group,
                consumer="executor"
            )
        # Idempotency store
        self.idempotency = idempotency or IdempotencyStore()
        # Order manager and locks
        self.order_manager = order_manager or OrderManager(getattr(settings, 'redis_url', None))
        
        # State tracking
        self.positions: Dict[str, Position] = {}
        self.last_processed_signal_time = 0.0
        self.is_running = False
        
        # Performance tracking
        self.trade_results: list[TradeResult] = []
        self.total_signals_processed = 0
        
        # Constants
        self.SOL_MINT = "So11111111111111111111111111111111111111112"
        self._cached_sol_usd: float | None = None
        self._sol_price_last_fetch: float = 0.0
        # Provide portfolio value fetcher to risk manager
        self.risk_manager._account_value_fetcher = self._estimate_account_value
        # Pre-trade onchain analyzer (lazy)
        self._pretrade_analyzer: OnchainAnalyzer | None = None
    
    async def start(self):
        """Start the executor engine"""
        
        logging.info("🚀 Starting Memecoin Executor...")
        start_metrics_server()

        # Resume any in-flight orders from SQLite
        try:
            inflight = await self.idempotency.load_positions_by_status("active")
            if inflight:
                logging.info(f"Resuming {len(inflight)} in-flight orders from persistence")
                for rec in inflight:
                    ca = rec["mint"]
                    # Minimal reconstruction
                    self.positions[ca] = Position(
                        ca=ca,
                        entry_price=float(rec.get("entry_price") or 0.0),
                        entry_time=float(rec.get("entry_time") or 0.0),
                        size_usd=float(rec.get("size_usd") or 0.0),
                        size_tokens=float(rec.get("size_tokens") or 0.0),
                        entry_signature=str(rec.get("entry_signature") or ""),
                        token_decimals=int(rec.get("token_decimals") or 9),
                        rugcheck_score="pending",
                        rugcheck_risks="pending",
                        rugcheck_lp_locked=False,
                    )
                    self.risk_manager.portfolio_stats.active_positions += 1
        except Exception as e:
            logging.error(f"Failed to resume in-flight orders: {e}")
        
        # Validate wallet
        balance = await self.wallet.get_balance()
        if balance is None or balance < 0.1:  # Need at least 0.1 SOL
            logging.error("❌ Insufficient SOL balance or wallet error")
            return
        
        logging.info(f"💰 Wallet balance: {balance:.3f} SOL")
        
        self.is_running = True
        
        # Start concurrent tasks
        tasks = [
            asyncio.create_task(self._signal_processor()),
            asyncio.create_task(self._position_manager()),
            asyncio.create_task(self._maintenance_task())
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logging.info("📴 Shutdown requested...")
        finally:
            self.is_running = False
            await self._cleanup()
    
    async def _signal_processor(self):
        """Process new signals and execute trades"""
        
        while self.is_running:
            try:
                # Get new signals
                # Redis queue returns list of (msg_id, SignalData)
                new_signals = []
                entries = await self.signal_queue.read_new()
                for msg_id, signal in entries:
                    new_signals.append((msg_id, signal))
                
                for msg_id, signal in new_signals:
            # Idempotency check
            sig_id = getattr(signal, 'signal_id', None) or f"{signal.ca}:{int(signal.first_seen_ts or signal.timestamp)}"
            # Optional Redis idempotency could be added here guarded by settings.idempotency_backend
            if await self.idempotency.has_processed(sig_id):
                        if msg_id:
                            await self.signal_queue.ack(msg_id)
                        continue
                    await self._process_signal(signal)
                    await self.idempotency.mark_processed(sig_id)
                    if msg_id:
                        await self.signal_queue.ack(msg_id)
                    self.last_processed_signal_time = max(self.last_processed_signal_time, signal.timestamp)
                
                await asyncio.sleep(0.1)  # 100ms loop for ultra-low latency
                
            except Exception as e:
                logging.error(f"Signal processor error: {e}")
                await asyncio.sleep(1.0)
    
    async def _process_signal(self, signal: SignalData):
        """Process individual signal and execute trade if criteria met"""
        
        try:
            self.total_signals_processed += 1
            tracker = LatencyTracker()
            
            # Skip if already holding this token
            if signal.ca in self.positions:
                logging.debug(f"⏭️  Skip {signal.ca} - already holding")
                return
            
            # Your monitor system already provides excellent signal filtering!
            # Signals that reach here have already passed:
            # - Multiple group mentions (ug_fast/ug_slow thresholds)
            # - Address validation (parser.py)
            # - Timing filters (tracker.py)
            
            logging.info(f"🎯 Processing signal: {signal.ca} | "
                        f"Groups: {signal.ug_fast} | "
                        f"Quality: {signal.quality_score:.3f} | "
                        f"Velocity: {signal.velocity_mpm:.1f}/min")
            
            
            # Check portfolio-level risk management
            can_trade, reason = self.risk_manager.can_open_position(signal)
            if not can_trade:
                logging.info(f"⛔ Trade blocked: {signal.ca} - {reason}")
                return

            # Token-level lock to prevent duplicates while order is running
            lock_key = f"{signal.ca}:{getattr(signal, 'signal_id', '')}"
            acquired = await self.order_manager.acquire_lock(lock_key, ttl_ms=120000)
            if not acquired:
                logging.info(f"⛔ Duplicate lock active for {signal.ca}; skipping")
                return
            try:
                # Pre-trade micro-guard (non-blocking budget)
                if getattr(self.settings, 'pretrade_onchain_guard', False):
                    try:
                        if self._pretrade_analyzer is None:
                            # Use app config RPC defaults if available; fall back to executor RPC
                            rpc_url = getattr(self.settings, 'rpc_url', None) or "https://api.mainnet-beta.solana.com"
                            self._pretrade_analyzer = OnchainAnalyzer(rpc_url, timeout_ms=getattr(self.settings, 'pretrade_timeout_ms', 150))
                        analysis = await asyncio.wait_for(self._pretrade_analyzer.analyze(signal.ca), timeout=(getattr(self.settings, 'pretrade_timeout_ms', 150)/1000.0))
                    except Exception:
                        analysis = None
                    decision = True
                    reason = "OK"
                    if analysis:
                        top1 = float(analysis.get('top1_pct', 0.0))
                        top10 = float(analysis.get('top10_pct', 0.0))
                        if top1 >= self.settings.pretrade_top1_max_pct or top10 >= self.settings.pretrade_top10_max_pct:
                            decision = False
                            reason = f"holder concentration top1={top1:.1f} top10={top10:.1f}"
                    else:
                        if str(getattr(self.settings, 'pretrade_fail_mode', 'soft')).lower() == 'hard':
                            decision = False
                            reason = "pretrade timeout"
                    if not decision:
                        logging.info(f"🛑 Pre-trade guard rejected {signal.ca}: {reason}")
                        return
                # Pre-trade rugcheck gates
                risks_lower = (signal.rugcheck_risks or "").lower()
                if any(flag in risks_lower for flag in ["honeypot", "blacklist", "blacklisted"]):
                    logging.info(f"🛑 Skip {signal.ca} due to rug flags: {signal.rugcheck_risks}")
                    return
                # Optional: basic LP/tax sanity via rugcheck text if provided
                if "high_tax" in risks_lower:
                    logging.info(f"🛑 Skip {signal.ca} due to high tax flag")
                    return
                
                # Calculate position size ($10 base)
                position_size_usd = self.settings.base_position_size_usd
                
                # Execute buy
                await self._execute_buy(signal, position_size_usd, tracker)
            finally:
                await self.order_manager.release_lock(lock_key)
            
        except Exception as e:
            logging.error(f"Signal processing error for {signal.ca}: {e}")
    
    async def _execute_buy(self, signal: SignalData, size_usd: float, tracker: LatencyTracker):
        """Execute buy order for signal"""
        
        try:
            logging.info(f"🎯 Executing buy: {signal.ca} for ${size_usd:.2f}")
            # Robust signal id for persistence
            sig_id = (getattr(signal, 'signal_id', None) or str(signal.timestamp))
            
            # Calculate SOL amount needed using live SOL/USD
            sol_usd = await self._get_sol_usd_price()
            if not sol_usd or sol_usd <= 0:
                logging.warning("⚠️  Could not fetch SOL/USD price")
                return
            sol_amount = size_usd / sol_usd
            sol_lamports = self.wallet.sol_to_lamports(sol_amount)
            
            # Get quote from Jupiter
            tracker.mark_quote_requested()
            ORDERS_STARTED.inc()
            quote = await self.jupiter.get_quote(
                input_mint=self.SOL_MINT,
                output_mint=signal.ca,
                amount=sol_lamports,
                slippage_bps=self.settings.max_slippage_bps,
                only_direct=True
            )
            
            if not quote:
                # Fallback: allow non-direct routes
                quote = await self.jupiter.get_quote(
                    input_mint=self.SOL_MINT,
                    output_mint=signal.ca,
                    amount=sol_lamports,
                    slippage_bps=self.settings.max_slippage_bps,
                    only_direct=False
                )
                if not quote:
                    logging.warning(f"❌ No quote for {signal.ca}")
                    return
            tracker.mark_quote_received()
            await self.idempotency.record_transition(sig_id, signal.ca, "QUOTED")
            
            # Validate quote
            is_valid, validation_msg = self.jupiter.validate_quote_for_memecoin(
                quote, self.settings.max_impact_bps / 100.0
            )
            
            if not is_valid:
                logging.warning(f"❌ Quote validation failed for {signal.ca}: {validation_msg}")
                return
            
            # Get swap transaction
            swap_transaction = await self.jupiter.get_swap_transaction(
                quote=quote,
                user_public_key=self.wallet.public_key,
                priority_fee_lamports=1000
            )
            
            if not swap_transaction:
                logging.error(f"❌ Failed to get swap transaction for {signal.ca}")
                return
            
            # Latency gating: if hot-path already > 100ms before submit, abort
            if tracker.hot_path_ms_so_far() > 100.0:
                ORDERS_ABORTED_LATENCY.inc()
                logging.error(f"Latency gate abort for {signal.ca}: {tracker.hot_path_ms_so_far():.1f}ms > 100ms")
                await self.idempotency.record_transition(sig_id, signal.ca, "FAILED")
                return

            # Sign with EphemeralSigner
            signed_b64 = self.signer.sign_b64(swap_transaction)
            tracker.mark_signed()
            await self.idempotency.record_transition(sig_id, signal.ca, "SIGNED")
            # Submit
            # Submit via wallet
            signature = await self.wallet.send_signed_transaction(signed_b64)
            tracker.mark_submitted()
            
            if not signature:
                logging.error(f"❌ Failed to send transaction for {signal.ca}")
                return
            
            # Calculate entry details
            in_amount, out_amount, price_impact = self.jupiter.calculate_impact_and_amounts(quote)
            # Normalize to USD/token
            in_sol = in_amount / 1e9
            in_usd = in_sol * sol_usd
            # Derive token decimals if present; fallback to position default later
            out_decimals = 9
            try:
                token_info = quote.get('outToken')
                if isinstance(token_info, dict) and 'decimals' in token_info:
                    out_decimals = int(token_info['decimals'])
            except Exception:
                pass
            out_tokens_ui = out_amount / (10 ** out_decimals)
            entry_price = in_usd / out_tokens_ui if out_tokens_ui > 0 else 0
            
            # Create position
            position = Position(
                ca=signal.ca,
                signal_id=sig_id,
                entry_price=entry_price,
                entry_time=time.time(),
                size_usd=size_usd,
                size_tokens=out_amount,
                entry_signature=signature,
                rugcheck_score=signal.rugcheck_score,
                rugcheck_risks=signal.rugcheck_risks,
                rugcheck_lp_locked="%" in signal.rugcheck_lp and "0%" not in signal.rugcheck_lp,
                token_decimals=int(quote.get('outToken', {}).get('decimals', 9)) if isinstance(quote.get('outToken'), dict) else 9
            )
            
            # Set dynamic stop loss based on rug profile
            position.stop_loss_price = self.risk_manager.calculate_stop_loss_price(position)
            
            # Store position
            self.positions[signal.ca] = position
            self.risk_manager.portfolio_stats.active_positions += 1
            await self.idempotency.upsert_position(
                signal_id=sig_id,
                mint=signal.ca,
                entry_signature=signature,
                entry_time=position.entry_time,
                size_usd=position.size_usd,
                size_tokens=position.size_tokens,
                token_decimals=position.token_decimals,
                entry_price=position.entry_price,
                status="active",
            )
            await self.idempotency.record_transition(sig_id, signal.ca, "CONFIRMED")
            
            logging.info(f"✅ Position opened: {signal.ca} | Size: ${size_usd:.2f} | "
                        f"Tokens: {out_amount:.0f} | Stop: ${position.stop_loss_price:.8f}")
            
            # Confirm transaction in background
            asyncio.create_task(self._confirm_transaction(signature, position, tracker))
            
        except Exception as e:
            logging.error(f"Buy execution error for {signal.ca}: {e}")
    
    async def _confirm_transaction(self, signature: str, position: Position, tracker: LatencyTracker):
        """Confirm transaction and handle failures"""
        
        confirmed = await self.wallet.confirm_transaction(signature, timeout_seconds=30.0)
        
        if not confirmed:
            logging.warning(f"⚠️  Transaction not confirmed: {signature}")
            # Remove failed position
            if position.ca in self.positions:
                del self.positions[position.ca]
                self.risk_manager.portfolio_stats.active_positions -= 1
            try:
                await self.idempotency.upsert_position(
                    signal_id=position.signal_id or "unknown",
                    mint=position.ca,
                    entry_signature=signature,
                    entry_time=position.entry_time,
                    size_usd=position.size_usd,
                    size_tokens=position.size_tokens,
                    token_decimals=position.token_decimals,
                    entry_price=position.entry_price,
                    status="failed",
                )
                await self.idempotency.record_transition(position.signal_id or "unknown", position.ca, "FAILED")
            except Exception:
                pass
            ORDERS_FAILED.inc()
        else:
            tracker.mark_confirmed()
            ORDERS_CONFIRMED.inc()
    
    async def _position_manager(self):
        """Monitor and manage active positions"""
        
        while self.is_running:
            try:
                if not self.positions:
                    await asyncio.sleep(1.0)
                    continue
                
                # Check each position
                positions_to_remove = []
                
                for ca, position in self.positions.items():
                    try:
                        await self._manage_position(position)
                        
                        if position.status != PositionStatus.ACTIVE:
                            positions_to_remove.append(ca)
                            
                    except Exception as e:
                        logging.error(f"Position management error for {ca}: {e}")
                
                # Remove completed positions
                for ca in positions_to_remove:
                    del self.positions[ca]
                    self.risk_manager.portfolio_stats.active_positions -= 1
                
                # Sleep based on interval setting
                interval_seconds = self.settings.price_check_interval_ms / 1000.0
                await asyncio.sleep(interval_seconds)
                
            except Exception as e:
                logging.error(f"Position manager error: {e}")
                await asyncio.sleep(5.0)
    
    async def _manage_position(self, position: Position):
        """Manage individual position"""
        
        try:
            # Get current price
            current_price = await self.price_monitor.get_current_price(position.ca)
            
            if current_price is None:
                logging.warning(f"⚠️  Cannot get price for {position.ca}")
                return
            
            # Update peak tracking (USD)
            if current_price > position.peak_price:
                position.peak_price = current_price
                position.peak_multiple = current_price / position.entry_price
            
            position.last_price_check = time.time()
            
            # Check exit conditions (staged exits handled by risk_manager tiers config)
            should_exit, exit_reason, sell_percentage = self.risk_manager.should_exit_position(position, current_price)
            
            if should_exit:
                await self._execute_sell(position, sell_percentage, exit_reason, current_price)
                return

            # Near-stop turbo: if within configured delta of any active stop/target, recheck sooner
            try:
                delta_pct = getattr(self.settings, 'near_stop_delta_pct', 0.03)
                turbo_ms = getattr(self.settings, 'near_stop_check_ms', 150)
                # Evaluate proximity to stop_loss or runner trailing implied stop
                targets = []
                if position.stop_loss_price and position.stop_loss_price > 0:
                    targets.append(position.stop_loss_price)
                if position.is_derisked and position.runner_peak_price > 0:
                    # Approximate current trailing stop price using last known zone calculation
                    trail_pct = self.settings.runner_trailing_stop_pct
                    targets.append(max(position.runner_peak_price * (1 - trail_pct), position.entry_price))
                for t in targets:
                    if t > 0 and abs(current_price - t) / max(t, 1e-12) <= delta_pct:
                        await asyncio.sleep(turbo_ms / 1000.0)
                        return
            except Exception:
                pass
            
        except Exception as e:
            logging.error(f"Position management error for {position.ca}: {e}")
    
    async def _execute_sell(self, position: Position, sell_percentage: float, exit_reason: ExitReason, current_price: float):
        """Execute sell order with 'Capture the Runner' logic"""
        
        try:
            current_multiple = current_price / position.entry_price
            tokens_to_sell = position.remaining_tokens * sell_percentage
            # Guard against tiny amounts due to decimals/rounding
            token_decimals = getattr(position, 'token_decimals', 9)
            min_atomic = 1  # smallest on-chain unit
            if int(tokens_to_sell) < min_atomic and sell_percentage < 1.0:
                logging.info(f"⏭️  Skipping tiny partial for {position.ca} (amount too small)")
                return
            # Floor to integer atomic units
            tokens_to_sell = int(tokens_to_sell)
            
            logging.info(f"🎯 Selling {sell_percentage:.1%} of {position.ca} | "
                        f"Reason: {exit_reason.value} | Multiple: {current_multiple:.2f}x")
            
            # Get quote for selling tokens
            quote = await self.jupiter.get_quote(
                input_mint=position.ca,
                output_mint=self.SOL_MINT,
                amount=int(tokens_to_sell),
                slippage_bps=self.settings.max_slippage_bps,
                only_direct=True
            )
            
            if not quote:
                # Fallback to non-direct routes
                quote = await self.jupiter.get_quote(
                    input_mint=position.ca,
                    output_mint=self.SOL_MINT,
                    amount=int(tokens_to_sell),
                    slippage_bps=self.settings.max_slippage_bps,
                    only_direct=False
                )
                if not quote:
                    logging.warning(f"❌ No sell quote for {position.ca}")
                    return
            
            # Get swap transaction
            swap_transaction = await self.jupiter.get_swap_transaction(
                quote=quote,
                user_public_key=self.wallet.public_key
            )
            
            if not swap_transaction:
                logging.error(f"❌ Failed to get sell transaction for {position.ca}")
                return
            
            # Send transaction
            signature = await self.wallet.send_transaction(swap_transaction)
            
            if signature:
                # Update position
                position.remaining_tokens -= tokens_to_sell
                
                # Calculate P&L for this sell
                in_amount, out_amount, _ = self.jupiter.calculate_impact_and_amounts(quote)
                sol_received = out_amount / 1e9
                sol_usd = await self._get_sol_usd_price()
                sol_value_usd = sol_received * (sol_usd or 0)
                
                if sell_percentage == 1.0:
                    # Full exit
                    total_pnl = sol_value_usd - position.size_usd
                    position.status = PositionStatus.COMPLETED
                    position.realized_pnl = total_pnl
                    
                    # Record trade result
                    trade_result = TradeResult(
                        ca=position.ca,
                        entry_time=position.entry_time,
                        exit_time=time.time(),
                        entry_price=position.entry_price,
                        exit_price=current_price,
                        size_usd=position.size_usd,
                        pnl_usd=total_pnl,
                        pnl_pct=total_pnl / position.size_usd,
                        exit_reason=exit_reason,
                        duration_minutes=(time.time() - position.entry_time) / 60,
                        peak_multiple=position.peak_multiple
                    )
                    
                    self.trade_results.append(trade_result)
                    self.risk_manager.record_trade_result(total_pnl)
                    BOTS_TRADES_TOTAL.inc()
                    if total_pnl > 0:
                        BOTS_TRADES_WON.inc()
                    try:
                        await self.idempotency.upsert_position(
                            signal_id=position.signal_id or "unknown",
                            mint=position.ca,
                            entry_signature=position.entry_signature,
                            entry_time=position.entry_time,
                            size_usd=position.size_usd,
                            size_tokens=position.size_tokens,
                            token_decimals=position.token_decimals,
                            entry_price=position.entry_price,
                            status="closed",
                        )
                        await self.idempotency.record_transition(position.signal_id or "unknown", position.ca, "CLOSED")
                    except Exception:
                        pass
                    
                    logging.info(f"🏁 Position closed: {position.ca} | "
                                f"P&L: ${total_pnl:.2f} ({total_pnl/position.size_usd:.1%}) | "
                                f"Peak: {position.peak_multiple:.2f}x")
                else:
                    # DE-RISKING SALE: Mark position as de-risked
                    if exit_reason == ExitReason.PROFIT_TAKE and not position.is_derisked:
                        self.risk_manager.mark_position_derisked(position, current_price, tokens_to_sell)
                    
                    partial_pnl = sol_value_usd - (position.size_usd * sell_percentage)
                    position.realized_pnl += partial_pnl
                    try:
                        await self.idempotency.record_exit(position.signal_id or "unknown", position.ca, sell_percentage)
                    except Exception:
                        pass
                    
                    logging.info(f"💰 Partial sell: {position.ca} | "
                                f"Sold: {sell_percentage:.1%} | "
                                f"Remaining: {position.remaining_tokens:.0f} tokens | "
                                f"De-risked: {'✅' if position.is_derisked else '❌'}")
            
        except Exception as e:
            logging.error(f"Sell execution error for {position.ca}: {e}")

    async def _get_sol_usd_price(self) -> Optional[float]:
        """Fetch and cache SOL/USD using Jupiter price API."""
        now = time.time()
        if self._cached_sol_usd and now - self._sol_price_last_fetch < 10:
            return self._cached_sol_usd
        # Jupiter price API expects mint; use SOL_MINT
        price = await self.price_monitor.get_current_price(self.SOL_MINT)
        if price:
            self._cached_sol_usd = price
            self._sol_price_last_fetch = now
        return self._cached_sol_usd

    def _estimate_account_value(self) -> float:
        """Estimate account value: MTM of positions (USD). Conservative, synchronous."""
        try:
            total_positions_value = 0.0
            for pos in self.positions.values():
                current_price = pos.peak_price or pos.entry_price
                remaining_ui = pos.remaining_tokens / (10 ** getattr(pos, 'token_decimals', 9))
                total_positions_value += remaining_ui * current_price
            baseline = 1000.0
            return max(baseline, total_positions_value)
        except Exception:
            return 1000.0
    
    async def _maintenance_task(self):
        """Background maintenance and cleanup"""
        
        while self.is_running:
            try:
                # Cleanup old signals from queue
                await self.signal_queue.cleanup_old_signals(max_age_hours=24.0)
                
                # Clear price cache periodically
                self.price_monitor.clear_cache()
                
                # Log portfolio stats
                self._log_portfolio_stats()
                
                # Log signal processing statistics
                if self.total_signals_processed > 0:
                    logging.info(f"📊 Signal stats: {self.total_signals_processed} processed")
                
                await asyncio.sleep(300)  # 5 minutes
                
            except Exception as e:
                logging.error(f"Maintenance task error: {e}")
                await asyncio.sleep(60)
    
    def _log_portfolio_stats(self):
        """Log current portfolio statistics"""
        
        if self.positions:
            total_unrealized = sum(
                (pos.peak_price - pos.entry_price) / pos.entry_price * pos.size_usd
                for pos in self.positions.values()
            )
            
            logging.info(f"📊 Portfolio: {len(self.positions)} active positions | "
                        f"Unrealized P&L: ${total_unrealized:.2f} | "
                        f"Daily P&L: ${self.risk_manager.portfolio_stats.daily_realized_pnl:.2f}")
    
    async def _cleanup(self):
        """Clean shutdown"""
        
        logging.info("🧹 Cleaning up executor...")
        
        try:
            await self.wallet.close()
            await self.jupiter.close()
        except Exception as e:
            logging.error(f"Cleanup error: {e}")
        
        logging.info("✅ Executor shutdown complete")


# Entry point for running executor standalone
async def main():
    """Run executor as standalone process"""
    
    import sys
    import os
    
    # Add parent directory to path for imports
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from exec.config import load_executor_settings
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('executor.log')
        ]
    )
    
    try:
        settings = load_executor_settings()
        executor = MemecoinExecutor(settings)
        await executor.start()
    except Exception as e:
        logging.error(f"Executor startup error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
