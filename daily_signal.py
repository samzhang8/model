#!/usr/bin/env python3
"""每日量化信号日报 — 产品雏形 v1"""
import json, urllib.request, math, time
from datetime import datetime

# Load asset list
with open("docs/metrics.json") as f:
    assets = json.load(f)['assets']

# Load strategy list
with open("docs/strategies.json") as f:
    strategies = json.load(f)['strategies']

# ===== 1. ETF实时信号 =====
print("# 📊 每日量化信号日报")
print(f"## {datetime.now().strftime('%Y-%m-%d %A')}")
print()

# Get latest ETF NAV values
print("## 🔥 今日ETF TOP5动量信号")
print()
# Sort by annual return (proxy for current momentum direction)
ranked = sorted(assets, key=lambda a: a.get('annual_return', -99), reverse=True)
for i, a in enumerate(ranked[:5]):
    ret = a.get('annual_return', 0)
    sharpe = a.get('sharpe', 0)
    md = a.get('max_drawdown', 0)
    print(f"  {i+1}. **{a['name']}**({a['code']}) — 年化{ret:.1f}% 夏普{sharpe:.2f} 回撤{md:.1f}%")

print()
print("## ❄️ 今日ETF BOTTOM5需要规避")
for i, a in enumerate(ranked[-5:]):
    ret = a.get('annual_return', 0)
    print(f"  {i+1}. {a['name']}({a['code']}) — 年化{ret:.1f}%")

# ===== 2. 策略轮动信号 =====
print()
print("## 🎯 TOP3策略当前信号")
print()

# Top 3 strategies by annual return
top3 = strategies[:3]
for i, s in enumerate(top3):
    nav = s.get('nav_history', [])
    if len(nav) < 2:
        continue
    
    latest = nav[-1]['nav']
    prev = nav[-2]['nav'] if len(nav) >= 2 else latest
    mom = (latest / prev - 1) * 100
    
    # Determine if strategy is in drawdown
    peak = max(h['nav'] for h in nav)
    dd = (latest / peak - 1) * 100
    
    status = "🟢 持有" if mom > 0 else ("🟡 关注" if mom > -2 else "🔴 减仓")
    
    print(f"### {i+1}. {s['name']}")
    print(f"- 年化: {s['annual_return']}% | 夏普: {s['sharpe']}")
    print(f"- 当前状态: {status}")
    print(f"- 近期趋势: {'↑' if mom > 0 else '↓'} {mom:+.1f}%")
    if dd < -5:
        print(f"- ⚠️ 回撤中: {dd:.1f}%（距峰{peak:.2f}）")
    print()

# ===== 3. 风险提示 =====
print("---")
print("## ⚠️ 风险提示")
print()
print("* 本日报由Hermes自动生成，基于历史数据回测，不构成投资建议")
print(f"* 策略信号由`samzhang8.github.io/model/`三面板实时算法输出")
print(f"* 更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
