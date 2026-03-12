"""
Strategy Configuration - All parameters in one place.
Three-Layer Protection System for Taiwan Stock Satellite Trading.
"""
import os

# ============================================================
# Capital Allocation
# ============================================================
INITIAL_CAPITAL = 1_000_000
CORE_RATIO = 0.70       # 70% for ETF
SATELLITE_RATIO = 0.30  # 30% for individual stocks

# Core ETF
CORE_ETF = '0050'
BENCHMARK_ETF = '0050'  # for comparison

# ============================================================
# Step 1: Screening Parameters (Weekly)
# ============================================================
MIN_AVG_VOLUME = 500          # 日均成交量 > 500 張 (unit: 張 = 1000 shares)
VOLUME_AVG_DAYS = 20          # 20-day average for volume screening
REVENUE_GROWTH_THRESHOLD = 0.10  # 營收年增率 > 10%
REVENUE_MONTHS = 3            # 近 3 月平均
REVENUE_LAG_DAYS = 41         # 營收公布延遲 (month+1 的 10 號後可用)

# Relative Strength (RS) filter - replaces revenue when unavailable
RS_ENABLED = True             # 啟用 RS 排名篩選
RS_PERIOD_SHORT = 60          # 近 60 日 (3 個月) 報酬
RS_PERIOD_LONG = 120          # 近 120 日 (6 個月) 報酬
RS_MIN_THRESHOLD = 1.0        # RS > 1.0 = 跑贏大盤才留下
RS_WEIGHT_SHORT = 0.6         # 近期 RS 權重較高
RS_WEIGHT_LONG = 0.4          # 遠期 RS 權重

# Industry filter (user-configurable)
# 留空 = 不限產業; 填入產業名稱 = 只交易這些產業
# 範例: ['半導體業', '電子工業', '電子零組件業', '光電業', '通信網路業']
ALLOWED_INDUSTRIES = []       # 白名單（留空=全部）
# 範例: ['食品工業', '紡織纖維', '造紙工業', '水泥工業']
EXCLUDED_INDUSTRIES = []      # 黑名單

# ============================================================
# Step 2: Position Sizing
# ============================================================
MAX_POSITIONS = 5             # 最多同時持有 5 檔
MAX_POSITION_SIZE = 60_000    # 單檔上限 6 萬 (含加碼)
MAX_EXPOSURE = 240_000        # 總曝險上限 24 萬 (衛星30萬的80%)
MIN_CASH_RESERVE = 60_000     # 現金預備 ≥ 6 萬
MAX_SAME_INDUSTRY = 2         # 同產業最多 2 檔

# ============================================================
# Step 3: Entry - Strategy A (Pullback Buy)
# ============================================================
MA_SHORT = 20                 # 短均線 (月線)
MA_LONG = 60                  # 長均線 (季線)
RSI_PERIOD = 14
RSI_ENTRY_LOW = 30            # RSI 下限 (< 30 可能還在崩)
RSI_ENTRY_HIGH = 65           # RSI 上限 (> 65 拉回不夠深)
MA_TOUCH_TOLERANCE = 0.01     # 股價碰 20MA 容忍度 ±1%
VOLUME_SURGE_RATIO = 1.3      # 止跌日成交量 > 5日均量 × 此比率 (1.3=量能放大30%)

# K-line Pattern (Method B)
LOWER_SHADOW_RATIO = 2.0      # 下影線 > 實體 × 2
ENGULFING_ENABLED = True       # 吞噬紅K辨識
CONSECUTIVE_ABOVE_MA = 2      # 連續N日收盤站回20MA

# 60MA slope check
MA_LONG_SLOPE_DAYS = 5        # 用最近N日判斷60MA是否向上

# ============================================================
# Step 3: Pyramiding
# ============================================================
PYRAMID_ENABLED = True          # 搭配 RS 篩選，加碼強勢股 (A/B測試 PF=1.05)
INITIAL_POSITION_SIZE = 40_000  # 初始倉位 4 萬 (資金利用率 33%→67%)
PYRAMID_1_THRESHOLD = 0.08     # +8% 加碼第一次
PYRAMID_1_SIZE = 20_000        # 加碼 2 萬
MAX_PYRAMIDS = 1               # 最多加碼 1 次 (40K+20K=60K=MAX_POSITION_SIZE，第二次永遠不會觸發)
# PYRAMID_2 已移除：40K初始+20K加碼=60K已達MAX_POSITION_SIZE上限

# ============================================================
# Step 4: Risk Control
# ============================================================
# First line: per-trade stop loss
ATR_STOP_ENABLED = False         # ATR 停損已驗證無效（寬停損讓輸家活太久，P3 +109K→+71K）
ATR_STOP_MULTIPLIER = 2.0       # (保留參數供未來參考)
ATR_STOP_MIN = 0.03
ATR_STOP_MAX = 0.15
MAX_LOSS_PER_TRADE_RATIO = 0.01  # 單筆虧損 ≤ 總資金 1%
INITIAL_STOP_LOSS_PCT = 0.05     # 固定停損 -5%（配合 grace period 效果最佳）

