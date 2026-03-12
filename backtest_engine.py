"""
Backtest Engine - Main loop coordinating screener, signals, portfolio.
Designed for T+1 execution: signals at T close, execute at T+1.
"""
import pandas as pd
import numpy as np
from datetime import timedelta

import config
from data_loader import DataManager
from indicators import add_all_indicators
from screener import Screener
from signals import (
    SignalType, detect_entry_signals, detect_exit_signals, detect_pyramid_signals
)
from portfolio import Portfolio


class CoreETFTracker:
    """Tracks core ETF (006208/0050) DCA performance."""

    def __init__(self, initial_cash, etf_id, dm):
        self.cash = initial_cash
        self.shares = 0
        self.total_invested = 0
        self.etf_id = etf_id
        self.dm = dm
        self.target_value = 0  # for value averaging
        self.equity_history = []
        self.trades = []

    def monthly_invest(self, date, month_num):
        """Execute monthly DCA / value averaging."""
        # Get ETF price
        prices = self.dm.get_stock_prices(self.etf_id)
        prices = prices[prices['date'] <= pd.Timestamp(date)]
        if len(prices) == 0:
            return

        price = prices.iloc[-1]['close']

        # Value averaging formula
        self.target_value += config.CORE_DCA_BASE
        current_value = self.shares * price
        invest_amount = self.target_value - current_value

        # Apply limits
        invest_amount = max(invest_amount, config.CORE_DCA_MIN)
        invest_amount = min(invest_amount, config.CORE_DCA_MAX)
        invest_amount = min(invest_amount, self.cash)

        if invest_amount <= 0:
            return

        # Buy
        new_shares = int(invest_amount / price)
        if new_shares <= 0:
            return

        actual_cost = new_shares * price
        commission = max(actual_cost * config.COMMISSION_RATE, config.MIN_COMMISSION)
        total = actual_cost + commission

        if total > self.cash:
            new_shares = int((self.cash - config.MIN_COMMISSION) / (price * (1 + config.COMMISSION_RATE)))
            if new_shares <= 0:
                return
            actual_cost = new_shares * price
            commission = max(actual_cost * config.COMMISSION_RATE, config.MIN_COMMISSION)
            total = actual_cost + commission

        self.cash -= total
        self.shares += new_shares
        self.total_invested += total

        self.trades.append({
            'date': date,
            'action': 'DCA_BUY',
            'price': price,
            'shares': new_shares,
            'amount': actual_cost,
            'invest_target': invest_amount,
        })

    def get_equity(self, date):
        """Get total equity on a date."""
        prices = self.dm.get_stock_prices(self.etf_id)
        prices = prices[prices['date'] <= pd.Timestamp(date)]
        if len(prices) == 0:
            return self.cash
        price = prices.iloc[-1]['close']
        return self.cash + self.shares * price

    def record_equity(self, date):
        prices = self.dm.get_stock_prices(self.etf_id)
        prices = prices[prices['date'] <= pd.Timestamp(date)]
        price = prices.iloc[-1]['close'] if len(prices) > 0 else 0
        self.equity_history.append({
            'date': date,
            'equity': self.cash + self.shares * price,
            'shares': self.shares,
            'cash': self.cash,
        })


class BenchmarkTracker:
    """Tracks pure buy-and-hold benchmark (100% in single ETF)."""

    def __init__(self, initial_cash, etf_id, dm):
        self.initial_cash = initial_cash
        self.etf_id = etf_id
        self.dm = dm
        self.shares = 0
        self.cash = initial_cash
        self.bought = False
        self.equity_history = []

    def init_buy(self, date):
        """Buy all on first trading day."""
        if self.bought:
            return
        prices = self.dm.get_stock_prices(self.etf_id)
        prices = prices[prices['date'] <= pd.Timestamp(date)]
        if len(prices) == 0:
            return
        price = prices.iloc[-1]['close']
        self.shares = int(self.initial_cash * 0.99 / price)  # leave some for commission
        cost = self.shares * price
        commission = max(cost * config.COMMISSION_RATE, config.MIN_COMMISSION)
        self.cash = self.initial_cash - cost - commission
        self.bought = True

    def record_equity(self, date):
        prices = self.dm.get_stock_prices(self.etf_id)
        prices = prices[prices['date'] <= pd.Timestamp(date)]
        price = prices.iloc[-1]['close'] if len(prices) > 0 else 0
        self.equity_history.append({
            'date': date,
            'equity': self.cash + self.shares * price,
        })


