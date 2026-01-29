"""
BTCシグナルダッシュボード用 バックエンドサーバー
- Flaskを使用して、ダッシュボードのHTMLを配信する
- /api/data エンドポイントで、最新の市場データをJSON形式で提供する
"""

import asyncio
import aiohttp
import json
from flask import Flask, jsonify, render_template, request
from datetime import datetime

# 共通モジュールからインポート
import data_provider
import config

# Flaskアプリケーションの初期化
app = Flask(__name__)
CACHE_FILE = "latest_successful_data.json"

# 起動時のデバッグログ
print(f"🔑 FRED_API_KEY: {'設定済み (' + config.FRED_API_KEY[:4] + '...)' if config.FRED_API_KEY and config.FRED_API_KEY != 'YOUR_FRED_API_KEY_HERE' else '未設定'}")
print(f"🔑 ETF_GIST_URL: {'設定済み' if config.ETF_GIST_URL else '未設定'}")


async def fetch_all_data():
    """全データを非同期で並列取得"""
    async with aiohttp.ClientSession() as session:
        tasks = [
            data_provider.get_fred_data(session, "WALCL"),
            data_provider.get_fred_data(session, "RRPONTSYD"),
            data_provider.get_fred_data(session, "WTREGEN"),
            data_provider.get_dxy(session),
            data_provider.get_exchange_flow(session),
            data_provider.get_macro_data(session),
            data_provider.get_btc_price(session),
            data_provider.get_fear_greed_index(session),
            data_provider.get_funding_rate(session),
            data_provider.get_etf_flow(session),
            # 隠れQE判定用データ（週次変化率付き）
            data_provider.get_fred_data_with_change(session, "WALCL"),   # Total Assets
            data_provider.get_fred_data_with_stats(session, "SWPT"),     # Central Bank Swaps（統計付き）
            data_provider.get_fred_data_with_change(session, "TREAST"),  # Treasury Holdings
            data_provider.get_usdjpy(session),                           # USDJPY
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return results


def calculate_hidden_qe_signal(walcl_data, swpt_data, treast_data, usdjpy_data):
    """
    隠れQE（日本経由）シグナルを計算（精度向上版 v2）

    Arthur Hayes "Japanese QE Thesis":
    FRBが直接的なQEを行わずに、日本市場を経由して
    ドル流動性を供給している兆候を検出

    判定条件:
    1. Total Assets（WALCL）> +0.1% → FRB資産拡大中
    2. Treasury Holdings（TREAST）< +0.5% → 国内QE非活発
    3. Central Bank Swaps（SWPT）急増（複合条件）:
       - 条件A: 週次% >= 10% かつ 週次増加額 >= 5B
       - 条件B: z-score >= 2.0（過去52週から2σ超の異常値）
       - 注: 値が1B未満の場合は週次%のみでは成立させない
    4. USDJPY 円安/介入局面:
       - 条件A: 週次変化 >= +1%（円安進行）
       - 条件B: USDJPY >= 150 かつ ボラ >= 1.5%（高水準&高ボラ）

    判定結果:
    - 4条件成立: ON（強気シグナル）
    - 2-3条件成立: WATCH（注視）
    - 0-1条件成立: OFF（シグナルなし）
    """
    score = 0
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # 閾値情報（フロントエンド表示用）
    thresholds = {
        "total_assets": f"> +{config.TOTAL_ASSETS_INCREASE_THRESHOLD}%",
        "treasury": f"< +{config.TREASURY_HOLDINGS_INCREASE_THRESHOLD}%",
        "swaps": f"週次% >= {config.SWAPS_SURGE_THRESHOLD_PCT}% & 増加額 >= {config.SWAPS_SURGE_THRESHOLD_ABS}B | z-score >= {config.SWAPS_SURGE_ZSCORE_THRESHOLD}",
        "usdjpy": f"円安 >= +{config.USDJPY_WEAKENING_THRESHOLD}% | (>={config.USDJPY_HIGH_LEVEL} & ボラ >= {config.USDJPY_HIGH_VOLATILITY}%)"
    }

    # 各条件の詳細情報を初期化
    details = {
        "total_assets": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.TOTAL_ASSETS_INCREASE_THRESHOLD,
            "met": False,
            "reason": "FREDからデータを取得できませんでした",
            "indicators": ["週次%"]
        },
        "treasury": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.TREASURY_HOLDINGS_INCREASE_THRESHOLD,
            "met": False,
            "reason": "FREDからデータを取得できませんでした",
            "indicators": ["週次%"]
        },
        "swaps": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.SWAPS_SURGE_THRESHOLD_PCT,
            "met": False,
            "reason": "FREDからデータを取得できませんでした",
            "indicators": ["週次%", "増加額", "z-score"]
        },
        "usdjpy": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.USDJPY_WEAKENING_THRESHOLD,
            "met": False,
            "reason": "Yahoo Financeからデータを取得できませんでした",
            "indicators": ["週次%", "水準", "ボラ"]
        },
    }

    # =========================================
    # 条件1: Total Assets（WALCL）が前週比で増加
    # 判定: change > 0.1% で「FRB資産拡大中」
    # =========================================
    if walcl_data and walcl_data.get("change") is not None:
        change = walcl_data["change"]
        threshold = config.TOTAL_ASSETS_INCREASE_THRESHOLD
        met = change > threshold

        if met:
            score += 1
            reason = f"前週比 {change:+.2f}% > {threshold}% → FRB資産拡大中"
            status = "増加"
        else:
            reason = f"前週比 {change:+.2f}% <= {threshold}% → 資産横ばい/減少"
            status = "横ばい/減少"

        details["total_assets"] = {
            "status": status,
            "value": walcl_data["value"],
            "change": change,
            "threshold": threshold,
            "met": met,
            "reason": reason,
            "date": walcl_data.get("date"),
            "indicators": ["週次%"]
        }

    # =========================================
    # 条件2: Treasury Holdings（TREAST）が横ばいまたは減少
    # 判定: change < 0.5% で「国内QE非活発」
    # =========================================
    if treast_data and treast_data.get("change") is not None:
        change = treast_data["change"]
        threshold = config.TREASURY_HOLDINGS_INCREASE_THRESHOLD
        met = change < threshold

        if met:
            score += 1
            reason = f"前週比 {change:+.2f}% < {threshold}% → 国内QE非活発"
            status = "非活発"
        else:
            reason = f"前週比 {change:+.2f}% >= {threshold}% → 国内QE活発"
            status = "活発"

        details["treasury"] = {
            "status": status,
            "value": treast_data["value"],
            "change": change,
            "threshold": threshold,
            "met": met,
            "reason": reason,
            "date": treast_data.get("date"),
            "indicators": ["週次%"]
        }

    # =========================================
    # 条件3: Central Bank Swaps（SWPT）急増（複合条件）
    # ノイズ耐性向上:
    # - 条件A: 週次% >= 10% かつ 週次増加額 >= 5B
    # - 条件B: z-score >= 2.0（過去52週から2σ超の異常値）
    # - 注: 値が1B未満の場合は週次%のみでは成立させない
    # =========================================
    if swpt_data and swpt_data.get("change") is not None:
        change_pct = swpt_data["change"]
        value = swpt_data["value"]
        value_b = value / 1000 if value else 0  # 百万ドル→10億ドル
        change_abs = swpt_data.get("change_abs", 0)
        change_abs_b = change_abs / 1000 if change_abs else 0  # 百万ドル→10億ドル
        zscore = swpt_data.get("zscore", 0)
        mean_52w = swpt_data.get("mean_52w", 0)
        std_52w = swpt_data.get("std_52w", 0)

        # 複合判定
        met = False
        met_reasons = []

        # 条件A: 週次% >= 10% かつ 週次増加額 >= 5B（かつ値が1B以上）
        pct_threshold = config.SWAPS_SURGE_THRESHOLD_PCT
        abs_threshold = config.SWAPS_SURGE_THRESHOLD_ABS
        min_value = config.SWAPS_MINIMUM_VALUE

        if value_b >= min_value:
            if change_pct >= pct_threshold and change_abs_b >= abs_threshold:
                met = True
                met_reasons.append(f"週次%({change_pct:+.1f}%)&増加額({change_abs_b:+.1f}B)")

        # 条件B: z-score >= 2.0
        zscore_threshold = config.SWAPS_SURGE_ZSCORE_THRESHOLD
        if zscore >= zscore_threshold:
            met = True
            met_reasons.append(f"z-score({zscore:+.2f})が{zscore_threshold}超")

        if met:
            score += 1
            reason = f"急増検出: {', '.join(met_reasons)}"
            status = "急増"
        else:
            # 不成立の理由を詳細に
            reasons = []
            if value_b < min_value:
                reasons.append(f"値が小さい({value_b:.1f}B < {min_value}B)")
            elif change_pct < pct_threshold:
                reasons.append(f"週次%不足({change_pct:+.1f}% < {pct_threshold}%)")
            elif change_abs_b < abs_threshold:
                reasons.append(f"増加額不足({change_abs_b:+.1f}B < {abs_threshold}B)")
            if zscore < zscore_threshold:
                reasons.append(f"z-score({zscore:+.2f}) < {zscore_threshold}")
            reason = f"通常レベル: {', '.join(reasons)}"
            status = "通常"

        details["swaps"] = {
            "status": status,
            "value": value,
            "value_b": value_b,
            "change": change_pct,
            "change_abs_b": change_abs_b,
            "zscore": zscore,
            "mean_52w": mean_52w,
            "std_52w": std_52w,
            "threshold": pct_threshold,
            "threshold_abs": abs_threshold,
            "threshold_zscore": zscore_threshold,
            "met": met,
            "reason": reason,
            "date": swpt_data.get("date"),
            "indicators": ["週次%", "増加額", "z-score"]
        }

    # =========================================
    # 条件4: USDJPY 円安/介入局面（複合条件）
    # - 条件A: 週次変化 >= +1%（円安進行）
    # - 条件B: USDJPY >= 150 かつ ボラ >= 1.5%（高水準&高ボラ）
    # =========================================
    if usdjpy_data and usdjpy_data.get("change") is not None:
        change = usdjpy_data["change"]
        value = usdjpy_data["value"]
        volatility = abs(change)  # ボラティリティ = 変化率の絶対値

        # 複合判定
        met = False
        met_reason = ""

        # 条件A: 円安進行
        if change >= config.USDJPY_WEAKENING_THRESHOLD:
            met = True
            met_reason = f"円安進行: 週次 {change:+.2f}% >= {config.USDJPY_WEAKENING_THRESHOLD}%"
            status = "円安進行"
        # 条件B: 高水準 & 高ボラ（介入警戒局面）
        elif value >= config.USDJPY_HIGH_LEVEL and volatility >= config.USDJPY_HIGH_VOLATILITY:
            met = True
            met_reason = f"介入警戒: {value:.1f} >= {config.USDJPY_HIGH_LEVEL} & ボラ {volatility:.2f}% >= {config.USDJPY_HIGH_VOLATILITY}%"
            status = "介入警戒"
        else:
            if change <= -config.USDJPY_WEAKENING_THRESHOLD:
                met_reason = f"円高進行: 週次 {change:+.2f}%"
                status = "円高進行"
            else:
                met_reason = f"安定推移: 週次 {change:+.2f}%, 水準 {value:.1f}"
                status = "安定"

        if met:
            score += 1

        details["usdjpy"] = {
            "status": status,
            "value": value,
            "change": change,
            "volatility": volatility,
            "threshold": config.USDJPY_WEAKENING_THRESHOLD,
            "threshold_level": config.USDJPY_HIGH_LEVEL,
            "threshold_volatility": config.USDJPY_HIGH_VOLATILITY,
            "met": met,
            "reason": met_reason,
            "indicators": ["週次%", "水準", "ボラ"]
        }

    # =========================================
    # シグナル判定（スコアに基づく3段階判定）
    # =========================================
    if score >= config.HIDDEN_QE_SIGNAL_ON:  # 4点
        signal = "ON"
        explanation = "全4条件成立。日本経由の隠れQEが活発化している可能性が高い。BTCに強気シグナル。"
    elif score >= config.HIDDEN_QE_SIGNAL_WATCH:  # 2-3点
        signal = "WATCH"
        met_conditions = [k for k, v in details.items() if v.get("met")]
        explanation = f"{score}/4条件成立（{', '.join(met_conditions)}）。隠れQEの兆候あり。動向を注視。"
    else:  # 0-1点
        signal = "OFF"
        explanation = f"{score}/4条件のみ成立。現時点で隠れQEの明確な兆候なし。"

    return {
        "signal": signal,
        "score": score,
        "details": details,
        "explanation": explanation,
        "thresholds": thresholds,
        "updated_at": updated_at
    }


