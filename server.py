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
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return results


@app.route('/')
def dashboard():
    """ダッシュボードのHTMLページを配信する"""
    return render_template('dashboard_pro.html')


@app.route('/api/data')
def get_data():
    """ダッシュボード用のデータを取得・計算してJSONで返す"""
    try:
        print("📊 /api/data: データ並列取得・計算開始...")
        start_time = datetime.now()

        # 非同期処理を同期的に実行
        results = asyncio.run(fetch_all_data())

        # エラーが発生した場合はNoneを設定
        balance_sheet, rrp, tga, dxy, ex_flow, macro_yh, btc, fg, fr, etf_flow = [
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

        sig_gold = {"name": "Gold", "status": "neutral", "weight": 1, "value": "N/A"}
        if macro_yh and macro_yh.get("gold_change") is not None:
            gc = macro_yh["gold_change"]
            sig_gold["value"] = f"{gc:+.1f}%"
            if abs(gc) > config.GOLD_CHANGE_THRESHOLD: sig_gold["status"] = "bullish" if gc > 0 else "bearish"
        signals.append(sig_gold)

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
