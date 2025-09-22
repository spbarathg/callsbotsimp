import time
import logging
from typing import Optional, Tuple
from .models import Position, PositionStatus, ExitReason, PortfolioStats, SignalData


class RiskManager:
    """Asymmetric risk management - protect downside, maximize upside"""
    
    def __init__(self, settings):
        self.settings = settings
        self.portfolio_stats = PortfolioStats()
    
    def calculate_position_size(self, signal: SignalData, account_balance_usd: float) -> float:
        """Calculate position size based on signal quality and risk limits"""
        
        base_size = self.settings.base_position_size_usd
        
        # Quality-based sizing
        if signal.quality_score >= 0.8:
            size_multiplier = 1.4  # $14 for excellent signals
        elif signal.quality_score >= 0.7:
            size_multiplier = 1.2  # $12 for good signals
        elif signal.quality_score >= 0.6:
            size_multiplier = 1.0  # $10 for decent signals
        else:
            size_multiplier = 0.8  # $8 for marginal signals
        
        calculated_size = base_size * size_multiplier
        
        # Portfolio protection: Never risk more than 2% of account on single trade
        max_size_by_account = account_balance_usd * 0.02
        
        return min(calculated_size, max_size_by_account)
    
    def can_open_position(self, signal: SignalData) -> Tuple[bool, str]:
        """Check if we can open a new position"""
        
        # Reset daily stats if needed
        if self.portfolio_stats.should_reset_daily():
            self.portfolio_stats.reset_daily_stats()
        
        # Check if trading is halted
        if self.portfolio_stats.is_trading_halted:
            remaining_minutes = (self.portfolio_stats.trading_halted_until - time.time()) / 60
            return False, f"Trading halted for {remaining_minutes:.1f} more minutes"
        
        # Check daily loss limit
        account_value = self.get_estimated_account_value()  # Implement this
        daily_loss_limit = account_value * self.settings.daily_loss_limit_pct
        
        if self.portfolio_stats.daily_realized_pnl < -daily_loss_limit:
            self._halt_trading(hours=6, reason="Daily loss limit exceeded")
            return False, "Daily loss limit exceeded"
        
        # Check consecutive losses
        if self.portfolio_stats.consecutive_losses >= self.settings.consecutive_loss_limit:
            self._halt_trading(hours=2, reason="Too many consecutive losses") 
            return False, f"Too many consecutive losses ({self.portfolio_stats.consecutive_losses})"
        
        # Check position limits
        if self.portfolio_stats.active_positions >= self.settings.max_concurrent_positions:
            return False, f"Too many active positions ({self.portfolio_stats.active_positions})"
        
        # Check quality threshold
        if signal.quality_score < 0.6:
            return False, f"Signal quality too low ({signal.quality_score:.3f})"
        
        return True, "OK"
    
    def calculate_stop_loss_price(self, position: Position) -> float:
        """Calculate stop-loss price based on rugcheck risk profile"""
        
        base_stop_pct = self.settings.stop_loss_base_pct
        risk_multiplier = 1.0
        
        # Adjust based on rugcheck score (normalized 0–10 string)
        if position.rugcheck_score == "pending" or position.rugcheck_score == "n/a":
            risk_multiplier = 0.7  # Looser stops (allow more room) when unknown
        else:
            try:
                score = float(position.rugcheck_score)
            except Exception:
                score = 10.0
            if score <= 3:      # High risk (0-3)
                risk_multiplier = 1.4
            elif score <= 6:    # Medium risk (4-6)
                risk_multiplier = 1.2
            elif score >= 8:    # Low risk (8-10)
                risk_multiplier = 0.8  # 20% looser stops
        
        # Check specific risk flags
        risks_lower = position.rugcheck_risks.lower()
        if "honeypot" in risks_lower:
            risk_multiplier = 2.0  # Very tight stops
        elif "blacklist" in risks_lower:
            risk_multiplier = 1.8
        elif "high_tax" in risks_lower:
            risk_multiplier = 1.3
        
        # LP lock status
        if not position.rugcheck_lp_locked:
            risk_multiplier *= 1.2  # Tighter stops for unlocked LP
        
        # Calculate final stop loss
        adjusted_stop_pct = max(0.10, min(0.90, base_stop_pct * risk_multiplier))
        stop_loss_price = position.entry_price * (1 - adjusted_stop_pct)
        
        return stop_loss_price
    
    def get_time_stop_limit(self, position: Position) -> float:
        """Get time-based stop limit in minutes based on risk profile"""
        
        base_time = self.settings.time_stop_minutes
        
        # Adjust based on rugcheck
        if position.rugcheck_score == "pending":
            return base_time * 0.5  # 10 minutes for unknown
        elif "honeypot" in position.rugcheck_risks.lower():
            return base_time * 0.3  # 6 minutes for honeypot risk
        elif not position.rugcheck_lp_locked:
            return base_time * 0.7  # 14 minutes for unlocked LP
        else:
            return base_time  # Full 20 minutes for clean tokens
    
    def should_exit_position(self, position: Position, current_price: float) -> Tuple[bool, ExitReason, float]:
        """
        "Capture the Runner" exit logic with tiered profit-taking and ratcheting trailing stops
        Returns: (should_exit, exit_reason, sell_percentage)
        """
        
        if position.status != PositionStatus.ACTIVE:
            return False, ExitReason.MANUAL, 0.0
        
        current_multiple = current_price / position.entry_price
        minutes_held = (time.time() - position.entry_time) / 60
        
        # 1. DISASTER STOP-LOSS: -80% hard stop
        disaster_stop_price = position.entry_price * (1 - self.settings.disaster_stop_pct)
        # Tolerate floating point rounding at boundaries
        if current_price <= (disaster_stop_price + 1e-12):
            logging.warning(f"💥 DISASTER STOP triggered for {position.ca}: {current_multiple:.2f}x")
            return True, ExitReason.STOP_LOSS, 1.0

        # 1b. BASE STOP-LOSS: configurable risk-adjusted stop price
        if position.stop_loss_price and position.stop_loss_price > 0 and current_price <= position.stop_loss_price:
            logging.info(f"🛑 STOP LOSS triggered for {position.ca}: price {current_price:.8f} <= stop {position.stop_loss_price:.8f}")
            return True, ExitReason.STOP_LOSS, 1.0
        
        # 2. TIME STOP: risk-adjusted minutes if not reaching profit target
        profit_target = 1.0 + self.settings.time_stop_profit_target_pct
        time_stop_limit = self.get_time_stop_limit(position)
        if minutes_held >= time_stop_limit and current_multiple < profit_target:
            logging.info(f"⏰ TIME STOP triggered for {position.ca}: {current_multiple:.2f}x after {minutes_held:.1f}min")
            return True, ExitReason.TIME_STOP, 1.0
        
        # 3. DE-RISKING: Sell 33% at 3x to recover initial stake
        if not position.is_derisked and current_multiple >= self.settings.derisking_multiple:
            logging.info(f"🎯 DE-RISKING triggered for {position.ca}: {current_multiple:.2f}x - selling {self.settings.derisking_sell_pct:.1%}")
            return True, ExitReason.PROFIT_TAKE, self.settings.derisking_sell_pct
        
        # 4. NEVER-ENDING TIERS: sell small slices at specified multiples
        if position.is_derisked and position.remaining_tokens > 0:
            # Respect cooldown between partials
            if time.time() - position.last_partial_sell_time >= self.settings.partial_sell_cooldown_sec:
                # Parse tiers from config once per call (strings like "5:0.10,8:0.10")
                try:
                    tiers = []
                    for item in self.settings.profit_tiers_csv.split(','):
                        multiple_str, pct_str = item.split(':')
                        tiers.append((float(multiple_str), float(pct_str)))
                    tiers.sort(key=lambda x: x[0])
                except Exception:
                    tiers = []

                # Find next tier not yet hit
                for multiple, sell_pct in tiers:
                    multiple_int = int(multiple)
                    if multiple_int not in position.tiers_hit and current_multiple >= multiple:
                        # Ensure we keep a minimum runner balance
                        projected_remaining_pct = 1.0 - sell_pct
                        if projected_remaining_pct < self.settings.min_runner_pct:
                            # Cap sale to keep min runner
                            sell_pct = max(0.0, 1.0 - self.settings.min_runner_pct)
                        if sell_pct > 0:
                            logging.info(f"📈 TIER SELL for {position.ca}: {multiple}x reached → selling {sell_pct:.1%}")
                            position.tiers_hit.add(multiple_int)
                            position.last_partial_sell_time = time.time()
                            return True, ExitReason.PROFIT_TAKE, sell_pct

        # 5. RUNNER TRAILING STOP: ratcheting by zones
        if position.is_derisked:
            # Update runner peak
            # Initialize runner peak from existing peak if missing
            if position.runner_peak_price <= 0:
                position.runner_peak_price = max(position.peak_price, position.entry_price)
            if current_price > position.runner_peak_price:
                position.runner_peak_price = current_price

            # Determine trailing pct from zones
            trail_pct = self.settings.runner_trailing_stop_pct
            try:
                zones = []
                for item in self.settings.trailing_zones_csv.split(','):
                    mul_str, pct_str = item.split(':')
                    zones.append((float(mul_str), float(pct_str)))
                zones.sort(key=lambda x: x[0])
                for threshold, pct in zones:
                    if current_multiple >= threshold:
                        trail_pct = pct
            except Exception:
                pass

            trailing_stop_price = position.runner_peak_price * (1 - trail_pct)
            final_stop_price = max(trailing_stop_price, position.entry_price)
            # Include a small epsilon for float boundary
            if current_price <= (final_stop_price + 1e-12):
                logging.info(f"🏃 RUNNER STOP triggered for {position.ca}: peak {position.runner_peak_price/position.entry_price:.2f}x, current {current_multiple:.2f}x, trail {trail_pct:.0%}")
                return True, ExitReason.TRAILING_STOP, 1.0
        
        return False, ExitReason.MANUAL, 0.0
    
    def should_exit_runner(self, position: Position, current_price: float) -> bool:
        """
        Check if runner should be sold based on 30% trailing stop
        Updates runner peak tracking
        """
        
        # Update runner peak price
        if current_price > position.runner_peak_price:
            position.runner_peak_price = current_price
        
        # Calculate 30% trailing stop from peak
        trailing_stop_price = position.runner_peak_price * (1 - self.settings.runner_trailing_stop_pct)
        
        # Also ensure we never sell runner below breakeven (entry price)
        final_stop_price = max(trailing_stop_price, position.entry_price)
        
        return current_price <= final_stop_price
    
    def mark_position_derisked(self, position: Position, current_price: float, amount_sold: float):
        """
        Mark position as de-risked after selling 33% at 3x
        Set up runner tracking
        """
        
        position.is_derisked = True
        position.derisked_at_price = current_price
        position.derisked_amount = amount_sold
        position.runner_tokens = position.remaining_tokens  # What's left after de-risking
        position.runner_peak_price = current_price  # Start tracking runner peak
        
        # Move stop loss to breakeven for remaining tokens
        position.stop_loss_price = position.entry_price
        
        logging.info(f"🛡️  Position de-risked: {position.ca} | "
                    f"Sold: {amount_sold:.0f} tokens at {current_price/position.entry_price:.2f}x | "
                    f"Runner: {position.runner_tokens:.0f} tokens (risk-free)")
    
    def update_trailing_stop(self, position: Position, current_price: float) -> bool:
        """Update trailing stop and return True if should exit"""
        # Deprecated alternative trailing logic; handled in should_exit_position via zones
        return False
    
    def record_trade_result(self, trade_result_pnl: float):
        """Record trade result for portfolio tracking"""
        
        self.portfolio_stats.total_trades += 1
        self.portfolio_stats.daily_realized_pnl += trade_result_pnl
        
        if trade_result_pnl > 0:
            self.portfolio_stats.winning_trades += 1
            self.portfolio_stats.consecutive_losses = 0  # Reset on win
        else:
            self.portfolio_stats.losing_trades += 1
            self.portfolio_stats.consecutive_losses += 1
        
        logging.info(f"📊 Trade recorded: P&L=${trade_result_pnl:.2f}, "
                    f"Daily P&L=${self.portfolio_stats.daily_realized_pnl:.2f}, "
                    f"Win Rate={self.portfolio_stats.win_rate:.2%}")
    
    def _halt_trading(self, hours: float, reason: str):
        """Halt trading for specified hours"""
        self.portfolio_stats.trading_halted_until = time.time() + (hours * 3600)
        logging.warning(f"🛑 Trading halted for {hours} hours: {reason}")

    # Public helper for tests and controls
    def halt_trading(self, duration_minutes: int = 30):
        self._halt_trading(hours=duration_minutes / 60.0, reason="manual halt")
    
    def get_estimated_account_value(self) -> float:
        """Estimate total account value (implement based on your needs)"""
        # This should fetch actual account balance + position values.
        # If executor injects a callable later, use it; otherwise keep a conservative fallback.
        try:
            fetcher = getattr(self, "_account_value_fetcher", None)
            if callable(fetcher):
                return float(fetcher())
        except Exception:
            pass
        return 1000.0
