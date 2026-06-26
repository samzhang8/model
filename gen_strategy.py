#!/usr/bin/env python3
"""
策略面板数据生成器
策略: 等权配置年收益率排名前10的ETF，每年末调仓
"""
import json, urllib.request, math, time
from datetime import datetime, timedelta
from collections import defaultdict

ASSETS = [
    {"code": "510300", "prefix": "sh", "name": "沪深300ETF"},
    {"code": "512090", "prefix": "sh", "name": "MSCI A股ETF"},
    {"code": "510500", "prefix": "sh", "name": "中证500ETF"},
    {"code": "510050", "prefix": "sh", "name": "上证50ETF"},
    {"code": "159915", "prefix": "sz", "name": "创业板ETF"},
    {"code": "159949", "prefix": "sz", "name": "创业板50"},
    {"code": "588000", "prefix": "sh", "name": "科创50ETF"},
    {"code": "588020", "prefix": "sh", "name": "科创成长ETF"},
    {"code": "159780", "prefix": "sz", "name": "科创创业ETF"},
    {"code": "159967", "prefix": "sz", "name": "创业板成长ETF"},
    {"code": "159552", "prefix": "sz", "name": "中证2000增强ETF"},
    {"code": "512100", "prefix": "sh", "name": "中证1000ETF"},
    {"code": "560110", "prefix": "sh", "name": "中证1000增强ETF"},
    {"code": "159901", "prefix": "sz", "name": "深证100ETF"},
    {"code": "510880", "prefix": "sh", "name": "红利ETF"},
    {"code": "512880", "prefix": "sh", "name": "证券ETF"},
    {"code": "512010", "prefix": "sh", "name": "医药ETF"},
    {"code": "512660", "prefix": "sh", "name": "军工ETF"},
    {"code": "512670", "prefix": "sh", "name": "国防ETF"},
    {"code": "512480", "prefix": "sh", "name": "半导体ETF"},
    {"code": "512690", "prefix": "sh", "name": "酒ETF"},
    {"code": "515050", "prefix": "sh", "name": "5GETF"},
    {"code": "515880", "prefix": "sh", "name": "通信ETF"},
    {"code": "516510", "prefix": "sh", "name": "云计算ETF"},
    {"code": "515400", "prefix": "sh", "name": "大数据ETF"},
    {"code": "512800", "prefix": "sh", "name": "银行ETF"},
    {"code": "512200", "prefix": "sh", "name": "房地产ETF"},
    {"code": "515700", "prefix": "sh", "name": "新能源车ETF"},
    {"code": "515790", "prefix": "sh", "name": "光伏ETF"},
    {"code": "516160", "prefix": "sh", "name": "新能源ETF"},
    {"code": "159996", "prefix": "sz", "name": "家电ETF"},
    {"code": "159611", "prefix": "sz", "name": "电力ETF"},
    {"code": "515220", "prefix": "sh", "name": "煤炭ETF"},
    {"code": "516110", "prefix": "sh", "name": "汽车ETF"},
    {"code": "159766", "prefix": "sz", "name": "旅游ETF"},
    {"code": "512980", "prefix": "sh", "name": "传媒ETF"},
    {"code": "159865", "prefix": "sz", "name": "养殖ETF"},
    {"code": "515210", "prefix": "sh", "name": "钢铁ETF"},
    {"code": "159819", "prefix": "sz", "name": "人工智能ETF"},
    {"code": "515980", "prefix": "sh", "name": "人工智能ETF华富"},
    {"code": "159530", "prefix": "sz", "name": "机器人ETF"},
    {"code": "159928", "prefix": "sz", "name": "消费ETF"},
    {"code": "159647", "prefix": "sz", "name": "中药ETF"},
    {"code": "159638", "prefix": "sz", "name": "高端装备ETF"},
    {"code": "159206", "prefix": "sz", "name": "卫星ETF"},
    {"code": "518880", "prefix": "sh", "name": "黄金ETF"},
    {"code": "513500", "prefix": "sh", "name": "标普500ETF"},
    {"code": "159941", "prefix": "sz", "name": "纳指ETF"},
    {"code": "510900", "prefix": "sh", "name": "H股ETF"},
    {"code": "513100", "prefix": "sh", "name": "纳指100ETF"},
    {"code": "159920", "prefix": "sz", "name": "恒生ETF"},
    {"code": "513520", "prefix": "sh", "name": "日经ETF"},
    {"code": "513080", "prefix": "sh", "name": "法国CAC40ETF"},
    {"code": "513050", "prefix": "sh", "name": "中概互联"},
    {"code": "159805", "prefix": "sz", "name": "传媒ETF鹏华"},
    {"code": "159985", "prefix": "sz", "name": "豆粕ETF"},
    {"code": "159980", "prefix": "sz", "name": "有色ETF"},
    {"code": "516400", "prefix": "sh", "name": "稀土ETF"},
    {"code": "159755", "prefix": "sz", "name": "电池ETF"},
    {"code": "159508", "prefix": "sz", "name": "普洛斯REIT"},
    {"code": "159545", "prefix": "sz", "name": "恒生红利ETF"},
    {"code": "159699", "prefix": "sz", "name": "恒生消费ETF"},
    {"code": "159561", "prefix": "sz", "name": "科创板50增强ETF"},
    {"code": "513300", "prefix": "sh", "name": "东南亚科技ETF"},
    {"code": "516880", "prefix": "sh", "name": "双碳ETF"},
    {"code": "159839", "prefix": "sz", "name": "创新药ETF"},
]