class BacktestEngine:
    """Main backtest engine."""

    def __init__(self, data_manager, start_date, end_date, name=''):
        self.dm = data_manager
        self.start_date = start_date
        self.end_date = end_date
        self.name = name

        # Initialize components
        self.screener = Screener(data_manager)
        self.screener.precompute_indicators()

        # Satellite portfolio (30% of capital)
        satellite_cash = config.INITIAL_CAPITAL * config.SATELLITE_RATIO
        self.satellite = Portfolio(satellite_cash)

        # Core ETF tracker (70% of capital)
        core_cash = config.INITIAL_CAPITAL * config.CORE_RATIO
        self.core = CoreETFTracker(core_cash, config.CORE_ETF, data_manager)

        # Benchmark (100% buy-and-hold 0050)
        self.benchmark = BenchmarkTracker(config.INITIAL_CAPITAL, config.BENCHMARK_ETF, data_manager)

        # TAIEX indicators for market regime
        self.taiex_indicators = self._prepare_taiex_indicators()

        # State
        self.watchlist = []
        self.last_screen_date = None
        self.pending_signals = []  # signals from T, to execute at T+1
        self.daily_log = []
        self._daily_positions = []
        self._peak_equity = config.INITIAL_CAPITAL
        self._cumulative_realized_pnl = 0.0
        self._name_cache = {}

    def _get_stock_name(self, stock_id):
        """Get stock name with caching."""
        if stock_id not in self._name_cache:
            self._name_cache[stock_id] = self.dm.get_stock_name(stock_id)
        return self._name_cache[stock_id]

    def _prepare_taiex_indicators(self):
        """Pre-calculate TAIEX technical indicators."""
        taiex = self.dm.taiex.copy()
        if len(taiex) == 0:
            return pd.DataFrame()
        taiex = taiex.rename(columns={'volume': 'volume_raw'})
        taiex['volume_lots'] = taiex.get('volume_raw', 0)
        taiex = add_all_indicators(taiex, ma_short=config.MA_SHORT, ma_long=config.MA_LONG)
        return taiex.set_index('date')

    def _get_taiex_regime(self, date):
        """Determine market regime based on TAIEX vs 60MA."""
        if len(self.taiex_indicators) == 0:
            return 'GREEN'
        ts = pd.Timestamp(date)
        if ts not in self.taiex_indicators.index:
            # Find closest prior date
            prior = self.taiex_indicators[self.taiex_indicators.index <= ts]
            if len(prior) == 0:
                return 'GREEN'
            row = prior.iloc[-1]
        else:
            row = self.taiex_indicators.loc[ts]

        if pd.isna(row['ma_long']):
            return 'GREEN'

        taiex_close = row['close']
        taiex_ma60 = row['ma_long']
        taiex_ma20 = row['ma_short'] if not pd.isna(row['ma_short']) else taiex_close

        if taiex_close < taiex_ma60:
            return 'RED'  # 大盤跌破季線
        elif taiex_close < taiex_ma20:
            return 'YELLOW'  # 大盤跌破月線但在季線上
        else:
            return 'GREEN'  # 正常

    def run(self):
        """Run the full backtest. Returns results dict."""
        trading_days = self.dm.get_trading_days(self.start_date, self.end_date)
        if len(trading_days) == 0:
            print(f"  No trading days found for {self.start_date} to {self.end_date}")
            return None

        print(f"\n{'='*60}")
        print(f"Running backtest: {self.name}")
        print(f"Period: {self.start_date} to {self.end_date} ({len(trading_days)} trading days)")
        print(f"{'='*60}")

        # Initialize benchmark
        self.benchmark.init_buy(trading_days[0])
        month_count = 0
        last_month = None

        for i, date in enumerate(trading_days):
            date_str = str(date)[:10]
            current_month = date_str[:7]

            # Monthly reset and core ETF DCA
            if current_month != last_month:
                last_month = current_month
                self.satellite.reset_monthly(current_month)
                month_count += 1

                # Core ETF monthly DCA (on first trading day of month)
                self.core.monthly_invest(date, month_count)

            # Step 1: Execute pending signals from yesterday at today's price
            trades_idx_before = len(self.satellite.trades)
            if self.pending_signals:
                self._execute_pending(date)
                self.pending_signals = []
            today_trades = self.satellite.trades[trades_idx_before:]

            # Step 2: Get market regime
            regime = self._get_taiex_regime(date)

            # Step 3: Weekly screening (every Monday or first trading day of week)
            if self._is_screen_day(date, trading_days, i):
                if regime != 'RED':
                    self.watchlist = self.screener.screen(date)
                else:
                    self.watchlist = []  # No screening in bear market

            # Step 4: Detect exit signals
            taiex_row = self.dm.get_taiex_on_date(date)
            exit_signals = detect_exit_signals(
                self.satellite.positions,
                self.screener,
                date,
                taiex_row,
                self.satellite.get_state(),
            )

            # Market regime RED: tighten trailing stop to 10MA
            if regime == 'RED' and len(self.satellite.positions) > 0:
                for stock_id in list(self.satellite.positions.keys()):
                    if any(s.stock_id == stock_id for s in exit_signals):
                        continue
                    ind_df = self.screener.get_indicators(stock_id, date)
                    if ind_df is None or len(ind_df) == 0:
                        continue
                    latest = ind_df.iloc[-1]
                    price = latest['close']
                    # Use 10MA as tighter trailing stop in RED regime
                    ma10 = latest['close']  # fallback
                    if len(ind_df) >= 10:
                        ma10 = ind_df['close'].iloc[-10:].mean()
                    if price < ma10:
                        from signals import Signal
                        exit_signals.append(Signal(
                            signal_type=SignalType.SELL_MARKET,
                            stock_id=stock_id,
                            reason=f"RED regime: price {price:.0f} < 10MA {ma10:.0f}",
                            priority=95,
                        ))

            # Step 5: Detect pyramid signals (only if enabled)
            pyramid_signals = []
            if config.PYRAMID_ENABLED and regime == 'GREEN':
                pyramid_signals = detect_pyramid_signals(
                    self.satellite.positions,
                    self.screener,
                    date,
                )

            # Step 5.5: Overnight sentiment overlay (EWT or SGX)
            overnight_change = None
            if config.EWT_ENABLED:
                overnight_change = self.dm.get_ewt_change(date)
            elif config.SGX_ENABLED:
                overnight_change = self.dm.get_sgx_change(date)

            # Step 6: Detect entry signals (GREEN regime only)
            entry_signals = []
            if regime == 'GREEN':
                current_prices = self._get_current_prices(date)
                can_open, reason = self.satellite.can_open_new_position(date, current_prices)
                if can_open:
                    entry_signals = detect_entry_signals(
                        self.watchlist,
                        self.screener,
                        date,
                        self.satellite.get_state(),
                    )

            # Step 6.5: Apply overnight sentiment adjustments (EWT or SGX)
            sentiment_enabled = config.EWT_ENABLED or config.SGX_ENABLED
            if overnight_change is not None and sentiment_enabled:
                # Determine thresholds based on which source
                if config.EWT_ENABLED:
                    extreme_th = config.EWT_EXTREME_THRESHOLD
                    panic_th = config.EWT_PANIC_THRESHOLD
                    bull_th = config.EWT_BULL_THRESHOLD
                else:
                    extreme_th = config.SGX_EXTREME_THRESHOLD
                    panic_th = config.SGX_PANIC_THRESHOLD
                    bull_th = config.SGX_BULL_THRESHOLD

                # Extreme panic: block new entries + pyramids
                if overnight_change <= extreme_th:
                    entry_signals = []
                    pyramid_signals = []
                # Panic: block pyramids only (don't add to positions in panic,
                # but allow new entries — never suppress exits)
                elif overnight_change <= panic_th:
                    pyramid_signals = []
                # Bullish: boost entry priority
                elif overnight_change >= bull_th:
                    for s in entry_signals:
                        s.priority *= 1.2  # 20% priority boost

            # Step 7: Queue all signals for T+1 execution
            self.pending_signals = exit_signals + pyramid_signals + entry_signals

            # Step 8: Update trailing stops
            self.satellite.update_trailing_stops(self.screener, date)

            # Step 9: Record daily equity
            current_prices = self._get_current_prices(date)
            self.satellite.record_equity(date, current_prices)
            self.core.record_equity(date)
            self.benchmark.record_equity(date)

            # Step 10: Record daily log for CSV export
            self._record_daily_log(date, regime, today_trades, current_prices)

            # Progress logging (monthly)
            if i % 22 == 0 or i == len(trading_days) - 1:
                sat_eq = self.satellite.total_equity(current_prices)
                core_eq = self.core.get_equity(date)
                total = sat_eq + core_eq
                print(f"  {date_str}: Total={total:>12,.0f} "
                      f"(Core={core_eq:>10,.0f} Sat={sat_eq:>10,.0f}) "
                      f"Positions={len(self.satellite.positions)} "
                      f"Regime={regime}")

        # Final results
        results = self._compile_results(trading_days)
        results['daily_log'] = self.daily_log
        results['daily_positions'] = self._daily_positions
        return results

    def _record_daily_log(self, date, regime, today_trades, current_prices):
        """Record comprehensive daily state for CSV export."""
        date_str = str(date)[:10]

        # Market trend & state
        trend = 'bull' if regime != 'RED' else 'bear'
        state_map = {'GREEN': '安全', 'YELLOW': '警戒', 'RED': '危險'}
        state = state_map.get(regime, regime)

        # Overnight sentiment (EWT or SGX)
        if config.EWT_ENABLED:
            overnight_chg = self.dm.get_ewt_change(date)
        elif config.SGX_ENABLED:
            overnight_chg = self.dm.get_sgx_change(date)
        else:
            overnight_chg = None

        # Current holdings
        holdings_parts = []
        for sid, pos in self.satellite.positions.items():
            name = self._get_stock_name(sid)
            holdings_parts.append(f"{name}({pos.shares}股@{pos.avg_price:.1f})")
        holdings_str = ', '.join(holdings_parts)

        # Today's buys
        buy_parts = []
        for t in today_trades:
            action = t.get('action', '')
            if 'BUY' in action or 'PYRAMID' in action:
                name = self._get_stock_name(t['stock_id'])
                label = 'ADD' if 'PYRAMID' in action else 'BUY'
                buy_parts.append(f"{name}({label} {t['shares']}股@{t['price']:.1f})")
        buy_str = ', '.join(buy_parts)

        # Today's sells
        sell_parts = []
        for t in today_trades:
            if 'SELL' in t.get('action', ''):
                name = self._get_stock_name(t['stock_id'])
                pnl = t.get('pnl', 0)
                sell_parts.append(f"{name}(SELL {t['shares']}股@{t['price']:.1f} PnL:{pnl:+,.0f})")
        sell_str = ', '.join(sell_parts)

        # Swap detection (both sell + buy on same day)
        sold_ids = {t['stock_id'] for t in today_trades if 'SELL' in t.get('action', '')}
        bought_ids = {t['stock_id'] for t in today_trades if t.get('action') == 'BUY'}
        swap_str = ''
        if sold_ids and bought_ids:
            sold_names = [self._get_stock_name(s) for s in sold_ids]
            bought_names = [self._get_stock_name(s) for s in bought_ids]
            swap_str = '賣' + '+'.join(sold_names) + '→買' + '+'.join(bought_names)

        # Equity
        sat_eq = self.satellite.total_equity(current_prices)
        core_eq = self.core.get_equity(date)
        total_eq = sat_eq + core_eq
        total_cash = self.satellite.cash + self.core.cash

        # Returns
        prev_eq = self.daily_log[-1]['帳戶淨值'] if self.daily_log else config.INITIAL_CAPITAL
        daily_ret = (total_eq - prev_eq) / prev_eq * 100 if prev_eq > 0 else 0
        cum_ret = (total_eq - config.INITIAL_CAPITAL) / config.INITIAL_CAPITAL * 100

        # Drawdown
        self._peak_equity = max(self._peak_equity, total_eq)
        dd = (total_eq - self._peak_equity) / self._peak_equity * 100 if self._peak_equity > 0 else 0

        # Cumulative realized PnL
        today_realized = sum(t.get('pnl', 0) for t in today_trades if 'pnl' in t)
        self._cumulative_realized_pnl += today_realized

        # Unrealized PnL
        unrealized = sum(
            pos.unrealized_pnl(current_prices.get(pos.stock_id, pos.avg_price))
            for pos in self.satellite.positions.values()
        )

        self.daily_log.append({
            '日期': date_str,
            '大盤趨勢': trend,
            '大盤狀態': state,
            '持倉數': len(self.satellite.positions),
            '持有股票': holdings_str,
            '今日買入': buy_str,
            '今日賣出': sell_str,
            '今日換股': swap_str,
            '衛星現金': round(self.satellite.cash),
            '衛星淨值': round(sat_eq),
            '核心ETF股數': self.core.shares,
            '核心ETF淨值': round(core_eq),
            '總現金': round(total_cash),
            '帳戶淨值': round(total_eq),
            '當日報酬%': round(daily_ret, 2),
            '累計報酬%': round(cum_ret, 2),
            '回撤%': round(dd, 2),
            '已實現損益': round(self._cumulative_realized_pnl),
            '未實現損益': round(unrealized),
            '夜盤情緒%': round(overnight_chg, 2) if overnight_chg is not None else '',
        })

        # Record daily positions
        for sid, pos in self.satellite.positions.items():
            cp = current_prices.get(sid, pos.avg_price)
            self._daily_positions.append({
                'date': date_str,
                'ticker': sid,
                'name': self._get_stock_name(sid),
                'shares': pos.shares,
                'avg_cost': round(pos.avg_price, 2),
                'buy_price': round(pos.first_entry_price, 2),
                'current_price': round(cp, 2),
                'unrealized_pnl': round(pos.unrealized_pnl(cp)),
                'pnl_pct': round((cp / pos.avg_price - 1) * 100, 2),
            })

    def export_daily_csvs(self, output_dir):
        """Export daily position and summary CSVs."""
        import os
        os.makedirs(output_dir, exist_ok=True)
        safe_name = self.name.replace(' ', '_').replace('/', '_')

        if self.daily_log:
            df = pd.DataFrame(self.daily_log)
            path = os.path.join(output_dir, f'{safe_name}_daily_log.csv')
            df.to_csv(path, index=False, encoding='utf-8-sig')
            print(f"  Daily log saved: {path} ({len(df)} rows)")

        if self._daily_positions:
            df = pd.DataFrame(self._daily_positions)
            path = os.path.join(output_dir, f'{safe_name}_daily_positions.csv')
            df.to_csv(path, index=False, encoding='utf-8-sig')
            print(f"  Daily positions saved: {path} ({len(df)} rows)")

        # Core ETF trades
        if self.core.trades:
            df = pd.DataFrame(self.core.trades)
            path = os.path.join(output_dir, f'{safe_name}_core_etf_trades.csv')
            df.to_csv(path, index=False, encoding='utf-8-sig')
            print(f"  Core ETF trades saved: {path} ({len(df)} rows)")

        # Core ETF daily equity
        if self.core.equity_history:
            df = pd.DataFrame(self.core.equity_history)
            path = os.path.join(output_dir, f'{safe_name}_core_etf_equity.csv')
            df.to_csv(path, index=False, encoding='utf-8-sig')
            print(f"  Core ETF equity saved: {path} ({len(df)} rows)")

    def _execute_pending(self, execution_date):
        """Execute pending signals at today's prices."""
        # EWT execution-day check: block pending buys if overnight was extreme panic
        # get_ewt_change(T+1) returns US T = overnight sentiment before T+1 opens
        exec_ewt = None
        if config.EWT_ENABLED:
            exec_ewt = self.dm.get_ewt_change(execution_date)

        for signal in self.pending_signals:
            stock_id = signal.stock_id

            # Get execution price
            ind_df = self.screener.get_indicators(stock_id, execution_date)
            if ind_df is None or len(ind_df) == 0:
                continue
            latest = ind_df.iloc[-1]

            if config.EXECUTION_PRICE == 'close':
                exec_price = latest['close']
            else:
                exec_price = latest.get('open', latest['close'])

            if exec_price <= 0:
                continue

            # Apply slippage (buy higher, sell lower)
            slippage = getattr(config, 'SLIPPAGE_PCT', 0)
            is_buy = signal.type in (SignalType.BUY, SignalType.PYRAMID)
            if slippage > 0:
                exec_price *= (1 + slippage) if is_buy else (1 - slippage)

            # EWT execution filter: block buys/pyramids if overnight extreme panic
            if is_buy and exec_ewt is not None:
                extreme_th = (config.EWT_EXTREME_THRESHOLD if config.EWT_ENABLED
                              else config.SGX_EXTREME_THRESHOLD)
                if exec_ewt <= extreme_th:
                    continue  # Skip buy — overnight crash before execution day

            # Execute based on signal type
            if signal.type == SignalType.BUY:
                can_open, reason = self.satellite.can_open_new_position(execution_date)
                if not can_open:
                    continue
                # Check exposure limits
                total_exp = sum(p.shares * p.avg_price for p in self.satellite.positions.values())
                if total_exp + config.INITIAL_POSITION_SIZE > config.MAX_EXPOSURE:
                    continue

                # Calculate ATR-based stop loss
                initial_stop = None
                if config.ATR_STOP_ENABLED:
                    atr_val = latest.get('atr', None)
                    if atr_val is not None and not pd.isna(atr_val) and exec_price > 0:
                        atr_pct = atr_val / exec_price
                        stop_dist = max(min(config.ATR_STOP_MULTIPLIER * atr_pct,
                                            config.ATR_STOP_MAX),
                                        config.ATR_STOP_MIN)
                        initial_stop = exec_price * (1 - stop_dist)

                industry = signal.extra.get('industry', 'Unknown')
                self.satellite.execute_buy(
                    stock_id, exec_price, config.INITIAL_POSITION_SIZE,
                    execution_date, industry, is_pyramid=False,
                    initial_stop_price=initial_stop,
                )

            elif signal.type == SignalType.PYRAMID:
                if stock_id in self.satellite.positions:
                    pos = self.satellite.positions[stock_id]
                    new_size = pos.total_cost + signal.extra.get('size', 0)
                    if new_size <= config.MAX_POSITION_SIZE:
                        self.satellite.execute_buy(
                            stock_id, exec_price,
                            signal.extra.get('size', config.PYRAMID_1_SIZE),
                            execution_date, pos.industry, is_pyramid=True,
                        )

            elif signal.type in (SignalType.SELL_STOP, SignalType.SELL_TRAIL,
                                 SignalType.SELL_MARKET):
                if stock_id in self.satellite.positions:
                    self.satellite.execute_sell(
                        stock_id, exec_price, execution_date,
                        sell_ratio=1.0, reason=signal.type,
                    )

            elif signal.type == SignalType.SELL_TP1:
                if stock_id in self.satellite.positions:
                    self.satellite.execute_sell(
                        stock_id, exec_price, execution_date,
                        sell_ratio=1/3, reason='TP1',
                    )

            elif signal.type == SignalType.SELL_TP2:
                if stock_id in self.satellite.positions:
                    self.satellite.execute_sell(
                        stock_id, exec_price, execution_date,
                        sell_ratio=1/3, reason='TP2',
                    )

    def _is_screen_day(self, date, trading_days, idx):
        """Check if this is a screening day (first trading day of each week)."""
        if idx == 0:
            return True
        ts = pd.Timestamp(date)
        prev_ts = pd.Timestamp(trading_days[idx - 1])
        # New week if current day is Monday or if >2 calendar days since last trading day
        return ts.weekday() == 0 or (ts - prev_ts).days > 2

    def _get_current_prices(self, date):
        """Get current prices for all held stocks."""
        prices = {}
        for stock_id in self.satellite.positions:
            ind_df = self.screener.get_indicators(stock_id, date)
            if ind_df is not None and len(ind_df) > 0:
                prices[stock_id] = ind_df.iloc[-1]['close']
            else:
                prices[stock_id] = self.satellite.positions[stock_id].avg_price
        return prices

    def _compile_results(self, trading_days):
        """Compile backtest results into a results dict."""
        sat_equity = pd.DataFrame(self.satellite.equity_history)
        core_equity = pd.DataFrame(self.core.equity_history)
        bench_equity = pd.DataFrame(self.benchmark.equity_history)

        # Merge into combined equity
        if len(sat_equity) > 0 and len(core_equity) > 0:
            combined = sat_equity[['date', 'equity']].merge(
                core_equity[['date', 'equity']],
                on='date', suffixes=('_sat', '_core'),
            )
            combined['equity_total'] = combined['equity_sat'] + combined['equity_core']
        else:
            combined = pd.DataFrame()

        if len(bench_equity) > 0:
            combined = combined.merge(
                bench_equity[['date', 'equity']].rename(columns={'equity': 'equity_bench'}),
                on='date', how='left',
            )

        return {
            'name': self.name,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'trading_days': len(trading_days),
            'satellite_equity': sat_equity,
            'core_equity': core_equity,
            'benchmark_equity': bench_equity,
            'combined_equity': combined,
            'trades': self.satellite.trades,
            'core_trades': self.core.trades,
            'initial_capital': config.INITIAL_CAPITAL,
        }
