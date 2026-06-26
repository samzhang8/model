#!/usr/bin/env python3
"""策略面板 v3 — 资产面板全字段 + 回撤绿色标记 + 无策略描述"""
import json, urllib.request, math, time
from datetime import datetime

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

print("Fetching ETF prices...")
all_prices = {}
for asset in ASSETS:
    prices = fetch_daily(asset["code"], asset["prefix"])
    if prices: all_prices[f'{asset["prefix"]}{asset["code"]}'] = prices
    if len(all_prices) % 15 == 0: print(f"  {len(all_prices)}/{len(ASSETS)}")
    time.sleep(0.08)

print("Fetching benchmark...")
bm_prices = fetch_daily("000985", "sh")

date_set = set()
for p in all_prices.values():
    for d in p: date_set.add(d["date"])
for d in bm_prices: date_set.add(d["date"])
dates = sorted(date_set)

daily_rtn = {}
for code, prices in all_prices.items():
    p_dict = {p["date"]: p["close"] for p in prices}
    rtn = {}
    prev = None
    for d in dates:
        if d in p_dict:
            curr = p_dict[d]; 
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

year_ends = [dates[0]] + [d for d in dates if d[5:10] == "12-31"]
if dates[-1] not in year_ends: year_ends.append(dates[-1])

nav = 1.0; bm_nav = 1.0
nav_history = []; bm_history = []; all_dates = []

for i in range(1, len(year_ends)):
    s, e = year_ends[i-1], year_ends[i]
    si, ei = dates.index(s), dates.index(e)
    trails = {}
    for code, rtn in daily_rtn.items():
        cum = 1.0
        for j in range(max(0, si-240), si+1):
            d = dates[j]
            if d in rtn: cum *= (1 + rtn[d])
        if cum > 0: trails[code] = cum
    top10 = [c for c, _ in sorted(trails.items(), key=lambda x: x[1], reverse=True)[:10]]
    
    seg_nav = 1.0; seg_bm = 1.0
    for j in range(si+1, ei+1):
        d = dates[j]
        day_rets = [daily_rtn.get(c, {}).get(d, 0) for c in top10]
        dr = sum(day_rets) / len(day_rets) if day_rets else 0
        seg_nav *= (1 + dr); nav_history.append(nav * seg_nav)
        br = bm_rtn.get(d, 0); seg_bm *= (1 + br); bm_history.append(bm_nav * seg_bm)
        all_dates.append(d)
    nav *= seg_nav; bm_nav *= seg_bm
    print(f"  {s[:4]}→{e[:4]}: +{((seg_nav-1)*100):.1f}%")

# ===== All metrics =====
total_ret = round((nav-1)*100, 1)
yrs = (len(dates)-1)/252
ann_ret = round((nav**(1/max(yrs,0.5))-1)*100, 1)
bm_ret = round((bm_nav-1)*100, 1)
bm_ann = round((bm_nav**(1/max(yrs,0.5))-1)*100, 1)

daily_rets = [nav_history[i]/nav_history[i-1]-1 for i in range(1,len(nav_history))]
avg = sum(daily_rets)/len(daily_rets)
std = math.sqrt(sum((r-avg)**2 for r in daily_rets)/len(daily_rets))
sharpe = round(avg/std*math.sqrt(252), 2) if std>0 else 0
ann_vol = round(std*math.sqrt(252)*100, 1)  # percentage

# Max drawdown + period
peak = 1.0; mdd = 0; mdd_start = 0; mdd_end = 0; peak_idx = 0
current_dd_start = 0
for j, n in enumerate(nav_history):
    if n > peak: peak = n; peak_idx = j; current_dd_start = j
    dd = (peak - n) / peak
    if dd > mdd: mdd = dd; mdd_start = current_dd_start; mdd_end = j

mdd = round(mdd*100, 1)
calmar = round(ann_ret/mdd, 2) if mdd > 0 else 0
total_value_10k = round(10000 * nav, 0)

# 最长无新高
max_no_high = 0; no_high_start = 0; no_high_end = 0
peak2 = nav_history[0]; current_start = 0
for j, n in enumerate(nav_history):
    if n >= peak2: peak2 = n; current_start = j
    gap = j - current_start
    if gap > max_no_high: max_no_high = gap; no_high_start = current_start; no_high_end = j

# 已回撤天数 + 近期高点
last_peak_idx = peak_idx
days_since_peak = len(nav_history) - 1 - last_peak_idx

# Daily change (涨跌幅)
latest_rtn = daily_rets[-1]*100 if daily_rets else 0

# Chart data with MDD green highlight
nav_out = []
for j in range(0, len(nav_history), 3):
    nav_out.append({
        "date": all_dates[j] if j < len(all_dates) else dates[-1],
        "nav": round(nav_history[j], 4),
        "in_drawdown": j >= mdd_start and j <= mdd_end
    })

bm_out = [{"date": all_dates[j], "nav": round(bm_history[j], 4)}
          for j in range(0, len(bm_history), 3) if j < len(all_dates)]

result = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "strategy": {
        "total_return": total_ret,
        "annual_return": ann_ret,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "annual_vol": ann_vol,
        "total_value_10k": total_value_10k,
        "max_high_gap_days": max_no_high,
        "no_high_start": all_dates[no_high_start] if no_high_start < len(all_dates) else "",
        "no_high_end": all_dates[no_high_end] if no_high_end < len(all_dates) else "",
        "days_since_last_peak": days_since_peak,
        "last_peak_date": all_dates[last_peak_idx] if last_peak_idx < len(all_dates) else "",
        "latest_change": round(latest_rtn, 2),
        "n_assets": 10,
        "rebalance": "annual",
        "start_date": all_dates[0],
        "end_date": all_dates[-1],
    },
    "benchmark": {
        "name": "中证全指(000985)",
        "total_return": bm_ret,
        "annual_return": bm_ann,
    },
    "nav_history": nav_out,
    "benchmark_nav": bm_out,
    "mdd_period": {
        "start": all_dates[mdd_start] if mdd_start < len(all_dates) else "",
        "end": all_dates[mdd_end] if mdd_end < len(all_dates) else "",
    }
}

with open("docs/strategy.json", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\n{total_ret}% tot, {ann_ret}% ann, {sharpe} Sharpe, -{mdd}% MDD, {calmar} Calmar")
print(f"MDD period: {result['mdd_period']['start']} → {result['mdd_period']['end']}")