def fetch_daily(code, prefix, num=1000):
    """从腾讯API获取日K线"""
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
                dt, o, c = k[0], float(k[1]), float(k[2])
                if c > 0: prices.append({"date": dt, "close": c})
            except: pass
        return prices if len(prices) > 100 else None
    except:
        return None

print("Fetching daily prices for 68 ETFs...")
all_prices = {}
for asset in ASSETS:
    code, prefix, name = asset["code"], asset["prefix"], asset["name"]
    prices = fetch_daily(code, prefix)
    if prices:
        all_prices[f"{prefix}{code}"] = prices
        if len(all_prices) % 10 == 0:
            print(f"  {len(all_prices)}/68")
    time.sleep(0.1)

print(f"Fetched {len(all_prices)} ETFs with daily data")

# Build date index
date_set = set()
for p in all_prices.values():
    for d in p:
        date_set.add(d["date"])
dates = sorted(date_set)
print(f"Date range: {dates[0]} to {dates[-1]}, {len(dates)} days")

# Compute daily returns for each ETF
daily_rtn = {}
for code, prices in all_prices.items():
    p_dict = {p["date"]: p["close"] for p in prices}
    rtn_dict = {}
    prev = None
    for d in dates:
        if d in p_dict:
            curr = p_dict[d]
            if prev and prev > 0:
                rtn_dict[d] = curr / prev - 1
            prev = curr
    daily_rtn[code] = rtn_dict

# ===== Strategy: annual top-10 rebalance =====
# Find year-end dates
year_end_dates = []
current_year = None
for d in dates:
    yr = d[:4]
    if yr != current_year:
        if current_year is not None and year_end_dates:
            pass  # keep the last day of previous year
        current_year = yr
    if d[5:10] == "12-31":
        year_end_dates.append(d)
# Also use last day as year-end for partial years
if dates[-1] not in year_end_dates:
    year_end_dates.append(dates[-1])

print(f"Year-end dates: {year_end_dates}")

# For each year-end, compute trailing 1-year return and rank
nav = 1.0
nav_history = [{"date": year_end_dates[0], "nav": 1.0}]
current_holdings = None
benchmark_nav = 1.0  # Equal weight all ETFs
benchmark_history = [{"date": year_end_dates[0], "nav": 1.0}]

