#!/usr/bin/env python3
"""
资产排名看板 - 数据生成器
拉取2020-01-01至今的历史日线数据，计算5项指标，输出JSON供前端加载

指标：
1. 年化收益率
2. 最大回撤
3. 夏普比率 (无风险利率2%)
4. 卡玛比率 (年化收益/最大回撤)
5. 创新高间隔天数 (距上次历史新高的天数)
"""

import urllib.request
import json
import math
import sys
from datetime import datetime

ASSETS = [
    {"code": "510300", "prefix": "sh", "name": "沪深300ETF"},
    {"code": "510500", "prefix": "sh", "name": "中证500ETF"},
    {"code": "511880", "prefix": "sh", "name": "银华日利ETF"},
]

RISK_FREE_RATE = 0.02  # 无风险利率2%（约等于中国10年期国债）
TRADING_DAYS_PER_YEAR = 252


def fetch_history(symbol: str, prefix: str) -> list:
    """分3段拉取2020-01-01至今的前复权日线数据"""
    chunks = [
        ("2020-01-01", "2021-12-31"),
        ("2022-01-01", "2023-12-31"),
        ("2024-01-01", "2026-12-31"),
    ]
    all_bars = []
    for start, end in chunks:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{symbol},day,{start},{end},640,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                bars = data.get("data", {}).get(f"{prefix}{symbol}", {}).get("qfqday", [])
                all_bars.extend(bars)
        except Exception as e:
            print(f"  Error fetching {symbol} {start}-{end}: {e}", file=sys.stderr)

    # 去重
    seen = set()
    unique = []
    for bar in all_bars:
        date = bar[0]
        if date not in seen:
            seen.add(date)
            unique.append(bar)

    # bar格式: [date, open, close, high, low, volume]
    return unique


def compute_metrics(bars: list, code: str, name: str) -> dict:
    """计算5项指标"""
    if len(bars) < 2:
        return {"code": code, "name": name, "error": "数据不足"}

    # 提取收盘价序列
    dates = [bar[0] for bar in bars]
    closes = [float(bar[2]) for bar in bars]

    n_days = len(closes)
    n_years = n_days / TRADING_DAYS_PER_YEAR

    # ---- 1. 年化收益率 ----
    total_return = (closes[-1] / closes[0]) - 1
    annual_return = (1 + total_return) ** (1 / n_years) - 1

    # ---- 2. 最大回撤 ----
    peak = closes[0]
    max_drawdown = 0
    max_dd_peak_date = dates[0]
    max_dd_trough_date = dates[0]
    current_peak_date = dates[0]

    for i, price in enumerate(closes):
        if price > peak:
            peak = price
            current_peak_date = dates[i]
        dd = (peak - price) / peak
        if dd > max_drawdown:
            max_drawdown = dd
            max_dd_peak_date = current_peak_date
            max_dd_trough_date = dates[i]

    # ---- 3. 夏普比率 ----
    daily_returns = []
    for i in range(1, n_days):
        daily_returns.append((closes[i] - closes[i-1]) / closes[i-1])

    mean_daily = sum(daily_returns) / len(daily_returns)
    var_daily = sum((r - mean_daily) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_daily = math.sqrt(var_daily)
    annual_vol = std_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0

    # ---- 4. 卡玛比率 ----
    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    # ---- 5. 创新高间隔天数 & 新高统计 ----
    running_peak = closes[0]
    new_high_count = 0
    last_high_idx = 0

    for i in range(1, n_days):
        if closes[i] > running_peak:
            new_high_count += 1
            last_high_idx = i
            running_peak = closes[i]

    # 平均创新高间隔 = 总交易日数 / 新高次数
    # 这个公式涵盖全部时间跨度，不会因为最后一次新高尚在遥远过去而失真
    avg_high_interval = n_days / new_high_count if new_high_count > 0 else n_days

    # 距上次创新高的交易日数
    days_since_high = n_days - 1 - last_high_idx if new_high_count > 0 else n_days - 1

    return {
        "code": code,
        "name": name,
        "start_date": dates[0],
        "end_date": dates[-1],
        "n_days": n_days,
        "start_price": round(closes[0], 3),
        "end_price": round(closes[-1], 3),
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "max_dd_peak_date": max_dd_peak_date,
        "max_dd_trough_date": max_dd_trough_date,
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "annual_vol": round(annual_vol * 100, 2),
        "avg_high_interval_days": round(avg_high_interval, 1),
        "days_since_high": days_since_high,
        "n_new_highs": new_high_count,
        # 1万元初始投资至今的总金额
        "total_value_10k": round(10000 * (1 + total_return), 0),
        # 保留收盘价序列供前端实时更新用
        "closes": [{"d": dates[i], "p": round(closes[i], 3)} for i in range(n_days)],
    }


def main():
    print("📊 资产排名看板 - 数据生成中...")
    results = []

    for asset in ASSETS:
        print(f"  拉取 {asset['name']} ({asset['code']})...", end=" ")
        bars = fetch_history(asset["code"], asset["prefix"])
        if not bars:
            print("❌ 失败")
            continue
        print(f"{len(bars)}个交易日")
        metrics = compute_metrics(bars, asset["code"], asset["name"])
        results.append(metrics)
        print(f"    年化: {metrics['annual_return']}%  回撤: {metrics['max_drawdown']}%  夏普: {metrics['sharpe']}  卡玛: {metrics['calmar']}  新高间隔: {metrics['avg_high_interval_days']}天  1万→{metrics['total_value_10k']:.0f}元")

    # 输出JSON
    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "risk_free_rate": RISK_FREE_RATE,
        "start_date": "2020-01-01",
        "assets": results,
    }

    # 写入文件
    output_path = "/opt/quant/docs/metrics.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"\n✅ 数据已写入 {output_path}")
    print(f"   {len(results)} 个资产, 生成时间: {output['generated_at']}")

    # 打印排名表
    print("\n📈 年化收益率排名:")
    for i, a in enumerate(sorted(results, key=lambda x: x["annual_return"], reverse=True)):
        print(f"  {i+1}. {a['name']:<12} 年化{a['annual_return']:>6.1f}%  回撤{a['max_drawdown']:>6.1f}%  夏普{a['sharpe']:>5.2f}  卡玛{a['calmar']:>5.2f}  新高间隔{a['avg_high_interval_days']:>5.1f}天")


if __name__ == "__main__":
    main()