@app.route('/')
def dashboard():
    """ダッシュボードのHTMLページを配信する"""
    return render_template('dashboard_pro.html')


@app.route('/liquidity')
def liquidity_page():
    """USD流動性チャートページを配信する"""
    return render_template('liquidity.html')


@app.route('/api/liquidity-history')
def get_liquidity_history():
    """過去1年分のFRED流動性データを取得"""
    import requests
    from datetime import timedelta

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)

    def fetch_fred_series(series_id):
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": config.FRED_API_KEY,
            "file_type": "json",
            "observation_start": start_date.strftime('%Y-%m-%d'),
            "observation_end": end_date.strftime('%Y-%m-%d'),
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {"date": obs["date"], "value": float(obs["value"])}
                    for obs in data.get("observations", [])
                    if obs["value"] != "."
                ]
        except Exception as e:
            print(f"FRED履歴取得エラー ({series_id}): {e}")
        return []

    return jsonify({
        "walcl": fetch_fred_series("WALCL"),
        "rrp": fetch_fred_series("RRPONTSYD"),
        "tga": fetch_fred_series("WTREGEN"),
    })


@app.route('/api/foreign-liquidity-history')
def get_foreign_liquidity_history():
    """
    過去1年分の海外向け流動性データを取得
    隠れQE分析用

    取得データ:
    - SWPT: Central Bank Liquidity Swaps（海外中銀へのドル供給）
    - WALCL: Total Assets（FRB総資産）
    - TREAST: Treasury Holdings（国債保有、国内QE指標）
    """
    import requests
    from datetime import timedelta

    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)

    def fetch_fred_series(series_id):
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": config.FRED_API_KEY,
            "file_type": "json",
            "observation_start": start_date.strftime('%Y-%m-%d'),
            "observation_end": end_date.strftime('%Y-%m-%d'),
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {"date": obs["date"], "value": float(obs["value"])}
                    for obs in data.get("observations", [])
                    if obs["value"] != "."
                ]
        except Exception as e:
            print(f"FRED履歴取得エラー ({series_id}): {e}")
        return []

    return jsonify({
        "swpt": fetch_fred_series("SWPT"),       # Central Bank Swaps
        "walcl": fetch_fred_series("WALCL"),     # Total Assets
        "treast": fetch_fred_series("TREAST"),   # Treasury Holdings
    })


