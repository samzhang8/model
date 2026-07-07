#!/usr/bin/env python3
"""策略数据校验脚本 v2 — 收紧容差 + 扩展校验"""
import json, math

with open('docs/strategies.json') as f:
    db = json.load(f)

errors = []; warnings = []

for s in db['strategies']:
    n = s['name']
    tr = s.get('total_return', 0)
    ann = s.get('annual_return', 0)
    mdd = abs(s.get('max_drawdown', 0))
    cal = s.get('calmar', 0)
    v10k = s.get('total_value_10k', 0)
    sharpe = s.get('sharpe', 0)
    vol = s.get('annual_vol', 0)
    
    # 1. total_return vs 1万→总额 (tight 1% tolerance)
    if isinstance(tr, (int,float)) and v10k > 0:
        expected = round(10000 * (1 + tr/100))
        if abs(v10k - expected) / max(expected, 1) > 0.01:
            errors.append(f'{n}: 1万→{v10k:,} ≠ 预期{expected:,} (tr={tr}%)')
    
    # 2. Calmar ≈ ann / |mdd| (5% tolerance)
    if mdd > 0 and ann != 0:
        exp_cal = round(ann / mdd, 2)
        if cal > 0 and abs(cal - exp_cal) / max(exp_cal, 0.01) > 0.05:
            errors.append(f'{n}: calmar={cal} ≠ {exp_cal}')
    
    # 3. Sharpe ≈ ann / vol (rough check)
    if vol > 0 and ann > 0 and sharpe > 0:
        exp_sharpe = ann / vol
        if abs(sharpe - exp_sharpe) > 1.5:
            warnings.append(f'{n}: sharpe={sharpe} vs ann/vol≈{exp_sharpe:.1f}')
    
    # 4. Year consistency: total_return ≈ (1+ann)^years - 1
    start = s.get('start_date', '2010')
    end = s.get('end_date', '2024')
    try:
        yrs = (int(end[:4]) - int(start[:4]))
        if yrs > 1 and ann > 0 and tr > 0:
            exp_tr = ((1 + ann/100) ** yrs - 1) * 100
            if abs(tr - exp_tr) / max(exp_tr, 1) > 0.20:
                warnings.append(f'{n}: {yrs}年 tr={tr}% vs 预期≈{exp_tr:.0f}%')
    except: pass
    
    # 5. Null/abnormal checks
    for field in ['annual_return', 'total_return', 'sharpe']:
        if s.get(field) is None or s.get(field) == 0:
            if field == 'sharpe' and ann < 3: continue
            warnings.append(f'{n}: {field}={s.get(field)}')

print(f'校验 {db["total"]} 个策略')
print(f'错误: {len(errors)} | 警告: {len(warnings)}')
if errors:
    print('\n❌ 错误:')
    for e in errors[:15]: print(f'  {e}')
if warnings:
    print('\n⚠️ 警告:')
    for w in warnings[:15]: print(f'  {w}')
if not errors:
    print('\n✅ 全部通过')
exit(1 if errors else 0)
