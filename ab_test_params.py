"""
參數敏感度測試 — 支援 --period 平行跑不同區間
用法:
  python3 ab_test_params.py <group> [--period P1,P3,P7]

  group: industry | stoploss | sell | all
  --period: 指定區間（逗號分隔），省略則跑全部 7 區間

範例:
  # 3 個 terminal 平行跑
  python3 ab_test_params.py all --period P1,P2,P3 &
  python3 ab_test_params.py all --period P4,P5 &
  python3 ab_test_params.py all --period P6,P7 &

測試項目：
  1. MAX_SAME_INDUSTRY: 1 vs 2(現行) vs 3
  2. 停損幅度: 3% vs 5%(現行) vs 7%
  3. 賣出策略: TP1減1/3(現行) vs 全賣
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from data_loader import DataManager
from backtest_engine import BacktestEngine
from report import calculate_metrics

# 7 個測試區間
PERIODS = {
    "P1": ("P1_完整4年",    "2022-01-01", "2026-02-11"),
    "P2": ("P2_純空頭",     "2022-01-01", "2022-10-31"),
    "P3": ("P3_純多頭",     "2023-01-01", "2024-07-31"),
    "P4": ("P4_高檔修正",   "2024-07-01", "2025-01-31"),
    "P5": ("P5_急殺崩盤",   "2025-02-01", "2025-07-31"),
    "P6": ("P6_V轉狂牛",    "2025-08-01", "2026-03-10"),
    "P7": ("P7_近半年",     "2025-09-11", "2026-03-10"),
}

# 測試組合：(名稱, {參數覆蓋})
TESTS = [
    # === 基準線 ===
    ("基準(現行)",         {}),

    # === 1. MAX_SAME_INDUSTRY ===
    ("產業上限=1",         {"MAX_SAME_INDUSTRY": 1}),
    ("產業上限=2(現行)",   {"MAX_SAME_INDUSTRY": 2}),
    ("產業上限=3",         {"MAX_SAME_INDUSTRY": 3}),

    # === 2. 停損幅度 ===
    ("停損3%",             {"INITIAL_STOP_LOSS_PCT": 0.03}),
    ("停損5%(現行)",       {"INITIAL_STOP_LOSS_PCT": 0.05}),
    ("停損7%",             {"INITIAL_STOP_LOSS_PCT": 0.07}),

    # === 3. 賣出策略: TP1 1/3 vs 全賣 ===
    ("TP1減1/3(現行)",     {"TP1_SELL_RATIO": 1/3}),
    ("TP1全賣",            {"TP1_SELL_RATIO": 1.0}),
]


def run_one(dm, label, start, end, overrides):
    """Run single backtest with parameter overrides, return metrics dict."""
    originals = {}
    for k, v in overrides.items():
        if hasattr(config, k):
            originals[k] = getattr(config, k)
        setattr(config, k, v)
    try:
        engine = BacktestEngine(dm, start, end, name=label)
        results = engine.run()
        if results is None:
            return None
        combined = results['combined_equity']
        trades = results['trades']
        if len(combined) == 0:
            return None
        metrics = calculate_metrics(combined, trades, config.INITIAL_CAPITAL)

        return {
            'total_return': metrics.get('Total Return', 'N/A'),
            'pf': metrics.get('Profit Factor', 'N/A'),
            'win_rate': metrics.get('Win Rate', 'N/A'),
            'mdd': metrics.get('Max Drawdown (MDD)', 'N/A'),
            'sharpe': metrics.get('Sharpe Ratio', 'N/A'),
            'trades': metrics.get('Total Trades', 'N/A'),
        }
    finally:
        for k, v in originals.items():
            setattr(config, k, v)


def print_summary_tables(tests_to_run, selected_periods, all_results):
    """Print PF / Return / MDD summary tables."""
    period_names = [PERIODS[k][0] for k in selected_periods]

    # --- PF ---
    print("\n")
    print("=" * 90)
    print("彙整結果 — Profit Factor")
    print("=" * 90)
    header = f"{'測試組合':<18}"
    for pn in period_names:
        header += f" {pn:>10}"
    print(header)
    print("-" * 90)
    for test_name, _ in tests_to_run:
        row = f"{test_name:<18}"
        for pn in period_names:
            m = all_results.get(test_name, {}).get(pn, {})
            pf = m.get('pf', 'N/A')
            row += f" {pf:>10}"
        print(row)

    # --- Return ---
    print("\n")
    print("=" * 90)
    print("彙整結果 — Total Return")
    print("=" * 90)
    print(header)
    print("-" * 90)
    for test_name, _ in tests_to_run:
        row = f"{test_name:<18}"
        for pn in period_names:
            m = all_results.get(test_name, {}).get(pn, {})
            ret = m.get('total_return', 'N/A')
            row += f" {ret:>10}"
        print(row)

    # --- MDD ---
    print("\n")
    print("=" * 90)
    print("彙整結果 — Max Drawdown")
    print("=" * 90)
    print(header)
    print("-" * 90)
    for test_name, _ in tests_to_run:
        row = f"{test_name:<18}"
        for pn in period_names:
            m = all_results.get(test_name, {}).get(pn, {})
            mdd = m.get('mdd', 'N/A')
            row += f" {mdd:>10}"
        print(row)

    # --- Win Rate ---
    print("\n")
    print("=" * 90)
    print("彙整結果 — Win Rate")
    print("=" * 90)
    print(header)
    print("-" * 90)
    for test_name, _ in tests_to_run:
        row = f"{test_name:<18}"
        for pn in period_names:
            m = all_results.get(test_name, {}).get(pn, {})
            wr = m.get('win_rate', 'N/A')
            row += f" {wr:>10}"
        print(row)


def main():
    parser = argparse.ArgumentParser(description="參數敏感度測試")
    parser.add_argument('group', choices=['industry', 'stoploss', 'sell', 'all'],
                        help="測試組: industry|stoploss|sell|all")
    parser.add_argument('--period', type=str, default=None,
                        help="指定區間，逗號分隔 (例: P1,P3,P7)。省略=全部")
    args = parser.parse_args()

    # Select test group
    groups = {
        'industry': [t for t in TESTS if '產業' in t[0]],
        'stoploss': [t for t in TESTS if '停損' in t[0]],
        'sell':     [t for t in TESTS if 'TP1' in t[0]],
        'all':      TESTS,
    }
    tests_to_run = groups[args.group]

    # Always include baseline
    baseline = ("基準(現行)", {})
    if baseline not in tests_to_run:
        tests_to_run = [baseline] + tests_to_run

    # Select periods
    if args.period:
        selected = [p.strip().upper() for p in args.period.split(',')]
        invalid = [p for p in selected if p not in PERIODS]
        if invalid:
            print(f"錯誤: 無效區間 {invalid}")
            print(f"可用: {list(PERIODS.keys())}")
            sys.exit(1)
    else:
        selected = list(PERIODS.keys())

    n_runs = len(tests_to_run) * len(selected)
    print("=" * 90)
    print(f"參數敏感度測試: {args.group}")
    print(f"區間: {', '.join(selected)}")
    print(f"測試組數: {len(tests_to_run)} × {len(selected)} 區間 = {n_runs} 次回測")
    print("=" * 90)

    # Load data once
    dm = DataManager()
    dm.load_all()

    # Run all combinations
    all_results = {}
    for test_name, overrides in tests_to_run:
        all_results[test_name] = {}
        print(f"\n{'='*70}")
        print(f">>> {test_name}  {overrides if overrides else '(預設參數)'}")
        print(f"{'='*70}")

        for period_key in selected:
            period_name, start, end = PERIODS[period_key]
            print(f"  {period_name}...", end="", flush=True)
            metrics = run_one(dm, f"{test_name}_{period_name}", start, end, overrides)
            if metrics:
                all_results[test_name][period_name] = metrics
                pf = metrics['pf']
                ret = metrics['total_return']
                print(f" PF={pf}  Return={ret}  MDD={metrics['mdd']}")
            else:
                print(" FAILED")

    # Print summary
    print_summary_tables(tests_to_run, selected, all_results)


if __name__ == '__main__':
    main()
