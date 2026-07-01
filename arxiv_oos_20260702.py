#!/usr/bin/python3
"""
OOS validation for paper-derived factors. 
IS: 2020-01 to 2023-12, OOS: 2024-01 to 2026-06
"""
import json, sys
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

# Load data from previous run
with open('/opt/quant/arxiv_factors_20260702.json') as f:
    prev = json.load(f)

print("=" * 80)
print("OOS VALIDATION: Paper-Derived Factors")
print("IS: 2020-2023 | OOS: 2024-2026")
print("=" * 80)

# Re-read IC records from the JSON
ic_summary = pd.DataFrame(prev['ic_summary'])
ic_summary = ic_summary[ic_summary['factor'] != 'f_momentum_1m']  # exclude buggy momentum
ic_summary = ic_summary.sort_values('IC_IR', ascending=False)

print("\nFull Period IC (for reference):")
print(f"{'Factor':<25s} {'IC_mean':>8s} {'IC_std':>8s} {'IC_IR':>8s} {'t-stat':>8s} {'N':>5s}")
print("-" * 70)
for _, row in ic_summary.iterrows():
    print(f"{row['factor']:<25s} {row['IC_mean']:>8.4f} {row['IC_std']:>8.4f} {row['IC_IR']:>8.4f} {row['IC_tstat']:>8.2f} {row['N']:>5.0f}")

# Since we don't have per-period IC in the JSON, let's re-run OOS from data
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

print("\n[Rerunning with OOS split...]")

# Quick fetch (use cached if possible, else re-fetch sample)
codes_sample = ['000001','000002','000858','002415','600000','600036','600519','601318',
                '600276','600887','000651','000333','002714','601012','600585','601888',
                '002475','600900','601166','000568','002230','603259','300750','300059',
                '688981','600030','601688','000063','002049','300015']

all_data = {}
for code in codes_sample:
    prefix = 'sz' if code.startswith(('00','30','688')) else 'sh'
    sym = f'{prefix}{code}'
    try:
        df = ak.stock_zh_a_daily(symbol=sym, start_date='20200101', end_date='20260630', adjust='qfq')
        if len(df) > 200:
            df = df.set_index('date').sort_index()
            all_data[code] = df
    except:
        pass

print(f"  Stock sample: {len(all_data)}")

# Compute factors and IS/OOS IC
factor_names = ['f1_magnitude_shrink', 'f2_lag3_reversal', 'f3_bounce_proxy',
                'f4_residual_ma20', 'f5_regime',
                'f_reversal_5d', 'f_lowvol']

factor_data = {}
for code, df in all_data.items():
    close = df['close']
    ret_1d = close.pct_change()
    ret_3d = close.pct_change(3)
    ret_5d = close.pct_change(5)
    ret_20d = close.pct_change(20)
    
    abs_ret_lag = ret_1d.abs().shift(1)
    ma20 = close.rolling(20).mean()
    vol_20 = ret_1d.rolling(20).std()
    vol_60 = ret_1d.rolling(60).std()
    
    factor_data[code] = pd.DataFrame({
        'f1_magnitude_shrink': -abs_ret_lag,
        'f2_lag3_reversal': -ret_3d.shift(1),
        'f3_bounce_proxy': -abs_ret_lag * (abs_ret_lag > 0.02).astype(float),
        'f4_residual_ma20': -(close - ma20) / close,
        'f5_regime': -((vol_20 / vol_60.shift(20) - 1).fillna(0).clip(-1, 1)),
        'f_reversal_5d': -ret_5d,
        'f_lowvol': -vol_20,
        'ret_20d': ret_20d,
    }, index=df.index)

# Get month ends
all_dates = sorted(set().union(*[set(fd.index) for fd in factor_data.values()]))
month_ends = []
for d in all_dates:
    try:
        if d.month != (d + pd.Timedelta(days=3)).month:
            month_ends.append(d)
    except:
        pass
month_ends = sorted(month_ends)

# Compute IC by period
def calc_ic(me_list, label):
    records = []
    for me in me_list[6:]:
        cs_values = {}
        for code, fd in factor_data.items():
            if me in fd.index:
                row = fd.loc[me]
                if not row[factor_names].isnull().any():
                    cs_values[code] = row
        if len(cs_values) < 5:
            continue
        df_cs = pd.DataFrame(cs_values).T
        fwd_ret = df_cs['ret_20d']
        for fn in factor_names:
            valid = df_cs[fn].notna() & fwd_ret.notna()
            if valid.sum() >= 5:
                ic, _ = spearmanr(df_cs.loc[valid, fn], fwd_ret[valid])
                records.append({'factor': fn, 'IC': ic})
    
    df_ic = pd.DataFrame(records)
    summary = df_ic.groupby('factor').agg(IC_mean=('IC','mean'), IC_std=('IC','std'), N=('IC','count')).reset_index()
    summary['IC_IR'] = summary['IC_mean'] / summary['IC_std']
    summary['period'] = label
    return summary

is_mes = [me for me in month_ends if me.year <= 2023]
oos_mes = [me for me in month_ends if me.year >= 2024]

ic_is = calc_ic(is_mes, 'IS (2020-2023)')
ic_oos = calc_ic(oos_mes, 'OOS (2024-2026)')

# Merge IS and OOS
merged = ic_is.merge(ic_oos, on='factor', suffixes=('_is', '_oos'))

print(f"\n{'Factor':<25s} {'IC_IS':>8s} {'IR_IS':>8s} {'IC_OOS':>8s} {'IR_OOS':>8s} {'IC_decay':>8s} {'OOS_verdict':>12s}")
print("-" * 90)
for _, row in merged.iterrows():
    decay = row['IC_mean_oos'] - row['IC_mean_is']
    ic_ratio = abs(row['IC_mean_oos'] / row['IC_mean_is']) if abs(row['IC_mean_is']) > 0.001 else 0
    # Verdict
    if ic_ratio > 0.7 and row['IC_mean_oos'] * row['IC_mean_is'] > 0:
        verdict = "✅ STABLE"
    elif ic_ratio > 0.3:
        verdict = "⚠️ WEAK"
    else:
        verdict = "❌ DECAYED"
    
    print(f"{row['factor']:<25s} {row['IC_mean_is']:>8.4f} {row['IC_IR_is']:>8.4f} {row['IC_mean_oos']:>8.4f} {row['IC_IR_oos']:>8.4f} {decay:>8.4f} {verdict:>12s}")

# Save OOS results
oos_results = {
    'date': '2026-07-02',
    'validation': 'OOS split: IS=2020-2023, OOS=2024-2026',
    'results': merged.to_dict('records'),
    'stock_sample': len(all_data),
    'n_is_months': len([me for me in is_mes]),
    'n_oos_months': len([me for me in oos_mes]),
}
with open('/opt/quant/arxiv_oos_20260702.json', 'w') as f:
    json.dump(oos_results, f, indent=2, default=str)

print(f"\nOOS results saved to /opt/quant/arxiv_oos_20260702.json")
