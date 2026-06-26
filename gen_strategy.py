#!/usr/bin/env python3
"""策略面板 v2 — 基准改为000985中证全指"""
import json, urllib.request, math, time
from datetime import datetime

# Load existing asset list
with open("docs/metrics.json") as f:
    metrics = json.load(f)

ASSETS = [{"code": a["code"], "prefix": "sh" if a["code"].startswith("5") else "sz", "name": a["name"]} for a in metrics["assets"]]

def fetch_daily(code, prefix, num=1000):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{num},qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode()
        data = json.loads(resp)
        klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", []) or \
                 data.get("data", {}).get(f"{prefix}{code}", {}).get("day", [])
        if not klines: return None
        prices = []
        for k in klines:
            try:
                dt, c = k[0], float(k[2])
                if c > 0: prices.append({"date": dt, "close": c})
            except: pass
        return prices if len(prices) > 100 else None
    except: return None

print("Fetching ETF daily prices...")
all_prices = {}
for asset in ASSETS:
    prices = fetch_daily(asset["code"], asset["prefix"])
    if prices: all_prices[f'{asset["prefix"]}{asset["code"]}'] = prices
    if len(all_prices) % 15 == 0: print(f"  {len(all_prices)}/{len(ASSETS)}")
    time.sleep(0.08)

print(f"Fetched {len(all_prices)} ETFs")

# Benchmark: 000985 中证全指
print("Fetching benchmark 000985...")
bm_prices = fetch_daily("000985", "sh")
if not bm_prices:
    # fallback: CSI All-Share via 399001 or calculate from ETFs
    bm_prices = fetch_daily("000001", "sh")  # 上证指数 as fallback

# Date alignment
date_set = set()
for p in all_prices.values():
    for d in p: date_set.add(d["date"])
for d in bm_prices: date_set.add(d["date"])
dates = sorted(date_set)
print(f"Dates: {dates[0]} ~ {dates[-1]}, {len(dates)} days")

# Daily returns
daily_rtn = {}
for code, prices in all_prices.items():
    p_dict = {p["date"]: p["close"] for p in prices}
    rtn = {}
    prev = None
    for d in dates:
        if d in p_dict:
            curr = p_dict[d]
            if prev and prev > 0: rtn[d] = curr / prev - 1
            prev = curr
    daily_rtn[code] = rtn

bm_rtn = {}
bm_pdict = {p["date"]: p["close"] for p in bm_prices}
prev = None
for d in dates:
    if d in bm_pdict:
        curr = bm_pdict[d]
        if prev and prev > 0: bm_rtn[d] = curr / prev - 1
        prev = curr

# Find year-ends
year_ends = []
for d in dates:
    if d[5:10] == "12-31": year_ends.append(d)
if dates[-1] not in year_ends: year_ends.append(dates[-1])
# Add first date as start
year_ends = [dates[0]] + year_ends

print(f"Year-ends: {len(year_ends)} points")

# Strategy: top 10 trailing return, equal weight, annual rebalance
nav = 1.0; bm_nav = 1.0
nav_history = []
bm_history = []

for i in range(1, len(year_ends)):
    s, e = year_ends[i-1], year_ends[i]
    si, ei = dates.index(s), dates.index(e)
    
    # Compute trailing 1-year returns
    trails = {}
    for code, rtn in daily_rtn.items():
        cum = 1.0
        for j in range(max(0, si-240), si+1):  # ~1yr lookback
            d = dates[j]
            if d in rtn: cum *= (1 + rtn[d])
        if cum > 0: trails[code] = cum
    ranked = sorted(trails.items(), key=lambda x: x[1], reverse=True)
    top10 = [c for c, _ in ranked[:10]]
    
    # Simulate period
    seg_nav = 1.0; seg_bm = 1.0
    for j in range(si+1, ei+1):
        d = dates[j]
        day_rets = [daily_rtn.get(c, {}).get(d, 0) for c in top10]
        dr = sum(day_rets) / len(day_rets) if day_rets else 0
        seg_nav *= (1 + dr); nav_history.append(nav * seg_nav)
        
        br = bm_rtn.get(d, 0)
        seg_bm *= (1 + br); bm_history.append(bm_nav * seg_bm)
    
    nav *= seg_nav; bm_nav *= seg_bm
    print(f"  {s[:4]}→{e[:4]}: strategy +{((seg_nav-1)*100):.1f}%  benchmark +{((seg_bm-1)*100):.1f}%")

# Stats
total_ret = round((nav-1)*100, 1); bm_ret = round((bm_nav-1)*100, 1)
yrs = (len(dates)-1)/252
ann_ret = round((nav**(1/max(yrs,0.5))-1)*100, 1)
bm_ann = round((bm_nav**(1/max(yrs,0.5))-1)*100, 1)

daily_rets = [nav_history[i]/nav_history[i-1]-1 for i in range(1,len(nav_history))]
avg = sum(daily_rets)/len(daily_rets)
std = math.sqrt(sum((r-avg)**2 for r in daily_rets)/len(daily_rets))
sharpe = round(avg/std*math.sqrt(252), 2) if std>0 else 0

peak = 1.0; mdd = 0
for n in nav_history:
    if n>peak: peak=n
    dd = (peak-n)/peak*100
    if dd>mdd: mdd=dd

# Output every 3rd day for compact JSON
first_date = dates[0]
nav_out = [{"date": first_date, "nav": 1.0}]
bm_out = [{"date": first_date, "nav": 1.0}]
for j in range(0, len(nav_history), 3):
    d_idx = min(len(dates)-1, 1 + j)
    nav_out.append({"date": dates[d_idx], "nav": round(nav_history[j], 4)})
    bm_out.append({"date": dates[d_idx], "nav": round(bm_history[j], 4)})

result = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "strategy": {
        "name": "年化TOP10等权轮动",
        "description": "每年末选取过去一年收益率最高的10只ETF，等权配置，满仓持有，年度调仓",
        "n_assets": 10, "rebalance": "annual",
        "start_date": dates[0], "end_date": dates[-1],
        "total_return": total_ret, "annual_return": ann_ret,
        "max_drawdown": round(mdd, 1), "sharpe": sharpe, "n_years": round(yrs, 1),
    },
    "benchmark": {
        "name": "中证全指(000985)", "code": "000985",
        "total_return": bm_ret, "annual_return": bm_ann,
    },
    "nav_history": nav_out,
    "benchmark_nav": bm_out,
}

with open("docs/strategy.json", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\nStrategy: {total_ret}% tot, {ann_ret}% ann, {sharpe} Sharpe, -{mdd:.1f}% MDD")
print(f"Benchmark: {bm_ret}% tot, {bm_ann}% ann")
