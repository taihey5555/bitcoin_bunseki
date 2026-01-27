"""
GitHub Actions用 ETFスクレイピングスクリプト
毎日1回実行し、結果をGistに保存する
"""

import json
import os
import re
import requests
from datetime import datetime

def scrape_etf_flow():
    """SeleniumBaseでCoinGlassからETFフローをスクレイピング"""
    from seleniumbase import SB

    with SB(uc=True, headless=True) as sb:
        sb.open('https://www.coinglass.com/ja/etf/bitcoin')
        sb.sleep(12)

        rows = sb.find_elements('css selector', 'table tr')
        if len(rows) < 3:
            return None

        # 最新の日付行を探す
        latest_row = None
        for row in rows[1:10]:
            text = row.text.strip()
            if re.match(r'^\d{4}-\d{2}-\d{2}', text):
                latest_row = text
                break

        if not latest_row:
            return None

        parts = latest_row.replace('\n', ' ').split()
        date_str = parts[0]

        numbers = re.findall(r'[+-]?\d+\.?\d*K?', latest_row)

        def parse_value(val_str):
            if not val_str or val_str == '0':
                return 0
            val_str = val_str.replace('+', '')
            if 'K' in val_str:
                return float(val_str.replace('K', '')) * 1000
            return float(val_str)

        total_flow = parse_value(numbers[-1]) if numbers else 0

        etf_names = ['GBTC', 'IBIT', 'FBTC', 'ARKB', 'BITB', 'BTCO', 'HODL', 'BRRR', 'EZBC', 'BTCW', 'BTC']
        etf_flows = []

        if len(numbers) >= len(etf_names):
            for i, name in enumerate(etf_names):
                flow = parse_value(numbers[i])
                if flow != 0:
                    etf_flows.append({"symbol": name, "daily_flow": flow})

        return {
            "total_daily_flow": total_flow,
            "date": date_str,
            "top_flows": sorted(etf_flows, key=lambda x: abs(x["daily_flow"]), reverse=True)[:5],
            "updated_at": datetime.now().isoformat()
        }


def update_gist(data):
    """GitHub Gistを更新"""
    gist_id = os.environ.get('GIST_ID')
    github_token = os.environ.get('GH_TOKEN')

    print(f"DEBUG: GIST_ID = {gist_id}")
    print(f"DEBUG: GH_TOKEN exists = {bool(github_token)}")
    print(f"DEBUG: GH_TOKEN length = {len(github_token) if github_token else 0}")

    if not gist_id or not github_token:
        print("Error: GIST_ID or GH_TOKEN not set")
        return False

    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    payload = {
        'files': {
            'etf_flow.json': {
                'content': json.dumps(data, ensure_ascii=False, indent=2)
            }
        }
    }

    response = requests.patch(
        f'https://api.github.com/gists/{gist_id}',
        headers=headers,
        json=payload
    )

    if response.status_code == 200:
        print(f"Gist updated successfully: {data['date']} / {data['total_daily_flow']}M USD")
        return True
    else:
        print(f"Failed to update Gist: {response.status_code} {response.text}")
        return False


if __name__ == '__main__':
    print("Starting ETF scraping...")
    data = scrape_etf_flow()

    if data:
        print(f"Scraped: {data}")
        update_gist(data)
    else:
        print("Failed to scrape ETF data")
