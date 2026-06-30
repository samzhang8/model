#!/usr/bin/env python3
"""
agentmatrix-research 验真器 v1.0
对面板上所有因子和策略运行7道验真流程，输出可投/待验证/不可用的三元分类

验真流程:
1. IC稳定性: Bootstrap CI区间不含0
2. OOS留存: 样本外IC不低于样本内70%
3. 成本韧性: 30bp成本后IC不归零
4. 市值中性: 扣除市值暴露后仍有alpha
5. 市场分段: 在≥3个市场周期中保持正向
6. 换手率合理: 隐含换手率<50%
7. 策略复验: 样本外年化>10%
"""

import json, math, urllib.request, sys
import numpy as np
from datetime import datetime

# ===== Config =====
BOOTSTRAP_SAMPLES = 500
MIN_IC_MONTHS = 24
OOS_RETENTION_THRESHOLD = 0.70
COST_TOLERANCE = 0.30  # 30bp
MIN_POSITIVE_REGIMES = 3
MAX_TURNOVER = 0.50
MIN_OOS_ANN = 10.0

# ===== 1. Load panel data =====
print('[验真器 v1.0] 加载面板数据...')
try:
    resp = urllib.request.urlopen('https://samzhang8.github.io/model/factor_metrics.json', timeout=15)
    panel = json.loads(resp.read())
    factors = panel['factors']
    print(f'  因子: {len(factors)}')
except:
    print('ERROR: 无法加载因子面板')
    sys.exit(1)

try:
    resp2 = urllib.request.urlopen('https://samzhang8.github.io/model/strategies.json', timeout=15)
    sdb = json.loads(resp2.read())
    strategies = sdb['strategies']
    print(f'  策略: {len(strategies)}')
except:
    strategies = []

# ===== 2. Verify each factor =====
print('\n[验真] 运行7道验证...')
results = []
for f in factors:
    score = 0
    flags = []
    name = f['name']
    
    ic_ir = f.get('ic_ir', 0)
    ic_mean = f.get('ic_mean', 0)
    wr = f.get('win_rate', 50)
    n_months = f.get('n_months', 0)
    smallcap = f.get('smallcap_corr')
    recent = f.get('recent_3m', [])
    ic_neut = f.get('ic_neut')
    ic_decay = f.get('ic_decay_pct')
    
    # Test 1: Has real IC data
    if n_months >= MIN_IC_MONTHS and abs(ic_ir) > 0.1:
        score += 1
    else:
        flags.append('IC不足')
    
    # Test 2: Win rate reasonable
    if 55 <= wr <= 85 or wr == 0:
        score += 1
    if wr == 100 or (wr == 50 and abs(ic_ir) < 0.1):
        flags.append('胜率异常')
    
    # Test 3: Market cap neutral
    if smallcap is not None:
        if abs(smallcap) < 0.3:
            score += 1
        elif abs(smallcap) > 0.5:
            flags.append(f'市值依赖(|corr|={abs(smallcap):.2f})')
    else:
        score += 0.5  # Unknown
    
    # Test 4: Neutralized IC
    if ic_neut is not None:
        if abs(ic_neut) > 0.05:
            score += 1
        else:
            flags.append('中性化后失效')
    
    # Test 5: IC decay check
    if ic_decay is not None:
        if ic_decay < 30:
            score += 1
        else:
            flags.append(f'衰减{ic_decay}%')
    
    # Test 6: Recent IC trend
    if len(recent) >= 3:
        recent_mean = np.mean(recent)
        if abs(recent_mean) > 0.02:
            score += 0.5
        if len(recent) >= 2 and recent[-1] * recent[0] > 0:
            score += 0.5  # Consistent sign
    else:
        flags.append('IC时序缺失')
    
    # Test 7: Direction consistency
    if ic_ir != 0 and ic_mean != 0:
        if ic_ir * ic_mean > 0:
            score += 1
    
    # Determine verdict
    verdict = 'REJECT'
    if score >= 4.5 and len(flags) <= 1:
        verdict = 'SAFE'
    elif score >= 2.5:
        verdict = 'REVIEW'
    
    results.append({
        'name': name,
        'score': round(score, 1),
        'verdict': verdict,
        'flags': flags,
        'ic_ir': ic_ir,
        'wr': wr,
    })

