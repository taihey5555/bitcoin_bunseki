"""
設定ファイル

APIキー、シグナルの閾値、その他の設定を管理します。
"""

import os

# ============================================
# APIキー（環境変数から取得）
# ============================================
# FRED (Federal Reserve Economic Data)
# 取得先: https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_FRED_API_KEY_HERE")

# CoinGlass API (使用しない - GitHub Actionsでスクレイピング)
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "")




# ============================================
# 全体設定
# ============================================
# Yahoo Financeからデータを取得する際のユーザーエージェント
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

# ETFフローデータのGist URL（GitHub Actionsで更新）
# 形式: https://gist.githubusercontent.com/{user}/{gist_id}/raw/etf_flow.json
ETF_GIST_URL = os.environ.get("ETF_GIST_URL", "")

# BTC ETFのシンボルリスト
ETF_SYMBOLS = ["IBIT", "FBTC", "GBTC", "ARKB", "BITB"]


# ============================================
# レポート・ダッシュボードのシグナル閾値
# ============================================

# --- USD流動性 (単位: 百万ドル) ---
LIQUIDITY_BULLISH_STRONG = 6_000_000  # これ以上で強気(x2)
LIQUIDITY_BULLISH_WEAK = 5_500_000   # これ以上で強気(x1)
LIQUIDITY_BEARISH_STRONG = 5_000_000  # これ以下で弱気(x2)

# --- DXY (ドル指数) ---
DXY_BEARISH_STRONG = 110  # これ以上で弱気(x2)
DXY_BEARISH_WEAK = 105    # これ以上で弱気(x1)
DXY_BULLISH_STRONG = 100  # これ以下で強気(x2)

# --- 取引所フロー (単位: BTC) ---
EXCHANGE_NET_FLOW_BULLISH_STRONG = 5000  # 純流出がこれ以上で強気(x2)
EXCHANGE_NET_FLOW_BULLISH_WEAK = 0       # 純流出(>0)で強気(x1)
EXCHANGE_NET_FLOW_BEARISH_STRONG = -5000 # 純流入がこれ以上で弱気(x2)
# ※ net_flow > 0 は流出, < 0 は流入

# --- ETF出来高 (単位: USD) ---
ETF_VOLUME_BULLISH = 5_000_000_000  # 50億ドル以上で強気

# --- ETFフロー (単位: 百万USD) ---
ETF_FLOW_BULLISH_STRONG = 500   # 5億ドル以上の純流入で強気(x2)
ETF_FLOW_BULLISH_WEAK = 100     # 1億ドル以上の純流入で強気(x1)
ETF_FLOW_BEARISH_WEAK = -100    # 1億ドル以上の純流出で弱気(x1)
ETF_FLOW_BEARISH_STRONG = -500  # 5億ドル以上の純流出で弱気(x2)

# --- RSI ---
RSI_OVERSOLD = 30  # これ以下で売られすぎ (強気 x2)
RSI_OVERBOUGHT = 70 # これ以上で買われすぎ (弱気 x2)

# --- ファンディングレート (%) ---
FUNDING_RATE_OVERHEAT = 0.1   # これ以上で過熱 (弱気)
FUNDING_RATE_COOLING = -0.1  # これ以下で冷却 (強気)

# --- OI (建玉) 変化率 (%) ---
OI_CHANGE_THRESHOLD = 10  # 24時間での変動率が±これ以上でシグナル

# --- Fear & Greed Index ---
FEAR_GREED_EXTREME_FEAR = 25  # これ以下で極度の恐怖 (強気 x2)
FEAR_GREED_FEAR = 40          # これ以下で恐怖 (強気 x1)
FEAR_GREED_EXTREME_GREED = 75 # これ以上で極度の貪欲 (弱気 x2)
FEAR_GREED_GREED = 60         # これ以上で貪欲 (弱気 x1)

# --- Gold 変動率 (%) ---
GOLD_CHANGE_THRESHOLD = 1.0 # 1日の変動率が±これ以上でシグナル

# ============================================
# 隠れQE（日本経由）シグナル判定閾値
# Arthur Hayes "Japanese QE Thesis" に基づく
# ============================================

# --- Central Bank Swaps 急増判定 (前週比%) ---
# FRBが海外中銀にドルを供給する際に増加
# 10%以上の週次増加で「急増」と判定
SWAPS_SURGE_THRESHOLD = 10.0

# --- USDJPY 円安判定 (前週比%) ---
# 1%以上の上昇で「円安方向に動いている」と判定
USDJPY_WEAKENING_THRESHOLD = 1.0

# --- Total Assets 増加判定 (前週比%) ---
# 0.1%以上の増加で「増加している」と判定
TOTAL_ASSETS_INCREASE_THRESHOLD = 0.1

# --- Treasury Holdings 増加判定 (前週比%) ---
# 0.5%以上の増加で「国内QE活発」と判定（逆に、これ以下なら「目立たない」）
TREASURY_HOLDINGS_INCREASE_THRESHOLD = 0.5

# --- 隠れQEシグナル判定スコア閾値 ---
# 4条件の合計スコアで判定
HIDDEN_QE_SIGNAL_ON = 4       # 4点: ON（強いシグナル）
HIDDEN_QE_SIGNAL_WATCH = 2    # 2-3点: WATCH（注視）
# 0-1点: OFF（シグナルなし）

