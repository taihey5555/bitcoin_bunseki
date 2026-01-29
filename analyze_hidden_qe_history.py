"""
隠れQE（日本経由）シグナルの過去データ分析

Arthur Hayes "Japanese QE Thesis" の判定ロジックを過去データに適用し、
OFF→ON に切り替わった日付（重要イベント）を抽出する。

使用方法:
    python analyze_hidden_qe_history.py

出力:
    - OFF→ON 転換日の一覧
    - 各転換日の条件詳細
"""

import os
import sys
import io

# Windows環境でのUTF-8出力対応
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import requests
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf

# 設定をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config


def fetch_fred_series(series_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    """FREDから時系列データを取得"""
    if config.FRED_API_KEY == "YOUR_FRED_API_KEY_HERE" or not config.FRED_API_KEY:
        print(f"⚠️ FRED APIキーが未設定です")
        return pd.DataFrame()

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": config.FRED_API_KEY,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": end_date,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            observations = data.get("observations", [])
            df = pd.DataFrame(observations)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                df = df.dropna(subset=["value"])
                df = df.set_index("date")
                return df[["value"]]
    except Exception as e:
        print(f"❌ FRED取得エラー ({series_id}): {e}")

    return pd.DataFrame()


def fetch_usdjpy_history(start_date: str, end_date: str) -> pd.DataFrame:
    """Yahoo FinanceからUSDJPYの履歴を取得"""
    try:
        ticker = yf.Ticker("USDJPY=X")
        df = ticker.history(start=start_date, end=end_date, interval="1wk")
        if not df.empty:
            df = df[["Close"]].rename(columns={"Close": "value"})
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df
    except Exception as e:
        print(f"❌ USDJPY取得エラー: {e}")

    return pd.DataFrame()


def calculate_weekly_metrics(df: pd.DataFrame, lookback_weeks: int = 52) -> pd.DataFrame:
    """週次変化率、z-scoreなどを計算"""
    if df.empty:
        return df

    df = df.copy()
    # 週次変化率
    df["change_pct"] = df["value"].pct_change() * 100
    # 絶対変化量
    df["change_abs"] = df["value"].diff()
    # 52週移動平均と標準偏差
    df["mean_52w"] = df["value"].rolling(window=lookback_weeks, min_periods=10).mean()
    df["std_52w"] = df["value"].rolling(window=lookback_weeks, min_periods=10).std()
    # z-score
    df["zscore"] = (df["value"] - df["mean_52w"]) / df["std_52w"].replace(0, float("nan"))

    return df


def evaluate_hidden_qe_conditions(
    walcl_row, swpt_row, treast_row, usdjpy_row
) -> dict:
    """
    指定された週のデータに対して隠れQE判定を実行

    Returns:
        dict: {
            "signal": "ON" | "WATCH" | "OFF",
            "score": int (0-4),
            "conditions": {
                "total_assets": bool,
                "treasury": bool,
                "swaps": bool,
                "usdjpy": bool
            },
            "details": {...}
        }
    """
    score = 0
    conditions = {
        "total_assets": False,
        "treasury": False,
        "swaps": False,
        "usdjpy": False,
    }
    details = {}

    # 条件1: Total Assets（WALCL）> +0.1%
    if walcl_row is not None and pd.notna(walcl_row.get("change_pct")):
        change = walcl_row["change_pct"]
        met = change > config.TOTAL_ASSETS_INCREASE_THRESHOLD
        conditions["total_assets"] = met
        details["total_assets"] = {
            "value": walcl_row["value"],
            "change": change,
            "met": met
        }
        if met:
            score += 1

    # 条件2: Treasury Holdings（TREAST）< +0.5%
    if treast_row is not None and pd.notna(treast_row.get("change_pct")):
        change = treast_row["change_pct"]
        met = change < config.TREASURY_HOLDINGS_INCREASE_THRESHOLD
        conditions["treasury"] = met
        details["treasury"] = {
            "value": treast_row["value"],
            "change": change,
            "met": met
        }
        if met:
            score += 1

    # 条件3: Central Bank Swaps（SWPT）急増
    if swpt_row is not None and pd.notna(swpt_row.get("change_pct")):
        change_pct = swpt_row["change_pct"]
        value = swpt_row["value"]
        value_b = value / 1000 if value else 0  # 百万ドル→10億ドル
        change_abs = swpt_row.get("change_abs", 0) or 0
        change_abs_b = change_abs / 1000
        zscore = swpt_row.get("zscore", 0) or 0

        met = False
        # 条件A: 週次% >= 10% かつ 週次増加額 >= 5B（かつ値が1B以上）
        if value_b >= config.SWAPS_MINIMUM_VALUE:
            if (change_pct >= config.SWAPS_SURGE_THRESHOLD_PCT and
                change_abs_b >= config.SWAPS_SURGE_THRESHOLD_ABS):
                met = True

        # 条件B: z-score >= 2.0
        if zscore >= config.SWAPS_SURGE_ZSCORE_THRESHOLD:
            met = True

        conditions["swaps"] = met
        details["swaps"] = {
            "value": value,
            "value_b": value_b,
            "change_pct": change_pct,
            "change_abs_b": change_abs_b,
            "zscore": zscore,
            "met": met
        }
        if met:
            score += 1

    # 条件4: USDJPY 円安/介入局面
    if usdjpy_row is not None and pd.notna(usdjpy_row.get("change_pct")):
        change = usdjpy_row["change_pct"]
        value = usdjpy_row["value"]
        volatility = abs(change)

        met = False
        # 条件A: 円安進行
        if change >= config.USDJPY_WEAKENING_THRESHOLD:
            met = True
        # 条件B: 高水準 & 高ボラ
        elif (value >= config.USDJPY_HIGH_LEVEL and
              volatility >= config.USDJPY_HIGH_VOLATILITY):
            met = True

        conditions["usdjpy"] = met
        details["usdjpy"] = {
            "value": value,
            "change": change,
            "volatility": volatility,
            "met": met
        }
        if met:
            score += 1

    # シグナル判定
    if score >= config.HIDDEN_QE_SIGNAL_ON:
        signal = "ON"
    elif score >= config.HIDDEN_QE_SIGNAL_WATCH:
        signal = "WATCH"
    else:
        signal = "OFF"

    return {
        "signal": signal,
        "score": score,
        "conditions": conditions,
        "details": details
    }


def analyze_hidden_qe_history(years: int = 5):
    """
    過去データを分析し、OFF→ON転換日を抽出

    Args:
        years: 分析対象期間（年数）
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    print(f"📊 隠れQEシグナル履歴分析")
    print(f"   期間: {start_str} ~ {end_str}")
    print()

    # データ取得
    print("📥 データ取得中...")
    walcl = fetch_fred_series("WALCL", start_str, end_str)
    swpt = fetch_fred_series("SWPT", start_str, end_str)
    treast = fetch_fred_series("TREAST", start_str, end_str)
    usdjpy = fetch_usdjpy_history(start_str, end_str)

    if walcl.empty or swpt.empty or treast.empty or usdjpy.empty:
        print("❌ データ取得に失敗しました。FRED_API_KEYを確認してください。")
        return []

    print(f"   WALCL: {len(walcl)}件, SWPT: {len(swpt)}件, TREAST: {len(treast)}件, USDJPY: {len(usdjpy)}件")

    # 週次メトリクス計算
    walcl = calculate_weekly_metrics(walcl)
    swpt = calculate_weekly_metrics(swpt)
    treast = calculate_weekly_metrics(treast)
    usdjpy = calculate_weekly_metrics(usdjpy)

    # 全データを週次でリサンプリング（FREDは週次、USDJPYも週次に）
    # 共通の日付インデックスを作成
    all_dates = sorted(set(walcl.index) | set(swpt.index) | set(treast.index))

    # 各週について判定を実行
    print()
    print("🔍 判定実行中...")
    results = []

    for date in all_dates:
        # 各データソースから該当日またはそれ以前の最新データを取得
        walcl_row = walcl.loc[:date].iloc[-1].to_dict() if date in walcl.index or len(walcl.loc[:date]) > 0 else None
        swpt_row = swpt.loc[:date].iloc[-1].to_dict() if date in swpt.index or len(swpt.loc[:date]) > 0 else None
        treast_row = treast.loc[:date].iloc[-1].to_dict() if date in treast.index or len(treast.loc[:date]) > 0 else None

        # USDJPYは最も近い日付を探す
        usdjpy_row = None
        if not usdjpy.empty:
            closest_idx = usdjpy.index.get_indexer([date], method="nearest")[0]
            if closest_idx >= 0 and closest_idx < len(usdjpy):
                usdjpy_row = usdjpy.iloc[closest_idx].to_dict()

        result = evaluate_hidden_qe_conditions(walcl_row, swpt_row, treast_row, usdjpy_row)
        result["date"] = date
        results.append(result)

    # OFF→ON 転換日を抽出
    print()
    print("=" * 70)
    print("🎯 OFF→ON 転換日（重要イベント）")
    print("=" * 70)

    transitions = []
    prev_signal = "OFF"

    for r in results:
        if prev_signal != "ON" and r["signal"] == "ON":
            transitions.append(r)
            date_str = r["date"].strftime("%Y-%m-%d")
            cond = r["conditions"]
            cond_str = ", ".join([
                f"Assets:{'+' if cond['total_assets'] else '-'}",
                f"Treasury:{'+' if cond['treasury'] else '-'}",
                f"Swaps:{'+' if cond['swaps'] else '-'}",
                f"USDJPY:{'+' if cond['usdjpy'] else '-'}"
            ])
            print(f"  📅 {date_str}  Score: {r['score']}/4  [{cond_str}]")

        prev_signal = r["signal"]

    print()
    print(f"合計: {len(transitions)}件のOFF→ON転換")
    print()

    # WATCH→ON も参考として表示
    print("=" * 70)
    print("📋 WATCH→ON 転換日（参考）")
    print("=" * 70)

    watch_to_on = []
    prev_signal = "OFF"
    for r in results:
        if prev_signal == "WATCH" and r["signal"] == "ON":
            watch_to_on.append(r)
            date_str = r["date"].strftime("%Y-%m-%d")
            print(f"  📅 {date_str}  Score: {r['score']}/4")
        prev_signal = r["signal"]

    print(f"合計: {len(watch_to_on)}件")

    # 詳細結果をCSVに出力
    output_file = "hidden_qe_history.csv"
    df_results = pd.DataFrame([
        {
            "date": r["date"].strftime("%Y-%m-%d"),
            "signal": r["signal"],
            "score": r["score"],
            "total_assets": r["conditions"]["total_assets"],
            "treasury": r["conditions"]["treasury"],
            "swaps": r["conditions"]["swaps"],
            "usdjpy": r["conditions"]["usdjpy"],
        }
        for r in results
    ])
    df_results.to_csv(output_file, index=False)
    print()
    print(f"📁 詳細結果を {output_file} に保存しました")

    return transitions


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="隠れQEシグナルの過去データ分析")
    parser.add_argument("--years", type=int, default=5, help="分析対象期間（年数、デフォルト: 5）")
    parser.add_argument("--api-key", type=str, help="FRED APIキー（環境変数FRED_API_KEYでも指定可）")
    args = parser.parse_args()

    # コマンドライン引数でAPIキーを指定した場合は上書き
    if args.api_key:
        config.FRED_API_KEY = args.api_key
        print(f"✅ FRED APIキーをコマンドライン引数から設定しました")

    # 環境変数を再チェック
    if os.environ.get("FRED_API_KEY"):
        config.FRED_API_KEY = os.environ["FRED_API_KEY"]
        print(f"✅ FRED APIキーを環境変数から設定しました")

    transitions = analyze_hidden_qe_history(years=args.years)
