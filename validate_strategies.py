#!/usr/bin/env python3
"""策略数据校验脚本 v2 — 8项自动检查"""
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
    conf = s.get('confidence', '')
    
    # 1. total_return vs 1万→总额 consistency (tightened from 15% to 2%)
    if isinstance(tr, (int,float)) and tr is not None and v10k > 0:
        expected = round(10000 * (1 + tr/100))
        if abs(v10k - expected) / max(expected, 1) > 0.02:
            errors.append(f'{n}: total_return={tr}% 预期1万→¥{expected:,} 实际¥{v10k:,} 偏差{abs(v10k-expected)/expected*100:.1f}%')
    
    # 2. Calmar ≈ ann / |mdd| (tightened from 15% to 5%)
    if mdd > 0 and ann != 0:
        expected_cal = round(ann / mdd, 2)
        if cal > 0 and abs(cal - expected_cal) / max(expected_cal, 0.01) > 0.05:
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
    
    # 4. Sharpe reasonability: daily Sharpe * sqrt(252) should be in sane range
    if sharpe > 0:
        daily_sharpe = sharpe / math.sqrt(252)
        if daily_sharpe > 0.5:  # annual Sharpe > 7.9 — suspicious
            warnings.append(f'{n}: Sharpe={sharpe}偏高(日频Sharpe={daily_sharpe:.3f}), 检查是否有未来函数')
    
    # 5. Annual return vs total return consistency (use strategy date range)
    start = s.get('start_date', '')
    end = s.get('end_date', '')
    if start and end and tr != 0 and ann != 0:
        try:
            from datetime import datetime
            d1 = datetime.strptime(start, '%Y-%m-%d')
            d2 = datetime.strptime(end, '%Y-%m-%d')
            n_years = (d2 - d1).days / 365.25
        except:
            n_years = 0
        if n_years > 1.0:
            expected_ann = ((1 + tr/100) ** (1/n_years) - 1) * 100
            if abs(ann - expected_ann) / max(abs(expected_ann), 1) > 0.10:
                warnings.append(f'{n}: ann={ann:.1f}% vs 从total_return推算{expected_ann:.1f}% (period={n_years:.1f}yr)')
    
    # 6. Annual vol sanity: should be positive if ann>0
    if vol <= 0 and ann > 0:
        warnings.append(f'{n}: annual_vol={vol}但annual_return={ann}%')
    
    # 7. High-return strategies should have confidence label
    if ann > 30 and not conf:
        warnings.append(f'{n}: 年化{ann}% 无confidence标签')
    
    # 8. Check for duplicate strategy names
    names_seen = {}
    for s2 in db['strategies']:
        n2 = s2['name']
        names_seen[n2] = names_seen.get(n2, 0) + 1
    for name, count in names_seen.items():
        if count > 1 and name == n:
            errors.append(f'{n}: 重复策略名(出现{count}次)')
            break

print(f'校验 {db["total"]} 个策略')
print(f'错误: {len(errors)} | 警告: {len(warnings)}')
print()
if errors:
    print('❌ 错误:')
    for e in errors[:15]:
        print(f'  {e}')
if warnings:
    print('⚠️ 警告:')
    for w in warnings[:15]:
        print(f'  {w}')
if not errors and not warnings:
    print('✅ 全部通过')

exit(1 if errors else 0)
