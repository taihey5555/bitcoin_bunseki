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
    try:
        from seleniumbase import SB

        with SB(uc=True, headless=True) as sb:
            print("Opening CoinGlass page...")
            sb.open('https://www.coinglass.com/ja/etf/bitcoin')
            sb.sleep(12)

            print("Finding table rows...")
            rows = sb.find_elements('css selector', 'table tr')
            print(f"Found {len(rows)} rows")

            if len(rows) < 3:
                print("Not enough rows found")
                return None

            # ETF名の順序（CoinGlassの表示順）
            etf_names = ['GBTC', 'IBIT', 'FBTC', 'ARKB', 'BITB', 'BTCO', 'HODL', 'BRRR', 'EZBC', 'BTCW']

            # 有効なデータがある行を探す（最新から順に）
            for row in rows[1:15]:
                text = row.text.strip()
                print(f"Row text: {text[:100]}...")  # デバッグ用

                # 日付で始まる行を探す
                if not re.match(r'^\d{4}-\d{2}-\d{2}', text):
                    continue

                lines = text.split('\n')
                date_str = lines[0].strip()

                # 数値を抽出（+/-付きの小数を含む）
                numbers = re.findall(r'[+-]?\d+\.?\d*', text)
                print(f"Found numbers: {numbers}")

                if len(numbers) < 5:
                    print(f"Not enough numbers in row, skipping...")
                    continue

                # 全て0の行はスキップ（データなし）
                non_zero_count = sum(1 for n in numbers if float(n) != 0)
                if non_zero_count < 2:
                    print(f"Row has mostly zeros, trying next row...")
                    continue

                # 数値をパース
                def parse_value(val_str):
                    try:
                        return float(val_str)
                    except:
                        return 0

                # 最後の3つは合計値（日次、週次、月次）
                # その前がETF個別の値
                if len(numbers) >= 13:  # 日付の数字 + ETF10個 + 合計3個くらい
                    # 日付部分を除く（YYYY-MM-DD = 3つの数字）
                    values = numbers[3:]  # 日付以降

                    # 最後の3つが合計
                    if len(values) >= 3:
                        daily_total = parse_value(values[-3])  # 日次合計

                        # ETF個別フロー（最後の3つを除く）
                        etf_values = values[:-3]
                        etf_flows = []

                        for i, name in enumerate(etf_names):
                            if i < len(etf_values):
                                flow = parse_value(etf_values[i])
                                if flow != 0:
                                    etf_flows.append({"symbol": name, "daily_flow": flow})

                        print(f"Daily total: {daily_total}, ETF flows: {etf_flows}")

                        return {
                            "total_daily_flow": daily_total,
                            "date": date_str,
                            "top_flows": sorted(etf_flows, key=lambda x: abs(x["daily_flow"]), reverse=True)[:5],
                            "updated_at": datetime.now().isoformat()
                        }

            print("No valid data row found")
            return None

    except Exception as e:
        print(f"Scraping error: {e}")
        import traceback
        traceback.print_exc()
        return None


def update_gist(data):
    """GitHub Gistを更新"""
    gist_id = os.environ.get('GIST_ID')
    github_token = os.environ.get('GH_TOKEN')

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
