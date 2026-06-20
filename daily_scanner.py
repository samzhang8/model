#!/usr/bin/env python3
"""
每日信号扫描器 — A股多因子选股
规则：3日金叉21日 + MACD红柱 + 涨幅<5% + 非ST
"""
import akshare as ak
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')
from datetime import datetime

today = datetime.now().strftime('%Y-%m-%d')
print(f"🕐 AgentMatrixLab 每日扫描 | {today}")
print("=" * 50)

# 1. 获取行情
print("[1] 拉取行情...")
df = ak.stock_zh_a_spot_em()
df = df[~df['名称'].str.contains('ST|退|N|C', na=False)]
df = df[df['代码'].str.match(r'^[0-9]{6}$')]
print(f"   A股池: {len(df)} 只")

# 2. 计算技术指标（需要日线数据）
print("[2] 扫描金叉+MACD信号...")

# 用 T+1 数据替代逐只拉取
signals = []
sample_size = min(200, len(df))  # 先扫200只

for i, (_, row) in enumerate(df.head(sample_size).iterrows()):
    code = row['代码']
    name = row['名称']
    price = row['最新价']
    pct = row['涨跌幅']
    
    if pct >= 5:  # 涨幅>5%不追
        continue
    
    try:
        hist = ak.stock_zh_a_hist(symbol=code, period="daily",
                                  start_date="20250801", end_date=today,
                                  adjust="qfq")
        if len(hist) < 30:
            continue
        
        # 均线
        ma3 = hist['收盘'].rolling(3).mean().iloc[-1]
        ma3_prev = hist['收盘'].rolling(3).mean().iloc[-2]
        ma21 = hist['收盘'].rolling(21).mean().iloc[-1]
        ma21_prev = hist['收盘'].rolling(21).mean().iloc[-2]
        
        golden_cross = (ma3_prev <= ma21_prev) and (ma3 > ma21)
        
        # MACD
        ema12 = hist['收盘'].ewm(span=12).mean()
        ema26 = hist['收盘'].ewm(span=26).mean()
        macd = ema12 - ema26
        signal_line = macd.ewm(span=9).mean()
        macd_hist = macd - signal_line
        macd_red = macd_hist.iloc[-1] > 0
        
        if golden_cross and macd_red:
            signals.append({
                '代码': code, '名称': name, '现价': price,
                '涨幅%': pct, '成交量(亿)': round(row.get('成交额', 0)/1e8, 1),
                '换手率%': row.get('换手率', 0)
            })
            if len(signals) % 5 == 0:
                print(f"   已扫描 {i+1}/{sample_size}，发现 {len(signals)} 个信号")
                
    except Exception as e:
        continue

# 3. 输出
print(f"\n{'='*50}")
print(f"📊 今日信号: {len(signals)} 只")
print(f"{'='*50}")

if signals:
    df_signals = pd.DataFrame(signals)
    df_signals = df_signals.sort_values('涨幅%')
    for _, s in df_signals.iterrows():
        print(f"  {s['代码']} {s['名称']:6s} | ¥{s['现价']:.2f} | "
              f"{s['涨幅%']:+.2f}% | 换手{s['换手率%']:.1f}%")
else:
    print("  今日无符合条件的信号。")

print(f"\n⚠️ 本扫描不含基本面(净利增长)和板块热点过滤。")
print(f"🔗 信号对接掘金量化的API接口待开发。")
print(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
