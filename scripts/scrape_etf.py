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
            sb.sleep(8)

            # ページ下部にスクロールしてフローテーブルを表示
            print("Scrolling to find flow table...")
            sb.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            sb.sleep(3)
            sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            sb.sleep(5)

            # ページのHTMLを取得してデバッグ
            page_text = sb.get_page_source()

            # 全ての行を取得
            all_rows = sb.find_elements('css selector', 'table tr')
            print(f"Found {len(all_rows)} total table rows")

            # フローデータを含む行を探す（日付形式の行）
            flow_rows = []
            for row in all_rows:
                try:
                    text = row.text if row.text else ""
                    # 日付パターン(YYYY-MM-DD)で始まる行を探す
                    if text and ('2026-' in text[:15] or '2025-' in text[:15]):
                        print(f"Found date row: {text[:80]}...")
                        flow_rows.append(row)
                except:
                    continue

            print(f"Found {len(flow_rows)} flow data rows")

            if len(flow_rows) < 1:
                print("No flow rows found")
                return None

            # ETF名の順序（CoinGlassの表示順）
            etf_names = ['GBTC', 'IBIT', 'FBTC', 'ARKB', 'BITB', 'BTCO', 'HODL', 'BRRR', 'EZBC', 'BTCW']

            # 有効なデータがある行を探す
            for row in flow_rows[:15]:
                text = row.text.strip() if hasattr(row, 'text') else str(row)
                print(f"Flow row: {text[:120]}...")

                # 日付を抽出（最初の10文字 YYYY-MM-DD）
                date_match = re.match(r'^(\d{4}-\d{2}-\d{2})', text)
                if not date_match:
                    continue
                date_str = date_match.group(1)

                # "-" が多い行はスキップ（データなしの行）
                # "- " または " -" のパターンをカウント
                dash_count = text.count(' - ') + text.count('- ') + text.count(' -\n')
                print(f"Dash count (no data indicators): {dash_count}")
                if dash_count >= 3:
                    print(f"Too many no-data indicators ({dash_count}), skipping...")
                    continue

                # 日付以降のテキストから数値を抽出
                text_after_date = text[10:]

                # 数値を抽出（+付き、-付き、K付きを含む）
                # "+183.54" や "-65.80" や "1.13K" などにマッチ
                raw_numbers = re.findall(r'[+-]?\d+\.?\d*K?', text_after_date)

                def parse_value(val_str):
                    try:
                        val_str = val_str.replace('+', '')
                        if 'K' in val_str.upper():
                            return float(val_str.upper().replace('K', '')) * 1000
                        return float(val_str)
                    except:
                        return 0

                numbers = [parse_value(n) for n in raw_numbers]
                print(f"Parsed numbers: {numbers[:15]}...")

                if len(numbers) < 10:
                    print(f"Not enough numbers ({len(numbers)}), skipping...")
                    continue

                # ユニークな非ゼロ値が2個未満の行はスキップ
                # (同じ値が2回出るだけの行はパースエラーの可能性が高い)
                non_zero_unique = set(n for n in numbers if n != 0)
                if len(non_zero_unique) < 2:
                    print(f"Only {len(non_zero_unique)} unique non-zero values, likely bad data, skipping...")
                    continue

                # ETF個別フロー + 合計
                # CoinGlassの構造: [ETF1, ..., ETF10, その他, 日次合計]
                # 最後の値が日次合計（トータル）
                daily_total = numbers[-1]
                etf_values = numbers[:-1][:10]  # 最初の10個がETF

                etf_flows = []
                for i, name in enumerate(etf_names):
                    if i < len(etf_values):
                        flow = etf_values[i]
                        if flow != 0:
                            etf_flows.append({"symbol": name, "daily_flow": flow})

                print(f"Date: {date_str}, Daily total: {daily_total}, ETF flows: {etf_flows[:5]}")

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
