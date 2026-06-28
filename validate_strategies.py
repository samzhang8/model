#!/usr/bin/env python3
"""策略数据校验脚本 — 自动检查所有策略的数据合理性"""
import json, math

with open('docs/strategies.json') as f:
    db = json.load(f)

errors = []
warnings = []

for s in db['strategies']:
    n = s['name']
    tr = s.get('total_return', 0)
    ann = s.get('annual_return', 0)
    mdd = abs(s.get('max_drawdown', 0))
    cal = s.get('calmar', 0)
    v10k = s.get('total_value_10k', 0)
    sharpe = s.get('sharpe', 0)
    vol = s.get('annual_vol', 0)
    
    # 1. total_return vs 1万→总额 consistency
    if isinstance(tr, (int,float)) and tr is not None and v10k > 0:
        expected = round(10000 * (1 + tr/100))
        if abs(v10k - expected) / max(expected, 1) > 0.15:
            errors.append(f'{n}: total_return={tr}% 预期1万→¥{expected:,} 实际¥{v10k:,}')
    
    # 2. Calmar ≈ ann / |mdd|
    if mdd > 0 and ann != 0:
        expected_cal = round(ann / mdd, 2)
        if cal > 0 and abs(cal - expected_cal) / max(expected_cal, 0.01) > 0.15:
            errors.append(f'{n}: calmar={cal} 预期≈{expected_cal} (ann={ann}/{mdd})')
    
    # 3. Check for null/zero anomalies
    for field in ['annual_return', 'total_return', 'sharpe', 'calmar']:
        v = s.get(field)
        if v is None or (isinstance(v, str) and 'undefined' in str(v)):
            errors.append(f'{n}: {field} is undefined/null')
    if mdd == 0 and ann > 0:
        warnings.append(f'{n}: max_drawdown=0 but annual_return={ann}%')
    if sharpe == 0 and ann > 10:
        warnings.append(f'{n}: sharpe=0(可能需要重算)')

print(f'校验 {db["total"]} 个策略')
print(f'错误: {len(errors)} | 警告: {len(warnings)}')
print()
if errors:
    print('❌ 错误:')
    for e in errors[:10]:
        print(f'  {e}')
if warnings:
    print('⚠️ 警告:')
    for w in warnings[:10]:
        print(f'  {w}')
if not errors and not warnings:
    print('✅ 全部通过')

exit(1 if errors else 0)
