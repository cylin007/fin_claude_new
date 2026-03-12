"""
Signal Detection - Entry and exit signals for Strategy A (Pullback Buy).
Designed for T+1 execution: analyze day T, execute day T+1.
"""
import pandas as pd
import numpy as np

import config
from indicators import (
    detect_long_lower_shadow,
    detect_bullish_engulfing,
    check_consecutive_above_ma,
)


class SignalType:
    BUY = 'BUY'
    SELL_STOP = 'SELL_STOP'
    SELL_TRAIL = 'SELL_TRAIL'
    SELL_TP1 = 'SELL_TP1'
    SELL_TP2 = 'SELL_TP2'
    SELL_MARKET = 'SELL_MARKET'  # Market regime filter
    PYRAMID = 'PYRAMID'


class Signal:
    def __init__(self, signal_type, stock_id, reason, priority=0, extra=None):
        self.type = signal_type
        self.stock_id = stock_id
        self.reason = reason
        self.priority = priority  # for ranking multiple buy signals
        self.extra = extra or {}

    def __repr__(self):
        return f"Signal({self.type}, {self.stock_id}, {self.reason})"


def detect_entry_signals(watchlist, screener, as_of_date, portfolio_state):
    """Detect Strategy A entry signals from the watchlist.

    Args:
        watchlist: list of dicts from screener (stock_id, industry, etc.)
        screener: Screener instance (for indicator access)
        as_of_date: current date (signals generated at close)
        portfolio_state: dict with current portfolio info for filtering

    Returns:
        list of Signal objects, ranked by priority (best first)
    """
    signals = []

    for stock in watchlist:
        stock_id = stock['stock_id']

        # Skip if already holding this stock
        if stock_id in portfolio_state.get('holdings', {}):
            continue

        # Skip if in cooldown (recently stopped out)
        if stock_id in portfolio_state.get('cooldown_stocks', {}):
            cooldown_until = portfolio_state['cooldown_stocks'][stock_id]
            if as_of_date <= cooldown_until:
                continue

        # Skip if blacklisted (stopped out too many times)
        if stock_id in portfolio_state.get('blacklist', set()):
            continue

        # Skip if industry is blacklisted (historically losing)
        industry = stock.get('industry', 'Unknown')
        if industry in config.INDUSTRY_BLACKLIST:
            continue

        # Skip if same industry already at max
        industry_count = portfolio_state.get('industry_counts', {}).get(industry, 0)
        if industry_count >= config.MAX_SAME_INDUSTRY:
            continue

        # Get indicator data
        ind_df = screener.get_indicators(stock_id, as_of_date)
        if ind_df is None or len(ind_df) < 3:
            continue

        latest = ind_df.iloc[-1]
        prev = ind_df.iloc[-2]

        # ====== Strategy A Entry Conditions ======

        # 1. Price near 20MA (within ±1%)
        if pd.isna(latest['ma_short']):
            continue
        dist = abs(latest['close'] - latest['ma_short']) / latest['ma_short']
        # Must be close to or below 20MA
        if latest['close'] > latest['ma_short'] * (1 + config.MA_TOUCH_TOLERANCE):
            # Price is too far above 20MA - hasn't pulled back enough
            # Exception: check if it just bounced back (was below yesterday)
            if prev['close'] > prev['ma_short'] * (1 + config.MA_TOUCH_TOLERANCE):
                continue

        # 2. 60MA still trending up
        if pd.isna(latest['ma_long_slope']) or latest['ma_long_slope'] <= 0:
            continue

        # 3. Price above 60MA (mid-term trend intact)
        if latest['close'] < latest['ma_long']:
            continue

        # 4. RSI in range [30, 65]
        if pd.isna(latest['rsi']):
            continue
        if latest['rsi'] < config.RSI_ENTRY_LOW or latest['rsi'] > config.RSI_ENTRY_HIGH:
            continue

        # 5. K-line pattern (Method B) - at least one must be true:
        has_pattern = False

        # 5a. Long lower shadow
        if detect_long_lower_shadow(latest, config.LOWER_SHADOW_RATIO):
            has_pattern = True

        # 5b. Bullish engulfing
        if not has_pattern and detect_bullish_engulfing(latest, prev):
            has_pattern = True

        # 5c. Consecutive closes above 20MA
        if not has_pattern:
            idx = len(ind_df) - 1
            if check_consecutive_above_ma(ind_df.reset_index(), idx, config.CONSECUTIVE_ABOVE_MA):
                has_pattern = True

        if not has_pattern:
            continue

        # 6. Volume surge: today's volume > 5-day average
        if pd.isna(latest['vol_5d_avg']) or latest['vol_5d_avg'] == 0:
            continue
        if latest['volume_lots'] < latest['vol_5d_avg'] * config.VOLUME_SURGE_RATIO:
            continue

        # All conditions met! Calculate priority score
        # Primary: RS ranking (higher = stronger momentum)
        # Secondary: RSI (lower = better pullback)
        rs_score = (stock.get('rs_score') or 1.0) * 50
        rsi_score = 100 - latest['rsi']  # invert: lower RSI = higher score

        priority = rs_score * 2 + rsi_score  # weight RS more

        signals.append(Signal(
            signal_type=SignalType.BUY,
            stock_id=stock_id,
            reason=f"Pullback to 20MA, RSI={latest['rsi']:.1f}, RevGr={(stock.get('revenue_growth') or 0):.1%}",
            priority=priority,
            extra={
                'industry': industry,
                'close': latest['close'],
                'ma_short': latest['ma_short'],
                'rsi': latest['rsi'],
                'revenue_growth': stock.get('revenue_growth', 0),
            }
        ))

    # Sort by priority (highest first)
    signals.sort(key=lambda s: s.priority, reverse=True)
    return signals


