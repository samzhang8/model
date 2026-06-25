#!/usr/bin/env python3
"""
资产排名看板 - 数据生成器
拉取2020-01-01至今的历史日线数据，计算指标，输出JSON供前端加载

指标：
1. 年化收益率
2. 最大回撤
3. 夏普比率 (统一0%无风险利率)
4. 卡玛比率 (年化收益/最大回撤)
5. 创新高最长天数 + 起止日期区间
"""

import urllib.request
import json
import math
import sys
from datetime import datetime

ASSETS = [
    # ═══ 宽基指数 ═══
    {"code": "510300", "prefix": "sh", "name": "沪深300ETF"},
    {"code": "510500", "prefix": "sh", "name": "中证500ETF"},
    {"code": "510050", "prefix": "sh", "name": "上证50ETF"},
    {"code": "159915", "prefix": "sz", "name": "创业板ETF"},
    {"code": "159949", "prefix": "sz", "name": "创业板50"},
    {"code": "588000", "prefix": "sh", "name": "科创50ETF"},
    {"code": "159552", "prefix": "sz", "name": "中证2000增强ETF"},
    {"code": "512100", "prefix": "sh", "name": "中证1000ETF"},
    {"code": "560110", "prefix": "sh", "name": "中证1000增强ETF"},
    {"code": "159901", "prefix": "sz", "name": "深证100ETF"},
    # ═══ 风格/策略 ═══
    {"code": "510880", "prefix": "sh", "name": "红利ETF(上证)"},
    {"code": "515080", "prefix": "sh", "name": "中证红利ETF"},
    {"code": "512890", "prefix": "sh", "name": "红利低波ETF"},
    # ═══ 国际指数 ═══
    {"code": "513100", "prefix": "sh", "name": "纳指ETF(国泰)"},
    {"code": "513500", "prefix": "sh", "name": "标普500ETF"},
    {"code": "159920", "prefix": "sz", "name": "恒生ETF"},
    {"code": "513030", "prefix": "sh", "name": "德国30ETF"},
    {"code": "513520", "prefix": "sh", "name": "日经ETF"},
    {"code": "513080", "prefix": "sh", "name": "法国CAC40ETF"},
    {"code": "513050", "prefix": "sh", "name": "中概互联"},
    # ═══ 行业板块 (无重叠) ═══
    {"code": "512880", "prefix": "sh", "name": "证券ETF"},
    {"code": "512010", "prefix": "sh", "name": "医药ETF"},
    {"code": "512660", "prefix": "sh", "name": "军工ETF"},
    {"code": "512480", "prefix": "sh", "name": "半导体ETF"},
    {"code": "512690", "prefix": "sh", "name": "酒ETF"},
    {"code": "515050", "prefix": "sh", "name": "5GETF"},
    {"code": "512800", "prefix": "sh", "name": "银行ETF"},
    {"code": "512200", "prefix": "sh", "name": "房地产ETF"},
    {"code": "515700", "prefix": "sh", "name": "新能源车ETF"},
    {"code": "515790", "prefix": "sh", "name": "光伏ETF"},
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
    # ═══ 商品 ═══
    {"code": "518880", "prefix": "sh", "name": "黄金ETF"},
    {"code": "518850", "prefix": "sh", "name": "黄金股票ETF"},
    {"code": "159980", "prefix": "sz", "name": "有色ETF"},
    {"code": "159985", "prefix": "sz", "name": "豆粕ETF"},
    # ═══ 债券/货基 ═══
    {"code": "511010", "prefix": "sh", "name": "国债ETF"},
    {"code": "511360", "prefix": "sh", "name": "短融ETF"},
    {"code": "511880", "prefix": "sh", "name": "银华日利ETF"},
    {"code": "511380", "prefix": "sh", "name": "可转债ETF"},
    {"code": "511520", "prefix": "sh", "name": "政金债ETF"},
    # ═══ REITs ═══
    {"code": "508056", "prefix": "sh", "name": "普洛斯REIT"},
    # ═══ 另类 ═══
    {"code": "600519", "prefix": "sh", "name": "贵州茅台"},
    {"code": "BTC", "prefix": "crypto", "name": "比特币"},
    {"code": "ETH", "prefix": "crypto", "name": "以太坊"},
    {"code": "TRX", "prefix": "crypto", "name": "波场"},
]

RISK_FREE_RATE = 0.0
TRADING_DAYS_PER_YEAR = 252