@app.route('/foreign-liquidity')
def foreign_liquidity_page():
    """海外向け流動性（隠れQE）チャートページを配信する"""
    return render_template('foreign_liquidity.html')


@app.route('/arthur-scenario')
def arthur_scenario_page():
    """Arthur Hayes Scenario可視化ページを配信する"""
    return render_template('arthur_scenario.html')


@app.route('/api/arthur-scenario-history')
def get_arthur_scenario_history():
    """
    過去のArthur Scenario（隠れQE）判定結果とOFF→ON転換日を取得

    Returns:
        - btc_prices: BTC価格履歴
        - on_transitions: OFF→ON転換日（重要イベント）
        - weekly_signals: 週次シグナル履歴
    """
    import requests
    import statistics
    from datetime import timedelta

    # 常にJSONを返すためtry/exceptで全体をラップ
    try:
        years = int(request.args.get('years', 3))
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years * 365)

        # FRED APIキーチェック
        if config.FRED_API_KEY == "YOUR_FRED_API_KEY_HERE" or not config.FRED_API_KEY:
            return jsonify({
                "error": "FRED APIキーが未設定です。環境変数 FRED_API_KEY を設定してください。",
                "on_transitions": [],
                "btc_prices": [],
                "weekly_signals": []
            })

        def fetch_fred_series(series_id):
            url = "https://api.stlouisfed.org/fred/series/observations"
            params = {
                "series_id": series_id,
                "api_key": config.FRED_API_KEY,
                "file_type": "json",
                "observation_start": start_date.strftime('%Y-%m-%d'),
                "observation_end": end_date.strftime('%Y-%m-%d'),
            }
            try:
                resp = requests.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    result = {}
                    for obs in data.get("observations", []):
                        if obs["value"] != ".":
                            result[obs["date"]] = float(obs["value"])
                    return result
            except Exception as e:
                print(f"FRED履歴取得エラー ({series_id}): {e}")
            return {}

        # FREDデータ取得
        walcl = fetch_fred_series("WALCL")
        swpt = fetch_fred_series("SWPT")
        treast = fetch_fred_series("TREAST")

        if not walcl or not swpt:
            return jsonify({
                "error": "FREDデータの取得に失敗しました。APIキーを確認してください。",
                "on_transitions": [],
                "btc_prices": [],
                "weekly_signals": []
            })

        # BTC価格履歴取得（Yahoo Finance）
        btc_prices = []
        try:
            import yfinance as yf
            btc = yf.Ticker("BTC-USD")
            hist = btc.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), interval="1d")
            for d, row in hist.iterrows():
                try:
                    # タイムゾーン対応: tz_localize(None)でnaiveに変換
                    date_str = d.tz_localize(None).strftime('%Y-%m-%d') if d.tzinfo else d.strftime('%Y-%m-%d')
                    btc_prices.append({"date": date_str, "price": float(row["Close"])})
                except Exception:
                    pass
        except Exception as e:
            print(f"BTC価格取得エラー: {e}")

        # USDJPY履歴取得
        usdjpy_data = {}
        try:
            import yfinance as yf
            usdjpy = yf.Ticker("USDJPY=X")
            hist = usdjpy.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), interval="1wk")
            prev_value = None
            for d, row in hist.iterrows():
                try:
                    date_str = d.tz_localize(None).strftime('%Y-%m-%d') if d.tzinfo else d.strftime('%Y-%m-%d')
                    value = float(row["Close"])
                    change = ((value - prev_value) / prev_value * 100) if prev_value else 0
                    usdjpy_data[date_str] = {"value": value, "change": change}
                    prev_value = value
                except Exception:
                    pass
        except Exception as e:
            print(f"USDJPY取得エラー: {e}")

        # 週次でシグナル判定
        dates = sorted(set(walcl.keys()) & set(swpt.keys()))
        weekly_signals = []
        on_transitions = []
        prev_signal = "OFF"

        # 52週分のデータでz-score計算用の統計を計算
        swpt_values = [swpt[d] for d in sorted(swpt.keys())]

        for i, date in enumerate(dates):
            try:
                # 前週比計算
                prev_dates = [d for d in dates if d < date]
                prev_date = prev_dates[-1] if prev_dates else None

                walcl_change = ((walcl[date] - walcl[prev_date]) / walcl[prev_date] * 100) if prev_date and prev_date in walcl and walcl[prev_date] != 0 else None
                swpt_change = ((swpt[date] - swpt[prev_date]) / swpt[prev_date] * 100) if prev_date and prev_date in swpt and swpt[prev_date] != 0 else None
                swpt_change_abs = (swpt[date] - swpt[prev_date]) if prev_date and prev_date in swpt else None
                treast_change = ((treast.get(date, 0) - treast.get(prev_date, 0)) / treast.get(prev_date, 1) * 100) if prev_date and treast.get(prev_date) else None

                # z-score計算（直近52週）
                swpt_recent = swpt_values[max(0, i-52):i+1]
                if len(swpt_recent) >= 10:
                    mean_52w = statistics.mean(swpt_recent)
                    std_52w = statistics.stdev(swpt_recent) if len(swpt_recent) > 1 else 1
                    zscore = (swpt[date] - mean_52w) / std_52w if std_52w > 0 else 0
                else:
                    zscore = 0

                # 判定
                score = 0
                conditions = {"total_assets": False, "treasury": False, "swaps": False, "usdjpy": False}

                # 条件1: Total Assets > +0.1%
                if walcl_change is not None and walcl_change > config.TOTAL_ASSETS_INCREASE_THRESHOLD:
                    score += 1
                    conditions["total_assets"] = True

                # 条件2: Treasury < +0.5%
                if treast_change is not None and treast_change < config.TREASURY_HOLDINGS_INCREASE_THRESHOLD:
                    score += 1
                    conditions["treasury"] = True

                # 条件3: Swaps急増
                swpt_value_b = swpt[date] / 1000
                swpt_change_abs_b = (swpt_change_abs / 1000) if swpt_change_abs else 0
                if swpt_value_b >= config.SWAPS_MINIMUM_VALUE:
                    if swpt_change and swpt_change >= config.SWAPS_SURGE_THRESHOLD_PCT and swpt_change_abs_b >= config.SWAPS_SURGE_THRESHOLD_ABS:
                        score += 1
                        conditions["swaps"] = True
                if zscore >= config.SWAPS_SURGE_ZSCORE_THRESHOLD:
                    if not conditions["swaps"]:
                        score += 1
                        conditions["swaps"] = True

                # 条件4: USDJPY
                usdjpy_entry = usdjpy_data.get(date) or {}
                if usdjpy_entry:
                    usdjpy_val = usdjpy_entry.get("value", 0)
                    usdjpy_chg = usdjpy_entry.get("change", 0)
                    if usdjpy_chg >= config.USDJPY_WEAKENING_THRESHOLD:
                        score += 1
                        conditions["usdjpy"] = True
                    elif usdjpy_val >= config.USDJPY_HIGH_LEVEL and abs(usdjpy_chg) >= config.USDJPY_HIGH_VOLATILITY:
                        score += 1
                        conditions["usdjpy"] = True

                # シグナル判定
                if score >= config.HIDDEN_QE_SIGNAL_ON:
                    signal = "ON"
                elif score >= config.HIDDEN_QE_SIGNAL_WATCH:
                    signal = "WATCH"
                else:
                    signal = "OFF"

                weekly_signals.append({
                    "date": date,
                    "signal": signal,
                    "score": score,
                    "conditions": conditions
                })

                # OFF→ON転換を検出
                if prev_signal != "ON" and signal == "ON":
                    on_transitions.append({
                        "date": date,
                        "score": score,
                        "conditions": conditions
                    })

                prev_signal = signal

            except Exception as e:
                print(f"シグナル判定エラー ({date}): {e}")
                continue

        return jsonify({
            "btc_prices": btc_prices,
            "on_transitions": on_transitions,
            "weekly_signals": weekly_signals,
            "period": {"start": start_date.strftime('%Y-%m-%d'), "end": end_date.strftime('%Y-%m-%d')}
        })

    except Exception as e:
        # 予期せぬエラーもJSONで返す（500で落とさない）
        print(f"Arthur Scenario API エラー: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "error": f"サーバーエラー: {str(e)}",
            "on_transitions": [],
            "btc_prices": [],
            "weekly_signals": []
        })


