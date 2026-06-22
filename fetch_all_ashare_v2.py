#!/usr/bin/env python3
"""
全A股历史数据批量拉取器 v2
- 原子保存（先写临时文件再rename）
- 每100只保存一次（断点续传）
- 后台运行，完成后exit
"""
import urllib.request, json, pickle, sys, time, os, tempfile
from datetime import datetime

PICKLE_PATH = '/tmp/all_ashare_data.pkl'
STOCK_LIST_PATH = '/tmp/all_a_stocks.json'
CHUNKS = [
    ("2020-01-01", "2021-12-31"),
    ("2022-01-01", "2023-12-31"),
    ("2024-01-01", "2026-12-31"),
]
MIN_BARS = 100
SAVE_EVERY = 100

def load_stock_list():
    with open(STOCK_LIST_PATH) as f:
        raw = json.load(f)
    return raw  # list of strings like "sh600519"

def load_existing():
    if os.path.exists(PICKLE_PATH):
        try:
            with open(PICKLE_PATH, 'rb') as f:
                return pickle.load(f)
        except:
            pass
    return {}

def save_progress(data):
    """原子保存：先写临时文件，再rename"""
    fd, tmp = tempfile.mkstemp(dir='/tmp', prefix='ashare_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, PICKLE_PATH)
    except:
        os.unlink(tmp)
        raise

def fetch_history(full_code):
    """拉取单只股票全部历史日线"""
    all_bars = []
    for start, end in CHUNKS:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={full_code},day,{start},{end},640,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
                stock_data = data.get("data", {}).get(full_code, {})
                bars = stock_data.get("qfqday") or stock_data.get("day") or []
                all_bars.extend(bars)
        except Exception as e:
            pass  # skip this chunk
    return all_bars

def main():
    stocks = load_stock_list()
    existing = load_existing()
    
    done = set(existing.keys())
    todo = [s for s in stocks if s not in done]
    
    print(f"Total stocks: {len(stocks)}, already done: {len(done)}, todo: {len(todo)}")
    
    if not todo:
        print("All stocks already downloaded.")
        return
    
    n_done = 0
    for i, code in enumerate(todo):
        try:
            bars = fetch_history(code)
            if len(bars) >= MIN_BARS:
                existing[code] = bars
                n_done += 1
        except Exception as e:
            pass  # skip bad stocks
        
        # Save every N stocks
        if (i + 1) % SAVE_EVERY == 0:
            save_progress(existing)
            pct = (i + 1) / len(todo) * 100
            print(f"  Saved: {i+1}/{len(todo)} ({pct:.1f}%), valid: {n_done}, time: {datetime.now().strftime('%H:%M:%S')}")
        
        time.sleep(0.05)  # rate limit
    
    # Final save
    save_progress(existing)
    print(f"\n✅ Done! Total valid stocks: {n_done}/{len(stocks)}")
    print(f"File: {PICKLE_PATH} ({os.path.getsize(PICKLE_PATH)/1024/1024:.0f}MB)")

if __name__ == '__main__':
    main()
