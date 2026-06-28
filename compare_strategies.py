#!/usr/bin/env python3
"""策略对比工具 — 任意两策略并排比较净值+风险"""
import json, math

with open('docs/strategies.json') as f: db = json.load(f)

# Find two strategies to compare
s1 = db['strategies'][0]  # Best
s2 = db['strategies'][5]  # 6th best

def metrics(s):
    nav = [h['nav'] for h in s['nav_history']]
    daily = [nav[i]/nav[i-1]-1 for i in range(1,len(nav))]
    avg = sum(daily)/len(daily)
    std = math.sqrt(sum((r-avg)**2 for r in daily)/len(daily))
    return {
        'name': s['name'],
        'ann': s['annual_return'],
        'sharpe': round(avg/std*math.sqrt(12),2) if std>0 else 0,
        'mdd': s['max_drawdown'],
        'vol': round(std*math.sqrt(12)*100, 1),
        'calmar': round(s['annual_return']/s['max_drawdown'], 2),
    }

m1, m2 = metrics(s1), metrics(s2)

print(f'策略对比')
print(f'{"":30} {m1["name"]:>20} vs {m2["name"]:>20}')
print(f'{"年化收益":30} {m1["ann"]:>19.1f}% {m2["ann"]:>20.1f}%')
print(f'{"夏普比率":30} {m1["sharpe"]:>20.2f} {m2["sharpe"]:>20.2f}')
print(f'{"最大回撤":30} {m1["mdd"]:>19.1f}% {m2["mdd"]:>20.1f}%')
print(f'{"年化波动":30} {m1["vol"]:>19.1f}% {m2["vol"]:>20.1f}%')
print(f'{"Calmar":30} {m1["calmar"]:>20.2f} {m2["calmar"]:>20.2f}')
print()

# Correlation
nav1 = [h['nav'] for h in s1['nav_history']]
nav2 = [h['nav'] for h in s2['nav_history']]
d1 = [nav1[i]/nav1[i-1]-1 for i in range(1,len(nav1))]
d2 = [nav2[i]/nav2[i-1]-1 for i in range(1,len(nav2))]
m = min(len(d1), len(d2))
corr = sum((a-sum(d1[:m])/m)*(b-sum(d2[:m])/m) for a,b in zip(d1[:m],d2[:m])) / m
corr /= (math.sqrt(sum((x-sum(d1[:m])/m)**2 for x in d1[:m])/m) * math.sqrt(sum((x-sum(d2[:m])/m)**2 for x in d2[:m])/m) + 1e-10)

print(f'收益相关性: {corr:.3f}')
if abs(corr) < 0.3:
    print('✅ 低相关，适合组合')
elif abs(corr) < 0.5:
    print('🟡 中等相关，可分散')
else:
    print('🔴 高相关，不宜配对')

# Output comparison JSON for possible chart
comp = {
    'strategy1': {'name': m1['name'], 'nav': s1['nav_history'][::2], 'ann': m1['ann'], 'mdd': m1['mdd']},
    'strategy2': {'name': m2['name'], 'nav': s2['nav_history'][::2], 'ann': m2['ann'], 'mdd': m2['mdd']},
    'correlation': round(corr, 3)
}

with open('docs/compare_top2.json', 'w') as f:
    json.dump(comp, f, ensure_ascii=False, indent=2)
print(f'\n对比数据: docs/compare_top2.json')
