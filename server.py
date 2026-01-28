"""
BTCシグナルダッシュボード用 バックエンドサーバー
- Flaskを使用して、ダッシュボードのHTMLを配信する
- /api/data エンドポイントで、最新の市場データをJSON形式で提供する
"""

import asyncio
import aiohttp
import json
from flask import Flask, jsonify, render_template
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
            data_provider.get_fred_data_with_change(session, "SWPT"),    # Central Bank Swaps
            data_provider.get_fred_data_with_change(session, "TREAST"),  # Treasury Holdings
            data_provider.get_usdjpy(session),                           # USDJPY
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return results


def calculate_hidden_qe_signal(walcl_data, swpt_data, treast_data, usdjpy_data):
    """
    隠れQE（日本経由）シグナルを計算

    Arthur Hayes "Japanese QE Thesis":
    FRBが直接的なQEを行わずに、日本市場を経由して
    ドル流動性を供給している兆候を検出

    判定条件（すべて前週比）:
    1. Total Assets（WALCL）> +0.1% → FRB資産拡大中
    2. Treasury Holdings（TREAST）< +0.5% → 国内QE非活発
    3. Central Bank Swaps（SWPT）>= +10% → 海外へのドル供給急増
    4. USDJPY >= +1% → 円安進行（ドル需要増）

    判定結果:
    - 4条件成立: ON（強気シグナル）
    - 2-3条件成立: WATCH（注視）
    - 0-1条件成立: OFF（シグナルなし）

    Returns:
        {
            "signal": "OFF" | "WATCH" | "ON",
            "score": 0-4,
            "details": {...},
            "explanation": "...",
            "thresholds": {...},
            "updated_at": "..."
        }
    """
    score = 0
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # 閾値情報（フロントエンド表示用）
    thresholds = {
        "total_assets": f"> +{config.TOTAL_ASSETS_INCREASE_THRESHOLD}%",
        "treasury": f"< +{config.TREASURY_HOLDINGS_INCREASE_THRESHOLD}%",
        "swaps": f">= +{config.SWAPS_SURGE_THRESHOLD}%",
        "usdjpy": f">= +{config.USDJPY_WEAKENING_THRESHOLD}%"
    }

    # 各条件の詳細情報を初期化
    details = {
        "total_assets": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.TOTAL_ASSETS_INCREASE_THRESHOLD,
            "met": False,
            "reason": "FREDからデータを取得できませんでした"
        },
        "treasury": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.TREASURY_HOLDINGS_INCREASE_THRESHOLD,
            "met": False,
            "reason": "FREDからデータを取得できませんでした"
        },
        "swaps": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.SWAPS_SURGE_THRESHOLD,
            "met": False,
            "reason": "FREDからデータを取得できませんでした"
        },
        "usdjpy": {
            "status": "データ取得失敗",
            "value": None,
            "change": None,
            "threshold": config.USDJPY_WEAKENING_THRESHOLD,
            "met": False,
            "reason": "Yahoo Financeからデータを取得できませんでした"
        },
    }

    # 条件1: Total Assets（WALCL）が前週比で増加
    # 判定: change > 0.1% で「FRB資産拡大中」
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
            "date": walcl_data.get("date")
        }

    # 条件2: Treasury Holdings（TREAST）が横ばいまたは減少
    # 判定: change < 0.5% で「国内QE非活発」（=隠れQEの条件成立）
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
            "date": treast_data.get("date")
        }

    # 条件3: Central Bank Swaps（SWPT）が急増
    # 判定: change >= 10% で「海外へのドル供給急増」
    if swpt_data and swpt_data.get("change") is not None:
        change = swpt_data["change"]
        threshold = config.SWAPS_SURGE_THRESHOLD
        met = change >= threshold

        if met:
            score += 1
            reason = f"前週比 {change:+.2f}% >= {threshold}% → ドル供給急増"
            status = "急増"
        else:
            reason = f"前週比 {change:+.2f}% < {threshold}% → 通常レベル"
            status = "通常"

        details["swaps"] = {
            "status": status,
            "value": swpt_data["value"],
            "change": change,
            "threshold": threshold,
            "met": met,
            "reason": reason,
            "date": swpt_data.get("date")
        }

    # 条件4: USDJPY が円安方向
    # 判定: change >= 1% で「円安進行」
    if usdjpy_data and usdjpy_data.get("change") is not None:
        change = usdjpy_data["change"]
        threshold = config.USDJPY_WEAKENING_THRESHOLD
        met = change >= threshold

        if met:
            score += 1
            reason = f"週次変化 {change:+.2f}% >= {threshold}% → 円安進行中"
            status = "円安進行"
        else:
            if change <= -threshold:
                reason = f"週次変化 {change:+.2f}% → 円高進行中"
                status = "円高進行"
            else:
                reason = f"週次変化 {change:+.2f}% → 安定推移"
                status = "安定"

        details["usdjpy"] = {
            "status": status,
            "value": usdjpy_data["value"],
            "change": change,
            "threshold": threshold,
            "met": met,
            "reason": reason
        }

    # シグナル判定（スコアに基づく3段階判定）
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

        sig_liquidity = {"name": "USD流動性", "status": "neutral", "weight": 1, "value": "N/A"}
        if liquidity:
            sig_liquidity["value"] = f"${liquidity/1e6:.2f}T"
            if liquidity > config.LIQUIDITY_BULLISH_STRONG: sig_liquidity.update({"status": "bullish", "weight": 2})
            elif liquidity > config.LIQUIDITY_BULLISH_WEAK: sig_liquidity["status"] = "bullish"
            elif liquidity < config.LIQUIDITY_BEARISH_STRONG: sig_liquidity.update({"status": "bearish", "weight": 2})
        signals.append(sig_liquidity)

        sig_dxy = {"name": "DXY", "status": "neutral", "weight": 1, "value": "N/A"}
        if dxy and dxy.get("value"):
            sig_dxy["value"] = f'{dxy["value"]:.1f}'
            if dxy["value"] > config.DXY_BEARISH_STRONG: sig_dxy.update({"status": "bearish", "weight": 2})
            elif dxy["value"] > config.DXY_BEARISH_WEAK: sig_dxy["status"] = "bearish"
            elif dxy["value"] < config.DXY_BULLISH_STRONG: sig_dxy.update({"status": "bullish", "weight": 2})
        signals.append(sig_dxy)

        sig_fg = {"name": "Fear & Greed", "status": "neutral", "weight": 1, "value": "N/A"}
        if fg:
            sig_fg["value"] = str(fg)
            if fg <= config.FEAR_GREED_EXTREME_FEAR: sig_fg.update({"status": "bullish", "weight": 2})
            elif fg <= config.FEAR_GREED_FEAR: sig_fg["status"] = "bullish"
            elif fg >= config.FEAR_GREED_EXTREME_GREED: sig_fg.update({"status": "bearish", "weight": 2})
            elif fg >= config.FEAR_GREED_GREED: sig_fg["status"] = "bearish"
        signals.append(sig_fg)

        sig_flow = {"name": "取引所フロー", "status": "neutral", "weight": 1, "value": "N/A"}
        if ex_flow and ex_flow.get("net_flow") is not None:
            flow = ex_flow["net_flow"]
            sig_flow["value"] = f"{flow:+.0f} BTC"
            if flow > config.EXCHANGE_NET_FLOW_BULLISH_STRONG: sig_flow.update({"status": "bullish", "weight": 2})
            elif flow > config.EXCHANGE_NET_FLOW_BULLISH_WEAK: sig_flow["status"] = "bullish"
            elif flow < config.EXCHANGE_NET_FLOW_BEARISH_STRONG: sig_flow.update({"status": "bearish", "weight": 2})
            else: sig_flow["status"] = "bearish"
        signals.append(sig_flow)

        sig_fr = {"name": "Funding Rate", "status": "neutral", "weight": 1, "value": "N/A"}
        if fr is not None:
            sig_fr["value"] = f"{fr:+.4f}%"
            if fr > config.FUNDING_RATE_OVERHEAT: sig_fr["status"] = "bearish"
            elif fr < config.FUNDING_RATE_COOLING: sig_fr["status"] = "bullish"
        signals.append(sig_fr)

        # Gold vs BTC ローテーションシグナル
        sig_rotation = {"name": "Gold→BTC", "status": "neutral", "weight": 1, "value": "N/A"}
        gold_change = macro_yh.get("gold_change") if macro_yh else None
        btc_change = btc.get("change") if btc else None

        if gold_change is not None and btc_change is not None:
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
        signals.append(sig_rotation)

        sig_etf = {"name": "ETFフロー", "status": "neutral", "weight": 1, "value": "N/A", "details": None}
        if etf_flow:
            if etf_flow.get("status") == "fetching":
                sig_etf["value"] = "取得中..."
                sig_etf["status"] = "loading"
            elif etf_flow.get("total_daily_flow") is not None:
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
        signals.append(sig_etf)

        # 隠れQE（日本経由）シグナル
        # Arthur Hayes Thesis: FRBが日本市場を使って隠れた量的緩和を行っている兆候
        hidden_qe = calculate_hidden_qe_signal(walcl_weekly, swpt_data, treast_data, usdjpy_data)

        sig_hidden_qe = {
            "name": "隠れQE",
            "status": "neutral",
            "weight": 1,
            "value": f"{hidden_qe['signal']} ({hidden_qe['score']}/4)",
            "details": hidden_qe
        }

        # シグナルに応じてステータスを設定
        if hidden_qe["signal"] == "ON":
            sig_hidden_qe.update({"status": "bullish", "weight": 2})
        elif hidden_qe["signal"] == "WATCH":
            sig_hidden_qe["status"] = "bullish"
        # OFF の場合は neutral のまま

        signals.append(sig_hidden_qe)

        bull_w = sum(s["weight"] for s in signals if s["status"] == "bullish")
        bear_w = sum(s["weight"] for s in signals if s["status"] == "bearish")
        neut_w = sum(1 for s in signals if s["status"] == "neutral")
        total_weight = bull_w + bear_w + neut_w
        score = ((bull_w - bear_w) / total_weight) * 100 if total_weight > 0 else 0

        summary_text = "方向感が出にくい状況。様子見推奨。"
        if score > 30: summary_text = "強気のシグナルが優勢です。DXYのドル安傾向や市場心理の改善が追い風となっています。"
        elif score > 10: summary_text = "やや強気の環境。上昇基調だが、過熱感には注意が必要。"
        elif score < -30: summary_text = "弱気のシグナルが優勢です。マクロ経済の不透明感から、短期的な下落に警戒が必要です。"
        elif score < -10: summary_text = "やや弱気の環境。下落リスクに注意し、ポジション調整も視野に。"

        response_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
            "btcPrice": btc.get("usd", 0),
            "score": round(score),
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