def fetch_history(symbol: str, prefix: str) -> list:
    """分3段拉取2020-01-01至今的前复权日线数据"""
    if prefix == "crypto":
        return fetch_crypto_history(symbol)

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
                stock_data = data.get("data", {}).get(f"{prefix}{symbol}", {})
                bars = stock_data.get("qfqday") or stock_data.get("day") or []
                all_bars.extend(bars)
        except Exception as e:
            print(f"  Error fetching {symbol} {start}-{end}: {e}", file=sys.stderr)

    seen = set()
    unique = []
    for bar in all_bars:
        date = bar[0]
        if date not in seen:
            seen.add(date)
            unique.append(bar)
    return unique


def fetch_crypto_history(symbol: str) -> list:
    """从Binance公共API拉取加密货币历史日线 (USD计价)"""
    binance_symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "TRX": "TRXUSDT"}[symbol]
    all_bars = []
    import time

    end_time = int(time.time() * 1000)

    while len(all_bars) < 2500 and end_time > 0:
        url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval=1d&limit=1000&endTime={end_time}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                klines = json.loads(resp.read())
                if not klines:
                    break
                for k in klines:
                    ts = k[0] / 1000
                    date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                    price = str(float(k[4]))
                    all_bars.append([date_str, str(float(k[1])), price, str(float(k[2])), str(float(k[3])), str(float(k[5]))])
                end_time = klines[0][0] - 86400000
        except Exception as e:
            print(f"  Binance error for {symbol}: {e}", file=sys.stderr)
            break
        time.sleep(0.5)

    seen = set()
    unique = []
    for bar in all_bars:
        date = bar[0]
        if date not in seen:
            seen.add(date)
            unique.append(bar)
    unique.sort(key=lambda x: x[0])
    return unique


def compute_metrics(bars: list, code: str, name: str) -> dict:
    """计算各项指标"""
    if len(bars) < 2:
        return {"code": code, "name": name, "error": "数据不足"}

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

    # ---- 5. 创新高最长天数 + 起止日期 ----
    running_peak = closes[0]
    high_indices = [0]

    for i in range(1, n_days):
        if closes[i] > running_peak:
            high_indices.append(i)
            running_peak = closes[i]

    high_indices.append(n_days - 1)

    # 最后一个真正创新高的位置（不含最后追加的终点索引）
    last_peak_idx = high_indices[-2] if len(high_indices) >= 2 else 0
    last_peak_date = dates[last_peak_idx]
    days_since_last_peak = n_days - 1 - last_peak_idx

    max_high_gap = 0
    max_high_gap_start = ""
    max_high_gap_end = ""
    for i in range(1, len(high_indices)):
        gap = high_indices[i] - high_indices[i-1]
        if gap > max_high_gap:
            max_high_gap = gap
            max_high_gap_start = dates[high_indices[i-1]]
            max_high_gap_end = dates[high_indices[i]]

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
        "max_high_gap_days": max_high_gap,
        "max_high_gap_start": max_high_gap_start,
        "max_high_gap_end": max_high_gap_end,
        "last_peak_date": last_peak_date,
        "days_since_last_peak": days_since_last_peak,
        "total_value_10k": round(10000 * (1 + total_return), 0),
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
        gap_info = ""
        if metrics.get("max_high_gap_start"):
            gap_info = f"  无新高: {metrics['max_high_gap_start']} ~ {metrics['max_high_gap_end']} ({metrics['max_high_gap_days']}天)"
        print(f"    年化: {metrics['annual_return']}%  回撤: {metrics['max_drawdown']}%  夏普: {metrics['sharpe']}  卡玛: {metrics['calmar']}{gap_info}  1万→{metrics['total_value_10k']:.0f}元")

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "risk_free_rate": RISK_FREE_RATE,
        "start_date": "2020-01-01",
        "assets": results,
    }

    output_path = "/opt/quant/docs/metrics.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"\n✅ 数据已写入 {output_path}")
    print(f"   {len(results)} 个资产, 生成时间: {output['generated_at']}")

    print("\n📈 年化收益率排名:")
    for i, a in enumerate(sorted(results, key=lambda x: x["annual_return"], reverse=True)):
        gap_str = ""
        if a.get("max_high_gap_start"):
            gap_str = f"  {a['max_high_gap_start']}~{a['max_high_gap_end']}"
        print(f"  {i+1}. {a['name']:<15} 年化{a['annual_return']:>6.1f}%  回撤{a['max_drawdown']:>6.1f}%  夏普{a['sharpe']:>5.2f}  卡玛{a['calmar']:>5.2f}  无新高{a['max_high_gap_days']:>4.0f}天{gap_str}")


if __name__ == "__main__":
    main()
