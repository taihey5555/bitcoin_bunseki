"""
BTC分析プロジェクト用 データプロバイダーモジュール (非同期・バックテスト対応版 v2)

各種APIから市場データを非同期で取得する。
target_date を指定すると過去のデータを、指定しない場合は最新のデータを取得する。
v2: 過去データ取得の信頼性を向上
"""

import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import config

class DataProviderError(Exception):
    """データ取得に関するカスタムエラー"""
    pass

async def _request_handler(session: aiohttp.ClientSession, url: str, params: Dict = None, headers: Dict = None) -> Dict:
    """共通の非同期リクエストハンドラ"""
    final_headers = {"User-Agent": config.USER_AGENT}
    if headers: final_headers.update(headers)
    
    try:
        async with session.get(url, params=params, headers=final_headers, timeout=20) as response:
            if response.status == 429:
                await asyncio.sleep(60)
                async with session.get(url, params=params, headers=final_headers, timeout=20) as resp:
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            response.raise_for_status()
            return await response.json(content_type=None)
    except Exception as e:
        raise DataProviderError(f"リクエストエラー ({url}): {e}") from e

async def get_fred_data(session: aiohttp.ClientSession, series_id: str, target_date: Optional[datetime] = None) -> Optional[float]:
    """FREDからデータを取得。休日等を考慮し、指定日から最大5日前まで遡って探す。"""
    url = "https://api.stlouisfed.org/fred/series/observations"
    for i in range(5):
        date_to_fetch = (target_date if target_date else datetime.now()) - timedelta(days=i)
        date_str = date_to_fetch.strftime('%Y-%m-%d')
        params = {
            "series_id": series_id, "api_key": config.FRED_API_KEY, "file_type": "json",
            "observation_start": date_str, "observation_end": date_str
        }
        try:
            data = await _request_handler(session, url, params=params)
            if data and data.get("observations"):
                value = data["observations"][0]["value"]
                return float(value) if value != "." else None
        except DataProviderError:
            continue # データがなければ次の日へ
    print(f"⚠️ FRED取得失敗 ({series_id}): 5日間データが見つかりませんでした。")
    return None