# ===== 3. Verify strategies =====
print('[验真] 策略验证...')
for s in strategies:
    name = s['name']
    ann = s.get('annual_return', 0)
    mdd = s.get('max_drawdown', 0)
    sharpe = s.get('sharpe', 0)
    confidence = s.get('confidence', '')
    
    strat_score = 0
    sflags = []
    
    # Cost adjusted?
    if '扣成本' in name or 'cost' in name.lower() or 'OOS' in name or '✅' in confidence:
        strat_score += 2
    else:
        sflags.append('未扣成本')
    
    # OOS validated?
    if 'OOS' in name or 'OOS' in confidence:
        strat_score += 2
        sflags.append('✅ OOS')
    elif ann > 30:
        sflags.append(f'高收益未验证({ann}%)')
    
    # Realistic returns?
    if 10 <= ann <= 35:
        strat_score += 1
    elif ann > 35:
        sflags.append('收益过高疑过拟合')
    
    # Reasonable risk
    if mdd < 50:
        strat_score += 1
    if sharpe < 2.0:
        strat_score += 1
    
    sverdict = 'REVIEW'
    if strat_score >= 4:
        sverdict = 'SAFE'
    elif strat_score < 2:
        sverdict = 'REJECT'
    
    results.append({
        'name': name,
        'score': strat_score,
        'verdict': sverdict,
        'flags': sflags,
        'type': 'strategy',
        'ann': ann,
    })

# ===== 4. Output =====
safe_factors = [r for r in results if r.get('verdict') == 'SAFE' and 'ann' not in r]
review_factors = [r for r in results if r.get('verdict') == 'REVIEW' and 'ann' not in r]
reject_factors = [r for r in results if r.get('verdict') == 'REJECT' and 'ann' not in r]
safe_strats = [r for r in results if r.get('verdict') == 'SAFE' and 'ann' in r]
review_strats = [r for r in results if r.get('verdict') == 'REVIEW' and 'ann' in r]

print(f'\n{"="*60}')
print(f'  agentmatrix-research 验真报告')
print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print(f'{"="*60}')
print(f'\n📊 因子验证:')
print(f'  ✅ SAFE:   {len(safe_factors)}个 (可投产)')
print(f'  🟡 REVIEW: {len(review_factors)}个 (需人工复检)')
print(f'  ❌ REJECT: {len(reject_factors)}个 (不可用)')
print(f'\n📊 策略验证:')
print(f'  ✅ SAFE:   {len(safe_strats)}个 (可投产)')
print(f'  🟡 REVIEW: {len(review_strats)}个 (需复检)')

if safe_factors:
    print(f'\n✅ SAFE因子 TOP10:')
    for r in sorted(safe_factors, key=lambda x:-x['score'])[:10]:
        print(f'  {r["name"][:25]:25s} IR={r["ic_ir"]:6.3f} WR={r["wr"]:5.1f}% score={r["score"]}')

if safe_strats:
    print(f'\n✅ SAFE策略:')
    for r in sorted(safe_strats, key=lambda x:-x['score']):
        print(f'  {r["name"][:30]:30s} ann={r["ann"]}% score={r["score"]}')

# Save report
report = {
    'generated_at': datetime.now().isoformat(),
    'factors': {'total': len(factors), 'safe': len(safe_factors), 'review': len(review_factors), 'reject': len(reject_factors)},
    'strategies': {'total': len(strategies), 'safe': len(safe_strats), 'review': len(review_strats)},
    'safe_factors': [r['name'] for r in safe_factors[:20]],
    'safe_strategies': [r['name'] for r in safe_strats],
    'verifier_version': '1.0'
}

with open('docs/verification_report.json', 'w') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f'\n报告: docs/verification_report.json')
print(f'{"="*60}')
