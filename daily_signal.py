#!/usr/bin/env python3
"""
📊 量化日报 v2 — 产品级报告 + 历史信号验证
"""
import json, urllib.request, math, os
from datetime import datetime, timedelta

OUTPUT_FILE = "/tmp/daily_report.md"

def load_data():
    with open("docs/metrics.json") as f:
        assets = json.load(f)['assets']
    with open("docs/strategies.json") as f:
        strategies = json.load(f)['strategies']
    return assets, strategies

def format_pct(v):
    if v is None: return "—"
    return f"{v:+.1f}%"

def signal_label(name, nav_history):
    if len(nav_history) < 4: return "⚪"
    recent = [h['nav'] for h in nav_history[-4:]]
    if recent[-1] > recent[0] * 1.03: return "🟢"
    if recent[-1] < recent[0] * 0.97: return "🔴"
    return "🟡"

def signal_strength(name, nav_history):
    if len(nav_history) < 12: return 50
    recent = [h['nav'] for h in nav_history[-12:]]
    up = sum(1 for i in range(1,len(recent)) if recent[i] > recent[i-1])
    return round(up / (len(recent)-1) * 100)

def validate_signals(strategies):
    """验证历史信号准确率"""
    results = []
    for s in strategies[:8]:
        nav = s.get('nav_history', [])
        if len(nav) < 24: continue
        monthly = [nav[i:i+4] for i in range(0, len(nav)-4, 4) if i+4 < len(nav)]
        correct = 0
        for m in monthly:
            signal_up = m[-1]['nav'] > m[0]['nav']
            actual_up = (nav[nav.index(m[-1])+1]['nav'] if nav.index(m[-1])+1 < len(nav) else m[-1]['nav']) > m[-1]['nav']
            if signal_up == actual_up: correct += 1
        acc = round(correct / len(monthly) * 100, 1) if monthly else 0
        results.append({'name': s['name'], 'accuracy': acc, 'annual': s['annual_return'], 'sharpe': s['sharpe']})
    return sorted(results, key=lambda x: x['accuracy'], reverse=True)

def generate_report():
    assets, strategies = load_data()
    ranked = sorted(assets, key=lambda a: a.get('annual_return', -99), reverse=True)
    top5_strats = strategies[:5]
    signal_valid = validate_signals(strategies)
    
    today = datetime.now()
    report = []
    
    # Header
    report.append(f"# 📊 量化日报")
    report.append(f"## {today.strftime('%Y年%m月%d日')} · {['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]}")
    report.append(f"> 由 AI量化引擎自动生成 | 基于 A 股 48 策略 + 888 因子 + 68 ETF")
    report.append("")
    
    # Market Overview
    report.append("## 🔥 今日最强 ETF TOP5")
    report.append("")
    report.append("| # | ETF | 年化 | 夏普 | 回撤 | 信号 |")
    report.append("|---|-----|:--:|:--:|:--:|:--:|")
    for i, a in enumerate(ranked[:5]):
        ret = a.get('annual_return', 0)
        sharpe = a.get('sharpe', 0)
        md = a.get('max_drawdown', 0)
        sig = "📈" if ret > 10 else ("📉" if ret < -5 else "➡️")
        report.append(f"| {i+1} | **{a['name']}**({a['code']}) | {ret:.1f}% | {sharpe:.2f} | {md:.1f}% | {sig} |")
    report.append("")
    
    # Strategy Signals
    report.append("## 🎯 TOP5 策略实时信号")
    report.append("")
    for i, s in enumerate(top5_strats):
        nav = s.get('nav_history', [])
        signal = signal_label(s['name'], nav)
        strength = signal_strength(s['name'], nav)
        bar = "█" * (strength // 10) + "░" * (10 - strength // 10)
        
        report.append(f"### {i+1}. {signal} {s['name']}")
        report.append(f"- 年化 **{s['annual_return']:.1f}%** | 夏普 **{s['sharpe']:.2f}** | 回撤 **-{s['max_drawdown']:.1f}%**")
        report.append(f"- 信号强度: `{bar}` {strength}%")
        
        # Recent nav trend
        if len(nav) >= 6:
            recent_nav = [h['nav'] for h in nav[-6:]]
            trend = "↑" if recent_nav[-1] > recent_nav[0] else "↓"
            change = (recent_nav[-1]/recent_nav[0] - 1)*100
            report.append(f"- 近期: {trend} {change:+.1f}%")
        report.append("")
    
    # Validation Section
    report.append("## 📋 历史信号验证")
    report.append("")
    report.append("| 策略 | 准确率 | 年化 | 夏普 | 评级 |")
    report.append("|------|:--:|:--:|:--:|:--:|")
    for v in signal_valid[:8]:
        stars = "⭐" * min(5, int(v['accuracy']/20 + 1))
        report.append(f"| {v['name']} | {v['accuracy']}% | {v['annual']:.1f}% | {v['sharpe']:.2f} | {stars} |")
    report.append("")
    
    # Risk Warnings
    report.append("## ⚠️ 风险警示")
    report.append("")
    losers = [a for a in ranked if a.get('annual_return', 0) < -5][:3]
    if losers:
        report.append("**🚫 需规避 ETF:**")
        for a in losers:
            report.append(f"- {a['name']}({a['code']}): 年化 {a['annual_return']:.1f}%")
    report.append("")
    
    # Allocation suggestion
    report.append("## 💡 今日配置建议")
    report.append("")
    top_etf = ranked[:3]
    top_strat = strategies[:3]
    report.append(f"- 🟢 **进攻仓位 60%**: {', '.join(a['name'] for a in top_etf[:2])}")
    report.append(f"- 🟡 **对冲仓位 30%**: 低波动率策略(年化{top_strat[0]['annual_return']:.0f}%)")
    report.append(f"- 🔴 **现金仓位 10%**: 等待回调加仓")
    report.append("")
    
    report.append("---")
    report.append(f"*本报告由 Hermes AI 量化引擎自动生成 | {today.strftime('%Y-%m-%d %H:%M')}*")
    report.append(f"*策略面板: samzhang8.github.io/model/strategy.html*")
    
    report_text = "\n".join(report)
    
    # Save to file
    with open(OUTPUT_FILE, 'w') as f:
        f.write(report_text)
    
    # Also save archive
    archive_dir = "/home/ubuntu/.hermes/data/signal_reports"
    os.makedirs(archive_dir, exist_ok=True)
    archive_file = f"{archive_dir}/{today.strftime('%Y-%m-%d')}.md"
    with open(archive_file, 'w') as f:
        f.write(report_text)
    
    # Accumulate accuracy tracker
    acc_file = f"{archive_dir}/accuracy_tracker.json"
    tracker = {}
    if os.path.exists(acc_file):
        with open(acc_file) as f:
            tracker = json.load(f)
    
    for v in signal_valid:
        name = v['name']
        if name not in tracker:
            tracker[name] = []
        tracker[name].append({'date': today.strftime('%Y-%m-%d'), 'accuracy': v['accuracy']})
    
    with open(acc_file, 'w') as f:
        json.dump(tracker, f, indent=2)
    
    print(report_text)
    return report_text

if __name__ == '__main__':
    generate_report()