async def get_btc_price(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Dict:
    """Yahoo FinanceからBTC価格を取得"""
    try:
        data = await _get_yahoo_finance_data(session, "BTC-USD", target_date)
        if not data: return {}
        
        closes = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
        if closes:
            return {"usd": closes[-1], "jpy": None} # JPYはYahoo FinanceのこのAPIでは直接取得しない
        return {}
    except Exception as e:
        print(f"⚠️ BTC価格取得失敗: {e}")
        return {}

async def get_fear_greed_index(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Optional[int]:
    """Alternative.meからF&G指数を取得。過去データはAPIが不安定なため最新値で代用。"""
    try:
        if target_date and (datetime.now().date() != target_date.date()):
            print("⚠️ F&Gの過去データはAPI信頼性のため最新値で代用します。")
        
        url = "https://api.alternative.me/fng/?limit=1"
        data = await _request_handler(session, url)
        return int(data["data"][0]["value"])
    except (DataProviderError, KeyError, IndexError, ValueError) as e:
        print(f"⚠️ F&G指数取得失敗: {e}")
        return None

async def _get_yahoo_finance_data(session: aiohttp.ClientSession, symbol: str, target_date: Optional[datetime] = None):
    """Yahoo Financeからデータを取得する共通関数（休日考慮）"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    for i in range(5):
        date_to_fetch = (target_date if target_date else datetime.now()) - timedelta(days=i)
        
        # 履歴取得にはperiod1とperiod2が必要
        period1 = int((date_to_fetch - timedelta(days=1)).timestamp())
        period2 = int(date_to_fetch.timestamp())
        params = {"period1": period1, "period2": period2, "interval": "1d"}

        try:
            data = await _request_handler(session, url, params=params)
            if data and data.get("chart", {}).get("result", [{}])[0].get("timestamp"):
                return data
        except DataProviderError:
            continue
    return None

async def get_dxy(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Optional[Dict]:
    try:
        data = await _get_yahoo_finance_data(session, "DX-Y.NYB", target_date)
        if not data: return None

        indicators = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0]
        closes = [c for c in indicators.get("close", []) if c is not None]

        if not closes: return None
        change = ((closes[-1] - closes[0]) / closes[0] * 100) if len(closes) > 1 else 0
        return {"value": closes[-1], "change": change}
    except (KeyError, IndexError) as e:
        print(f"⚠️ DXY取得失敗: {e}")
        return None

async def get_macro_data(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Dict:
    result = {}
    endpoints = {"gold": "GC=F", "sp500": "^GSPC", "vix": "^VIX"}
    
    tasks = {key: _get_yahoo_finance_data(session, symbol, target_date) for key, symbol in endpoints.items()}
    responses = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    res_map = dict(zip(tasks.keys(), responses))

    for key, res_data in res_map.items():
        if isinstance(res_data, Exception) or not res_data: continue
        try:
            indicators = res_data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0]
            closes = [c for c in indicators.get("close", []) if c is not None]
            if not closes: continue
            result[key] = closes[-1]
            result[f"{key}_change"] = ((closes[-1] - closes[0]) / closes[0] * 100) if len(closes) > 1 else 0
        except (KeyError, IndexError) as e:
            print(f"⚠️ マクロデータ解析失敗 ({key}): {e}")
    return result

# バックテスト非対応の関数
async def get_exchange_flow(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Optional[Dict]:
    if target_date:
        print("⚠️ 取引所フローの過去データは取得できません。")
        return None
    # ... (実装は変更なし)
    try:
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        params = {"assets": "btc", "metrics": "FlowInExNtv,FlowOutExNtv", "page_size": 1}
        data = await _request_handler(session, url, params=params)
        latest = data["data"][0]
        return {"net_flow": float(latest.get("FlowOutExNtv", 0)) - float(latest.get("FlowInExNtv", 0))}
    except Exception as e: return None

async def get_funding_rate(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Optional[float]:
    if target_date:
        print("⚠️ ファンディングレートの過去データは取得できません。")
        return None

    # Binance APIを試す
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": "BTCUSDT", "limit": 1}
        data = await _request_handler(session, url, params=params)
        if data:
            return float(data[0]["fundingRate"]) * 100
    except Exception as e:
        print(f"⚠️ Binance Funding Rate取得失敗: {e}")

    # フォールバック: CoinGlass公開API
    try:
        url = "https://open-api.coinglass.com/public/v2/funding"
        params = {"symbol": "BTC", "time_type": "all"}
        data = await _request_handler(session, url, params=params)
        if data and data.get("data"):
            # 最初のデータ（通常はBinance）を取得
            rate = data["data"][0].get("rate")
            if rate:
                return float(rate) * 100
    except Exception as e:
        print(f"⚠️ CoinGlass Funding Rate取得失敗: {e}")

    return None

# ETFフローのキャッシュ（1時間有効）
_etf_cache = {"data": None, "timestamp": None}
ETF_CACHE_DURATION = 3600  # 1時間

# GitHub GistのURL（公開Gist）
ETF_GIST_URL = "https://gist.githubusercontent.com/{user}/{gist_id}/raw/etf_flow.json"


async def get_etf_flow(session: aiohttp.ClientSession, target_date: Optional[datetime] = None) -> Optional[Dict]:
    """GitHub GistからETFフローデータを取得（GitHub Actionsで更新）"""
    global _etf_cache

    if target_date:
        print("⚠️ ETFフローの過去データは取得できません。")
        return None

    # キャッシュチェック（1時間以内ならスキップ）
    now = datetime.now()
    if _etf_cache["data"] and _etf_cache["timestamp"]:
        elapsed = (now - _etf_cache["timestamp"]).total_seconds()
        if elapsed < ETF_CACHE_DURATION:
            print(f"📡 ETFフロー（キャッシュ）: {_etf_cache['data'].get('total_daily_flow')}M USD")
            return _etf_cache["data"]

    try:
        # GistからJSONを取得
        gist_url = config.ETF_GIST_URL if hasattr(config, 'ETF_GIST_URL') else None
        if not gist_url:
            print("⚠️ ETF_GIST_URLが設定されていません")
            return None

        data = await _request_handler(session, gist_url)
        if data and data.get("total_daily_flow") is not None:
            _etf_cache["data"] = data
            _etf_cache["timestamp"] = now
            print(f"📡 ETFフロー取得成功: {data.get('date')} / Total: {data.get('total_daily_flow')}M USD")
            return data

    except Exception as e:
        print(f"⚠️ ETFフロー取得失敗: {e}")
        # エラー時は古いキャッシュを返す
        if _etf_cache["data"]:
            print("↪️ 古いキャッシュを使用します")
            return _etf_cache["data"]

    return None

# --- 同期関数 (変更なし) ---
def calculate_liquidity(balance_sheet: List[Dict], rrp: List[Dict], tga: List[Dict]) -> List[Dict]:
    # ...
    return []
def calculate_correlation(data1: List[Dict], data2: List[Dict]) -> Optional[float]:
    # ...
    return None