# Second line: trailing stop
TRAILING_STOP_METHOD = '20ma'    # '20ma' or 'pct'
TRAILING_STOP_PCT = 0.10         # 固定百分比回落 (if pct method)
TRAILING_STOP_CONFIRM_DAYS = 2   # 跌破 20MA 需連續 N 天才觸發 (讓贏家跑久一點)

# Take profit (partial)
TAKE_PROFIT_1_PCT = 0.10   # +10% 賣 1/3
TAKE_PROFIT_2_PCT = 0.20   # +20% 賣 1/3

# Third line: portfolio-level risk
MONTHLY_LOSS_PAUSE = 12_000     # 月虧 > 1.2萬 暫停新倉 (原3萬太寬鬆，P3 2024-01月虧1.8萬才觸發)
MONTHLY_LOSS_CRITICAL = 50_000  # 月虧 > 5萬 緊停損
CONSECUTIVE_LOSS_PAUSE = 3      # 連虧 3 筆暫停
PAUSE_TRADING_DAYS = 10         # 暫停 10 個交易日 (≈2 週)

# Market regime filter (TAIEX vs 60MA)
MARKET_FILTER_ENABLED = True    # 大盤跌破 60MA → 清倉衛星

# Industry filter — 靜態黑名單已驗證無效（P3 PF 1.97→1.01），多頭期贏家也被排除
# 保留 set 結構供未來動態篩選使用
INDUSTRY_BLACKLIST = set()

# Re-entry rules
REENTRY_COOLDOWN_DAYS = 10      # 同檔停損後等 10 個交易日
MAX_STOPOUTS_PER_STOCK = 2      # 同檔最多被停損 2 次就移除

# ============================================================
# SGX Futures Sentiment (富台指數) - DISABLED: CSV是日盤非夜盤，A/B測試零影響
# ============================================================
SGX_ENABLED = False             # 關閉 SGX (日盤數據無預測力)
SGX_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'SGX_20220101_20260311.csv')
SGX_BULL_THRESHOLD = 1.5       # SGX > +1.5% → 進場加分 (確認多頭)
SGX_PANIC_THRESHOLD = -3.0     # SGX < -3% → 暫緩賣出 (均值回歸)
SGX_EXTREME_THRESHOLD = -5.0   # SGX < -5% → 真恐慌，啟動防禦

# ============================================================
# EWT Overnight Sentiment (iShares MSCI Taiwan ETF)
# ============================================================
# EWT 在美股交易，收盤時間 = 台灣凌晨 4~5 點
# 用 EWT 前一晚漲跌% 判斷隔天台股開盤情緒
EWT_ENABLED = True              # 啟用 EWT 夜盤情緒
EWT_BULL_THRESHOLD = 1.5        # EWT > +1.5% → 進場加分 (priority ×1.2)
EWT_PANIC_THRESHOLD = -3.0      # EWT < -3% → 暫緩加碼
EWT_EXTREME_THRESHOLD = -5.0    # EWT < -5% → 真恐慌，阻擋進場
# Bull 放寬已驗證無效：多開1檔+量能1.1 → P1 PF 1.27→1.02，品質差的股票拖累績效

# ============================================================
# Transaction Costs (元大先生 零股)
# ============================================================
COMMISSION_RATE = 0.001425 * 0.6  # 0.1425% × 6折 = 0.0855%
MIN_COMMISSION = 1                # 零股最低 NT$1
STOCK_TAX_RATE = 0.003            # 個股證交稅 0.3%
ETF_TAX_RATE = 0.001              # ETF 證交稅 0.1%

# ============================================================
# Execution
# ============================================================
EXECUTION_PRICE = 'close'  # 收盤執行（拉回買策略適合收盤；開盤有gap反而更差，P3 1.97→0.81）
SLIPPAGE_PCT = 0.001       # 0.1% 滑價（元大先生零股收盤撮合，價差極小）

# ============================================================
# Core ETF - DCA (定期不定額)
# ============================================================
CORE_DCA_BASE = 50_000       # 每月基準額 5 萬
CORE_DCA_MAX = 80_000        # 單月最多投 8 萬
CORE_DCA_MIN = 20_000        # 單月最少投 2 萬
CORE_DCA_DAY = 1             # 每月 1 號 (遇假日順延)

# ============================================================
# Data Settings
# ============================================================
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
FINMIND_TOKEN = ''  # Optional: register at finmindtrade.com for higher rate limit

# Data download range (includes warmup period for MA calculation)
DATA_START_DATE = '2020-01-01'
DATA_END_DATE = '2026-12-31'  # 設遠一點，避免每次手動改