@app.route('/api/data')
def get_data():
    """ダッシュボード用のデータを取得・計算してJSONで返す"""
    try:
        print("📊 /api/data: データ並列取得・計算開始...")
        start_time = datetime.now()

        # 非同期処理を同期的に実行
        results = asyncio.run(fetch_all_data())

        # エラーが発生した場合はNoneを設定
        (balance_sheet, rrp, tga, dxy, ex_flow, macro_yh, btc, fg, fr, etf_flow,
         walcl_weekly, swpt_data, treast_data, usdjpy_data) = [
            res if not isinstance(res, Exception) else None for res in results
        ]

        # BTC価格が取得できない場合は致命的エラーとみなし、フォールバックさせる
        if not btc or not btc.get("usd"):
            raise data_provider.DataProviderError("BTC価格の取得に失敗しました。")

        # --- 計算ロジック ---
        liquidity = None
        if all([balance_sheet, rrp is not None, tga]):
            liquidity = balance_sheet - (rrp * 1000) - tga

        signals = []

        # =====================================================================
        # シグナル生成（各シグナルに available / reason を付与）
        # available=True: データ取得成功、スコア計算に含める
        # available=False: データ欠損、スコア計算から除外（coverageで表示）
        # =====================================================================

        # USD流動性（FRED API必須）
        sig_liquidity = {"name": "USD流動性", "status": "neutral", "weight": 1, "value": "N/A", "available": False, "reason": ""}
        if config.FRED_API_KEY == "YOUR_FRED_API_KEY_HERE" or not config.FRED_API_KEY:
            sig_liquidity["reason"] = "FRED APIキー未設定"
        elif not all([balance_sheet, rrp is not None, tga]):
            sig_liquidity["reason"] = "FRED データ取得失敗"
        elif liquidity:
            sig_liquidity["available"] = True
            sig_liquidity["value"] = f"${liquidity/1e6:.2f}T"
            if liquidity > config.LIQUIDITY_BULLISH_STRONG: sig_liquidity.update({"status": "bullish", "weight": 2})
            elif liquidity > config.LIQUIDITY_BULLISH_WEAK: sig_liquidity["status"] = "bullish"
            elif liquidity < config.LIQUIDITY_BEARISH_STRONG: sig_liquidity.update({"status": "bearish", "weight": 2})
        signals.append(sig_liquidity)

        # DXY（Yahoo Finance）
        sig_dxy = {"name": "DXY", "status": "neutral", "weight": 1, "value": "N/A", "available": False, "reason": ""}
        if dxy and dxy.get("value"):
            sig_dxy["available"] = True
            sig_dxy["value"] = f'{dxy["value"]:.1f}'
            if dxy["value"] > config.DXY_BEARISH_STRONG: sig_dxy.update({"status": "bearish", "weight": 2})
            elif dxy["value"] > config.DXY_BEARISH_WEAK: sig_dxy["status"] = "bearish"
            elif dxy["value"] < config.DXY_BULLISH_STRONG: sig_dxy.update({"status": "bullish", "weight": 2})
        else:
            sig_dxy["reason"] = "Yahoo Finance取得失敗"
        signals.append(sig_dxy)

        # Fear & Greed Index
        sig_fg = {"name": "Fear & Greed", "status": "neutral", "weight": 1, "value": "N/A", "available": False, "reason": ""}
        if fg:
            sig_fg["available"] = True
            sig_fg["value"] = str(fg)
            if fg <= config.FEAR_GREED_EXTREME_FEAR: sig_fg.update({"status": "bullish", "weight": 2})
            elif fg <= config.FEAR_GREED_FEAR: sig_fg["status"] = "bullish"
            elif fg >= config.FEAR_GREED_EXTREME_GREED: sig_fg.update({"status": "bearish", "weight": 2})
            elif fg >= config.FEAR_GREED_GREED: sig_fg["status"] = "bearish"
        else:
            sig_fg["reason"] = "API取得失敗"
        signals.append(sig_fg)

        # 取引所フロー（CoinGlass）
        sig_flow = {"name": "取引所フロー", "status": "neutral", "weight": 1, "value": "N/A", "available": False, "reason": ""}
        if ex_flow and ex_flow.get("net_flow") is not None:
            sig_flow["available"] = True
            flow = ex_flow["net_flow"]
            sig_flow["value"] = f"{flow:+.0f} BTC"
            if flow > config.EXCHANGE_NET_FLOW_BULLISH_STRONG: sig_flow.update({"status": "bullish", "weight": 2})
            elif flow > config.EXCHANGE_NET_FLOW_BULLISH_WEAK: sig_flow["status"] = "bullish"
            elif flow < config.EXCHANGE_NET_FLOW_BEARISH_STRONG: sig_flow.update({"status": "bearish", "weight": 2})
            else: sig_flow["status"] = "bearish"
        else:
            sig_flow["reason"] = "CoinGlass取得失敗"
        signals.append(sig_flow)

        # Funding Rate
        sig_fr = {"name": "Funding Rate", "status": "neutral", "weight": 1, "value": "N/A", "available": False, "reason": ""}
        if fr is not None:
            sig_fr["available"] = True
            sig_fr["value"] = f"{fr:+.4f}%"
            if fr > config.FUNDING_RATE_OVERHEAT: sig_fr["status"] = "bearish"
            elif fr < config.FUNDING_RATE_COOLING: sig_fr["status"] = "bullish"
        else:
            sig_fr["reason"] = "OKX API取得失敗"
        signals.append(sig_fr)

        # Gold vs BTC ローテーションシグナル
        sig_rotation = {"name": "Gold→BTC", "status": "neutral", "weight": 1, "value": "N/A", "available": False, "reason": ""}
        gold_change = macro_yh.get("gold_change") if macro_yh else None
        btc_change = btc.get("change") if btc else None

        if gold_change is not None and btc_change is not None:
            sig_rotation["available"] = True
            sig_rotation["value"] = f"Au:{gold_change:+.1f}% BTC:{btc_change:+.1f}%"

            # Gold下落 + BTC上昇 = ローテーション発生（強気）
            if gold_change < -1.0 and btc_change > 1.0:
                sig_rotation.update({"status": "bullish", "weight": 2})
            elif gold_change < 0 and btc_change > 0:
                sig_rotation["status"] = "bullish"
            # Gold上昇 + BTC下落 = 安全資産へ逃避（弱気）
            elif gold_change > 1.0 and btc_change < -1.0:
                sig_rotation.update({"status": "bearish", "weight": 2})
            elif gold_change > 0 and btc_change < 0:
                sig_rotation["status"] = "bearish"
            # それ以外は中立
        else:
            sig_rotation["reason"] = "Gold/BTC価格取得失敗"
        signals.append(sig_rotation)

        # ETFフロー（Gist URL必須）
        sig_etf = {"name": "ETFフロー", "status": "neutral", "weight": 1, "value": "N/A", "details": None, "available": False, "reason": ""}
        if not config.ETF_GIST_URL:
            sig_etf["reason"] = "ETF_GIST_URL未設定"
        elif etf_flow:
            if etf_flow.get("status") == "fetching":
                sig_etf["value"] = "取得中..."
                sig_etf["status"] = "loading"
                sig_etf["reason"] = "取得中"
            elif etf_flow.get("total_daily_flow") is not None:
                sig_etf["available"] = True
                flow = etf_flow["total_daily_flow"]
                sig_etf["value"] = f"{flow:+.1f}M USD"
                sig_etf["details"] = {
                    "date": etf_flow.get("date", ""),
                    "top_flows": etf_flow.get("top_flows", [])
                }
                if flow >= config.ETF_FLOW_BULLISH_STRONG:
                    sig_etf.update({"status": "bullish", "weight": 2})
                elif flow >= config.ETF_FLOW_BULLISH_WEAK:
                    sig_etf["status"] = "bullish"
                elif flow <= config.ETF_FLOW_BEARISH_STRONG:
                    sig_etf.update({"status": "bearish", "weight": 2})
                elif flow <= config.ETF_FLOW_BEARISH_WEAK:
                    sig_etf["status"] = "bearish"
            else:
                sig_etf["reason"] = "ETFデータ取得失敗"
        else:
            sig_etf["reason"] = "ETFデータ取得失敗"
        signals.append(sig_etf)

        # 隠れQE（日本経由）シグナル（FRED API必須）
        # Arthur Hayes Thesis: FRBが日本市場を使って隠れた量的緩和を行っている兆候
        hidden_qe = calculate_hidden_qe_signal(walcl_weekly, swpt_data, treast_data, usdjpy_data)

        sig_hidden_qe = {
            "name": "隠れQE",
            "status": "neutral",
            "weight": 1,
            "value": f"{hidden_qe['signal']} ({hidden_qe['score']}/4)",
            "details": hidden_qe,
            "available": False,
            "reason": ""
        }

        # データ可用性チェック
        if config.FRED_API_KEY == "YOUR_FRED_API_KEY_HERE" or not config.FRED_API_KEY:
            sig_hidden_qe["reason"] = "FRED APIキー未設定"
        elif not all([walcl_weekly, swpt_data, usdjpy_data]):
            sig_hidden_qe["reason"] = "FRED/USDJPY取得失敗"
        else:
            sig_hidden_qe["available"] = True
            # シグナルに応じてステータスを設定
            if hidden_qe["signal"] == "ON":
                sig_hidden_qe.update({"status": "bullish", "weight": 2})
            elif hidden_qe["signal"] == "WATCH":
                sig_hidden_qe["status"] = "bullish"
            # OFF の場合は neutral のまま

        signals.append(sig_hidden_qe)

        # =====================================================================
        # 総合スコア計算
        # =====================================================================
        # available=True のシグナルのみでスコアを計算（データ欠損は除外）
        available_signals = [s for s in signals if s.get("available", False)]
        unavailable_signals = [s for s in signals if not s.get("available", False)]

        # coverage: データカバレッジ（取得成功率）
        # 80%未満の場合は警告を表示
        total_signal_count = len(signals)
        available_count = len(available_signals)
        coverage = (available_count / total_signal_count * 100) if total_signal_count > 0 else 0

        # 各シグナルの重み合計を算出（available かつ neutral も weight を使用し対称性を確保）
        bull_w = sum(s["weight"] for s in available_signals if s["status"] == "bullish")
        bear_w = sum(s["weight"] for s in available_signals if s["status"] == "bearish")
        neut_w = sum(s["weight"] for s in available_signals if s["status"] == "neutral")

        # confidence（信頼度）: アクティブなシグナルの割合（available内での比率）
        # 値が高いほど「方向感のあるシグナルが多い」= 判断材料が豊富
        total_w = bull_w + bear_w + neut_w
        active_w = bull_w + bear_w
        confidence = (active_w / total_w * 100) if total_w > 0 else 0

        # スコア計算モード（クエリパラメータ優先、なければconfig設定を使用）
        score_mode = request.args.get('score_mode', getattr(config, 'SCORE_CALC_MODE', 'momentum'))
        if score_mode not in ('momentum', 'conservative'):
            score_mode = 'momentum'

        if score_mode == "momentum":
            # -------------------------------------------------------------
            # Momentumモード: neutral を分母から除外
            # 思想: 「判断材料がある指標」だけで方向性を測る。
            #       neutral が多くてもスコアが薄まらない。トレンド追従向き。
            # 計算: score = (bull - bear) / (bull + bear) * 100
            # -------------------------------------------------------------
            score = ((bull_w - bear_w) / max(active_w, 1)) * 100
        else:
            # -------------------------------------------------------------
            # Conservativeモード: neutral も分母に含める
            # 思想: 「判断材料がない」ことも情報として扱う。
            #       neutral が多いほどスコアは0に近づき、慎重な判断を促す。
            # 計算: score = (bull - bear) / (bull + bear + neutral) * 100
            # -------------------------------------------------------------
            score = ((bull_w - bear_w) / total_w * 100) if total_w > 0 else 0

        summary_text = "方向感が出にくい状況。様子見推奨。"
        if score > 30: summary_text = "強気のシグナルが優勢です。DXYのドル安傾向や市場心理の改善が追い風となっています。"
        elif score > 10: summary_text = "やや強気の環境。上昇基調だが、過熱感には注意が必要。"
        elif score < -30: summary_text = "弱気のシグナルが優勢です。マクロ経済の不透明感から、短期的な下落に警戒が必要です。"
        elif score < -10: summary_text = "やや弱気の環境。下落リスクに注意し、ポジション調整も視野に。"

        # coverage低下時の警告をサマリーに追加
        if coverage < 80:
            summary_text = f"⚠️ データ欠損あり（カバレッジ{round(coverage)}%）。{summary_text}"

        response_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
            "btcPrice": btc.get("usd", 0),
            "score": round(score),
            "confidence": round(confidence),
            "coverage": round(coverage),
            "scoreDetails": {
                "mode": score_mode,
                "bullishWeight": bull_w,
                "bearishWeight": bear_w,
                "neutralWeight": neut_w,
                "availableCount": available_count,
                "totalCount": total_signal_count,
                "formula": f"({bull_w} - {bear_w}) / {active_w}" if score_mode == "momentum" else f"({bull_w} - {bear_w}) / {total_w}"
            },
            "unavailableSignals": [{"name": s["name"], "reason": s.get("reason", "不明")} for s in unavailable_signals],
            "summary": {"title": "💡 分析サマリー", "text": summary_text},
            "signals": signals,
            "is_fallback": False
        }

        duration = (datetime.now() - start_time).total_seconds()
        print(f"✅ /api/data: 計算完了 (処理時間: {duration:.2f}秒)")
        return jsonify(response_data)

    except Exception as e:
        print(f"❌ /api/data: データ取得・計算中にエラー発生: {e}")
        # エラー時はデフォルトレスポンスを返す
        return jsonify({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
            "btcPrice": 0,
            "score": 0,
            "summary": {"title": "⚠️ エラー", "text": "データの取得に失敗しました。しばらく待ってから再度お試しください。"},
            "signals": [],
            "is_fallback": True,
            "error": str(e)
        }), 500


if __name__ == '__main__':
    print(f"🔑 FRED_API_KEY: {'設定済み (' + config.FRED_API_KEY[:4] + '...)' if config.FRED_API_KEY and config.FRED_API_KEY != 'YOUR_FRED_API_KEY_HERE' else '未設定'}")
    print(f"🔑 ETF_GIST_URL: {'設定済み' if config.ETF_GIST_URL else '未設定'}")

    if config.FRED_API_KEY == "YOUR_FRED_API_KEY_HERE":
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("!!! 環境変数FRED_API_KEYを設定してください。          !!!")
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    else:
        print("📊 BTCシグナルダッシュボード起動中...")
        app.run(debug=True, host='0.0.0.0', port=5000)