for i in range(len(year_end_dates) - 1):
    start_date = year_end_dates[i]
    end_date = year_end_dates[i+1]
    
    # Compute trailing 1-year return for all ETFs up to start_date
    trailing_returns = {}
    for code, rtn_dict in daily_rtn.items():
        # Find the 1-year lookback date
        start_idx = dates.index(start_date)
        lookback_start = None
        for j in range(start_idx, -1, -1):
            if dates[j] <= start_date and (start_idx - j) >= 240:  # ~1 year of trading days
                lookback_start = dates[j]
                break
        if lookback_start is None and start_idx > 0:
            lookback_start = dates[0]
        if lookback_start is None:
            continue
        
        # Compute cumulative return from lookback to start
        cum_ret = 1.0
        for d in dates:
            if d <= lookback_start: continue
            if d > start_date: break
            if d in rtn_dict:
                cum_ret *= (1 + rtn_dict[d])
        if cum_ret > 0:
            trailing_returns[code] = cum_ret
    
    # Top 10
    ranked = sorted(trailing_returns.items(), key=lambda x: x[1], reverse=True)
    top10 = [code for code, ret in ranked[:10]]
    current_holdings = [{"code": code, "weight": 0.1, "trailing_ret": trailing_returns[code]} for code in top10]
    
    if i == 0:
        current_holdings_display = [{"code": code, "weight": 0.1} for code in top10]
    
    # Simulate next year with top 10 equal weight
    start_idx = dates.index(start_date)
    end_idx = dates.index(end_date)
    
    segment_nav = 1.0
    segment_bench = 1.0
    daily_navs = []
    
    for j in range(start_idx + 1, end_idx + 1):
        d = dates[j]
        # Portfolio return = average of top 10 returns
        day_rets = [daily_rtn.get(code, {}).get(d, 0) for code in top10]
        day_ret = sum(day_rets) / len(day_rets) if day_rets else 0
        segment_nav *= (1 + day_ret)
        daily_navs.append({"date": d, "nav": round(nav * segment_nav, 4)})
        
        # Benchmark: equal weight all ETFs
        all_rets = [daily_rtn.get(code, {}).get(d, 0) for code in daily_rtn.keys()]
        all_ret = sum(all_rets) / len(all_rets) if all_rets else 0
        segment_bench *= (1 + all_ret)
    
    nav *= segment_nav
    benchmark_nav *= segment_bench
    
    nav_history.extend(daily_navs)
    
    print(f"  {start_date[:4]}→{end_date[:4]}: +{((segment_nav-1)*100):.1f}%  holdings={len(top10)}")

# Compute stats
total_return = round((nav - 1) * 100, 1)
years = len(year_end_dates) - 1
annual_return = round((nav ** (1/max(years,1)) - 1) * 100, 1)
bench_total = round((benchmark_nav - 1) * 100, 1)
bench_annual = round((benchmark_nav ** (1/max(years,1)) - 1) * 100, 1)

# Compute max drawdown
peak = nav_history[0]["nav"]
mdd = 0
for h in nav_history[1:]:
    if h["nav"] > peak: peak = h["nav"]
    dd = (peak - h["nav"]) / peak * 100
    if dd > mdd: mdd = dd

# Sharpe (simplified, annual)
daily_rets = []
for i in range(1, len(nav_history)):
    r = nav_history[i]["nav"] / nav_history[i-1]["nav"] - 1
    daily_rets.append(r)
avg_daily = sum(daily_rets) / len(daily_rets) if daily_rets else 0
std_daily = math.sqrt(sum((r-avg_daily)**2 for r in daily_rets) / len(daily_rets)) if daily_rets else 0
sharpe = round(avg_daily / std_daily * math.sqrt(252), 2) if std_daily > 0 else 0

result = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "strategy": {
        "name": "年化TOP10等权轮动",
        "description": "每年末选取过去一年收益率最高的10只ETF，等权配置，满仓持有，年度调仓",
        "n_assets": 10,
        "rebalance": "annual",
        "start_date": year_end_dates[0],
        "end_date": year_end_dates[-1],
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": round(mdd, 1),
        "sharpe": sharpe,
        "n_years": years,
    },
    "benchmark": {
        "name": "等权全ETF组合",
        "total_return": bench_total,
        "annual_return": bench_annual,
    },
    "nav_history": [{"date": h["date"], "nav": round(h["nav"], 4)} for h in nav_history[::5]]  # every 5th day to keep json small
}

with open("docs/strategy.json", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\nStrategy: {total_return}% total, {annual_return}% annual, {sharpe} Sharpe, -{mdd:.1f}% MDD")
print(f"Benchmark: {bench_total}% total, {bench_annual}% annual")
print("JSON written to docs/strategy.json")
