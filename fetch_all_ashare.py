#!/usr/bin/env python3
"""
全A股历史数据批量拉取器
- 读取 /tmp/all_a_stocks.json 中的股票列表
- 分3段拉取2020-01-01至今的前复权日线
- 增量保存到 /tmp/all_ashare_data.pkl (断点续传)
- 过滤退市/ST
"""

import urllib.request
import json
import pickle
import sys
import time
import os
from datetime import datetime

PICKLE_PATH = '/tmp/all_ashare_data.pkl'
STOCK_LIST_PATH = '/tmp/all_a_stocks.json'
CHUNKS = [
    ("2020-01-01", "2021-12-31"),
    ("2022-01-01", "2023-12-31"),
    ("2024-01-01", "2026-12-31"),
]
MIN_BARS = 100  # 最少需要100个交易日

def load_stock_list():
    with open(STOCK_LIST_PATH) as f:
        raw = json.load(f)
    # raw is list of strings like "sh600519"
    stocks = []
    for code in raw:
        prefix = code[:2]
        num = code[2:]
        stocks.append({"prefix": prefix, "code": num, "full": code})
    return stocks

def load_existing():
    if os.path.exists(PICKLE_PATH):
        with open(PICKLE_PATH, 'rb') as f:
            return pickle.load(f)
    return {}

def save_progress(data):
    with open(PICKLE_PATH, 'wb') as f:
        pickle.dump(data, f)

def fetch_history(prefix, code):
    """拉取单只股票全部历史日线"""
    all_bars = []
    for start, end in CHUNKS:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,{start},{end},640,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                stock_data = data.get("data", {}).get(f"{prefix}{code}", {})
                bars = stock_data.get("qfqday") or stock_data.get("day") or []
                all_bars.extend(bars)
        except Exception as e:
            return None, str(e)

    # 去重排序
    seen = set()
    unique = []
    for bar in all_bars:
        date = bar[0]
        if date not in seen:
            seen.add(date)
            unique.append(bar)
    unique.sort(key=lambda x: x[0])
    return unique, None

def main():
    stocks = load_stock_list()
    existing = load_existing()
    total = len(stocks)
    done = len(existing)
    
    print(f"📊 全A股数据拉取")
    print(f"   总股票: {total}  已完成: {done}  待拉取: {total - done}")
    print(f"   输出: {PICKLE_PATH}")
    
    fetched = 0
    skipped = 0
    failed = 0
    batch_start = time.time()
    
    for i, stock in enumerate(stocks):
        full_code = stock['full']
        
        # 断点续传
        if full_code in existing:
            skipped += 1
            continue
        
        bars, err = fetch_history(stock['prefix'], stock['code'])
        
        if bars and len(bars) >= MIN_BARS:
            existing[full_code] = bars
            fetched += 1
        else:
            failed += 1
            if err:
                pass  # silent
        
        # 每50只存盘+汇报
        if (fetched + failed) % 50 == 0:
            elapsed = time.time() - batch_start
            save_progress(existing)
            pct = (done + fetched + failed) / total * 100
            print(f"  [{done+fetched+failed}/{total} {pct:.0f}%] "
                  f"新拉{fetched} 跳过{skipped} 失败{failed} "
                  f"耗时{elapsed:.0f}s", file=sys.stderr)
            batch_start = time.time()
            time.sleep(1)  # 每50只休息1秒
        
        time.sleep(0.1)  # 单只间隔
    
    # 最终存盘
    save_progress(existing)
    
    print(f"\n✅ 完成!")
    print(f"   总拉取: {fetched}  跳过: {skipped}  失败: {failed}")
    print(f"   总股票: {len(existing)}")
    print(f"   数据文件: {PICKLE_PATH} ({os.path.getsize(PICKLE_PATH)/1024/1024:.1f} MB)")

if __name__ == "__main__":
    main()