def detect_exit_signals(holdings, screener, as_of_date, taiex_row, portfolio_state):
    """Detect exit signals for current holdings.

    Args:
        holdings: dict of stock_id -> Position objects
        screener: Screener instance
        as_of_date: current date
        taiex_row: TAIEX data for today
        portfolio_state: dict with monthly PnL info

    Returns:
        list of Signal objects
    """
    signals = []

    for stock_id, position in holdings.items():
        ind_df = screener.get_indicators(stock_id, as_of_date)
        if ind_df is None or len(ind_df) == 0:
            continue

        latest = ind_df.iloc[-1]
        current_price = latest['close']

        # Update highest close for trailing stop
        position.update_highest(current_price)

        # ====== Exit Condition 1: Fixed Stop Loss ======
        if current_price <= position.stop_loss_price:
            signals.append(Signal(
                signal_type=SignalType.SELL_STOP,
                stock_id=stock_id,
                reason=f"Stop loss hit: {current_price:.2f} <= {position.stop_loss_price:.2f}",
                priority=100,  # High priority - stop losses first
                extra={'price': current_price},
            ))
            continue  # Don't check other exits if stop loss triggered

        # ====== Exit Condition 2: Trailing Stop (20MA with confirmation) ======
        if config.TRAILING_STOP_METHOD == '20ma':
            if not pd.isna(latest['ma_short']) and current_price < latest['ma_short']:
                # Only trigger trailing stop if position is in profit
                if current_price > position.avg_price:
                    # Check consecutive days below 20MA for confirmation
                    confirm_days = config.TRAILING_STOP_CONFIRM_DAYS
                    confirmed = True
                    if confirm_days > 1 and len(ind_df) >= confirm_days:
                        for d in range(confirm_days):
                            row_check = ind_df.iloc[-(d+1)]
                            if row_check['close'] >= row_check['ma_short']:
                                confirmed = False
                                break
                    if confirmed:
                        signals.append(Signal(
                            signal_type=SignalType.SELL_TRAIL,
                            stock_id=stock_id,
                            reason=f"Trailing stop: close < 20MA for {confirm_days}d",
                            priority=90,
                            extra={'price': current_price},
                        ))
                        continue

        # ====== Exit Condition 3: Take Profit ======
        pnl_pct = (current_price - position.avg_price) / position.avg_price

        tp1_ratio = getattr(config, 'TP1_SELL_RATIO', 1/3)
        tp2_ratio = getattr(config, 'TP2_SELL_RATIO', 1/3)

        if pnl_pct >= config.TAKE_PROFIT_1_PCT and not position.tp1_executed:
            signals.append(Signal(
                signal_type=SignalType.SELL_TP1,
                stock_id=stock_id,
                reason=f"Take profit 1: +{pnl_pct:.1%} (sell {tp1_ratio:.0%})",
                priority=50,
                extra={'price': current_price, 'sell_ratio': tp1_ratio},
            ))

        elif pnl_pct >= config.TAKE_PROFIT_2_PCT and not position.tp2_executed:
            signals.append(Signal(
                signal_type=SignalType.SELL_TP2,
                stock_id=stock_id,
                reason=f"Take profit 2: +{pnl_pct:.1%} (sell {tp2_ratio:.0%})",
                priority=50,
                extra={'price': current_price, 'sell_ratio': tp2_ratio},
            ))

    # ====== Market Regime Filter ======
    if config.MARKET_FILTER_ENABLED and taiex_row is not None:
        # Check if TAIEX is below its 60MA
        # We need TAIEX 60MA - calculate inline
        pass  # This is handled in the engine with TAIEX indicator data

    # Sort: stop losses first, then trailing stops, then take profits
    signals.sort(key=lambda s: s.priority, reverse=True)
    return signals


def detect_pyramid_signals(holdings, screener, as_of_date):
    """Detect pyramiding (add to winning position) signals.

    Args:
        holdings: dict of stock_id -> Position objects
        screener: Screener instance
        as_of_date: current date

    Returns:
        list of Signal objects
    """
    signals = []

    for stock_id, position in holdings.items():
        if position.pyramid_count >= config.MAX_PYRAMIDS:
            continue

        ind_df = screener.get_indicators(stock_id, as_of_date)
        if ind_df is None or len(ind_df) == 0:
            continue

        latest = ind_df.iloc[-1]
        current_price = latest['close']
        pnl_pct = (current_price - position.first_entry_price) / position.first_entry_price

        # Pyramid 1: +8% from first entry (only one pyramid allowed,
        # 40K initial + 20K pyramid = 60K = MAX_POSITION_SIZE)
        if position.pyramid_count == 0 and pnl_pct >= config.PYRAMID_1_THRESHOLD:
            # Additional check: price still above 20MA (trend intact)
            if not pd.isna(latest['ma_short']) and current_price > latest['ma_short']:
                signals.append(Signal(
                    signal_type=SignalType.PYRAMID,
                    stock_id=stock_id,
                    reason=f"Pyramid 1: +{pnl_pct:.1%} from entry",
                    priority=30,
                    extra={
                        'price': current_price,
                        'size': config.PYRAMID_1_SIZE,
                        'pyramid_level': 1,
                    },
                ))

    return signals
