#!/usr/bin/python3
"""
Night Cruise H1: Paper-Derived Factor Extraction + Backtest (v2 - resilient fetching)
"""
import akshare as ak
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
import time, json, sys
import warnings
warnings.filterwarnings('ignore')

print("[1/6] Fetching A-share stock list...")
try:
    codes_df = ak.stock_info_a_code_name()
    print(f"  Got {len(codes_df)} stocks via stock_info_a_code_name")
except:
    try:
        codes_df = ak.stock_zh_a_spot_em()
        codes_df = codes_df[['代码','名称']].rename(columns={'代码':'code','名称':'name'})
        print(f"  Got {len(codes_df)} stocks via spot_em")
    except:
        print("  ERROR: Cannot get stock list. Using hardcoded top 20.")
        codes_df = pd.DataFrame({
            'code': ['000001','000002','000858','002415','600000','600036','600519','601318',
                     '600276','600887','000651','000333','002714','601012','600585','601888',
                     '002475','600900','601166','000568'],
            'name': ['平安银行','万科A','五粮液','海康威视','浦发银行','招商银行','贵州茅台','中国平安',
                     '恒瑞医药','伊利股份','格力电器','美的集团','牧原股份','隆基绿能','海螺水泥','中国中免',
                     '立讯精密','长江电力','兴业银行','泸州老窖']
        })

# Filter to main board liquid stocks
codes_df = codes_df[codes_df['code'].str.match(r'^(60[0-3]|00[0-3])')]
codes_df = codes_df.head(300)  # Limit for speed

stock_list = [(row['code'], row.get('name', '')) for _, row in codes_df.iterrows()]
print(f"  Fetching {len(stock_list)} stocks...")

# ===== FETCH WITH RETRY =====
all_data = {}
for i, (code, name) in enumerate(stock_list):
    prefix = 'sz' if code.startswith(('00','30')) else 'sh'
    sym = f'{prefix}{code}'
    
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=sym, start_date='20200101', end_date='20260630', adjust='qfq')
            if len(df) > 200:
                df = df.set_index('date').sort_index()
                all_data[code] = df
                break
        except Exception as e:
            time.sleep(0.3)
    
    if (i+1) % 50 == 0:
        print(f"  Fetched {len(all_data)}/{i+1}...", flush=True)

print(f"  Done: {len(all_data)} stocks fetched")

if len(all_data) < 20:
    print(f"ERROR: Only {len(all_data)} stocks. Exiting.")
    sys.exit(1)

# ===== FACTOR COMPUTATION =====
print("\n[2/6] Computing paper-derived factors...")
dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
print(f"  Date range: {dates[0]} to {dates[-1]} ({len(dates)} days)")

factor_data = {}
for code, df in all_data.items():
    if len(df) < 250:
        continue
    close = df['close']
    ret_1d = close.pct_change()
    ret_3d = close.pct_change(3)
    ret_5d = close.pct_change(5)
    ret_20d = close.pct_change(20)
    
    # Factor 1a: Magnitude Shrinkage
    abs_ret_lag = ret_1d.abs().shift(1)
    f1_magnitude_shrink = -abs_ret_lag
    
    # Factor 1b: Lag-3 directional reversal
    f2_lag3_reversal = -ret_3d.shift(1)
    
    # Factor 1c: Bounce proxy
    f3_bounce_proxy = -abs_ret_lag * (abs_ret_lag > 0.02).astype(float)
    
    # Factor 2: Transient Statistical - mean reversion residual
    ma20 = close.rolling(20).mean()
    residual_20 = (close - ma20) / close
    f4_residual_ma20 = -residual_20
    
    # Factor 3: VQ-inspired regime
    vol_20 = ret_1d.rolling(20).std()
    vol_60 = ret_1d.rolling(60).std()
    regime_score = (vol_20 / vol_60.shift(20) - 1).fillna(0)
    f5_regime = -regime_score.clip(-1, 1)
    
    # Baseline factors
    f_momentum_1m = ret_20d
    f_reversal_5d = -ret_5d
    f_lowvol = -vol_20
    
    factor_data[code] = pd.DataFrame({
        'f1_magnitude_shrink': f1_magnitude_shrink,
        'f2_lag3_reversal': f2_lag3_reversal,
        'f3_bounce_proxy': f3_bounce_proxy,
        'f4_residual_ma20': f4_residual_ma20,
        'f5_regime': f5_regime,
        'f_momentum_1m': f_momentum_1m,
        'f_reversal_5d': f_reversal_5d,
        'f_lowvol': f_lowvol,
        'ret_1d': ret_1d,
        'ret_5d': ret_5d,
        'ret_20d': ret_20d,
        'close': close
    }, index=df.index)

print(f"  Computed for {len(factor_data)} stocks")

# ===== MONTH-END IC =====
print("\n[3/6] Computing monthly IC...")

all_dates = sorted(set().union(*[set(fd.index) for fd in factor_data.values()]))
month_ends = []
for d in all_dates:
    try:
        nxt = d + pd.Timedelta(days=3)
        if d.month != nxt.month:
            month_ends.append(d)
    except:
        pass
month_ends = sorted(month_ends)
print(f"  Month ends: {len(month_ends)}")

factor_names = ['f1_magnitude_shrink', 'f2_lag3_reversal', 'f3_bounce_proxy',
                'f4_residual_ma20', 'f5_regime',
                'f_momentum_1m', 'f_reversal_5d', 'f_lowvol']

ic_records = []
for me in month_ends[6:]:
    cs_values = {}
    for code, fd in factor_data.items():
        if me in fd.index:
            row = fd.loc[me]
            if not row[factor_names].isnull().any():
                cs_values[code] = row
    
    if len(cs_values) < 20:
        continue
    
    df_cs = pd.DataFrame(cs_values).T
    fwd_ret = df_cs['ret_20d']
    
    for fn in factor_names:
        valid = df_cs[fn].notna() & fwd_ret.notna()
        if valid.sum() >= 10:
            ic, _ = spearmanr(df_cs.loc[valid, fn], fwd_ret[valid])
            ic_records.append({'date': me, 'factor': fn, 'IC': ic})

df_ic = pd.DataFrame(ic_records)

# ===== IC SUMMARY =====
print("\n[4/6] IC Summary:")
print("=" * 80)
ic_summary = df_ic.groupby('factor').agg(
    IC_mean=('IC', 'mean'), IC_std=('IC', 'std'), N=('IC', 'count')
).reset_index()
ic_summary['IC_IR'] = ic_summary['IC_mean'] / ic_summary['IC_std']
ic_summary['IC_tstat'] = ic_summary['IC_mean'] / (ic_summary['IC_std'] / np.sqrt(ic_summary['N']))
ic_summary = ic_summary.sort_values('IC_IR', ascending=False)

print(f"{'Factor':<25s} {'IC_mean':>8s} {'IC_std':>8s} {'IC_IR':>8s} {'t-stat':>8s} {'N':>5s}")
print("-" * 70)
for _, row in ic_summary.iterrows():
    print(f"{row['factor']:<25s} {row['IC_mean']:>8.4f} {row['IC_std']:>8.4f} {row['IC_IR']:>8.4f} {row['IC_tstat']:>8.2f} {row['N']:>5.0f}")

# ===== BACKTEST =====
print("\n[5/6] Running simplified backtest...")
novel_factors = ['f1_magnitude_shrink', 'f2_lag3_reversal', 'f3_bounce_proxy',
                 'f4_residual_ma20', 'f5_regime']

bt_results = {}
for fn in novel_factors:
    monthly_rets = []
    for i, me in enumerate(month_ends[6:-1]):
        next_me = month_ends[month_ends.index(me) + 1]
        cs_scores = {}
        for code, fd in factor_data.items():
            if me in fd.index and next_me in fd.index:
                score = fd.loc[me, fn]
                if not pd.isna(score):
                    cs_scores[code] = score
        
        if len(cs_scores) < 20:
            continue
        
        sorted_codes = sorted(cs_scores, key=cs_scores.get, reverse=True)
        top_n = max(5, len(sorted_codes) // 5)
        long_codes = sorted_codes[:top_n]
        
        rets = []
        for code in long_codes:
            if code in factor_data and next_me in factor_data[code].index:
                sp = factor_data[code].loc[me, 'close']
                ep = factor_data[code].loc[next_me, 'close']
                if sp > 0:
                    rets.append(ep / sp - 1)
        if rets:
            monthly_rets.append(np.mean(rets))
    
    if len(monthly_rets) >= 12:
        mr = np.array(monthly_rets)
        ann_ret = np.mean(mr) * 12
        ann_vol = np.std(mr) * np.sqrt(12)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        cum = np.cumprod(1 + mr)
        peak = np.maximum.accumulate(cum)
        max_dd = (cum - peak).min() / peak[np.argmin(cum - peak)]
        win_rate = (mr > 0).mean()
        bt_results[fn] = {
            'ann_ret': ann_ret, 'ann_vol': ann_vol, 'sharpe': sharpe,
            'max_dd': max_dd, 'win_rate': win_rate, 'n_months': len(mr)
        }

print(f"{'Factor':<25s} {'Ann Ret':>8s} {'Vol':>8s} {'Sharpe':>8s} {'MaxDD':>8s} {'Win%':>7s} {'N':>5s}")
print("-" * 75)
for fn in novel_factors:
    if fn in bt_results:
        r = bt_results[fn]
        print(f"{fn:<25s} {r['ann_ret']:>8.2%} {r['ann_vol']:>8.2%} {r['sharpe']:>8.2f} {r['max_dd']:>8.2%} {r['win_rate']:>7.1%} {r['n_months']:>5d}")

# ===== COMBO =====
print("\n[6/6] Factor combination...")
monthly_rets_combo = []
monthly_rets_baseline = []
for i, me in enumerate(month_ends[6:-1]):
    next_me = month_ends[month_ends.index(me) + 1]
    cs_combo, cs_baseline = {}, {}
    
    for code, fd in factor_data.items():
        if me in fd.index and next_me in fd.index:
            row = fd.loc[me]
            nv = [row[fn] for fn in novel_factors if fn in row and not pd.isna(row[fn])]
            if len(nv) >= 3:
                cs_combo[code] = np.mean(nv)
            bs = 0
            for bf in ['f_reversal_5d', 'f_lowvol']:
                if bf in row and not pd.isna(row[bf]):
                    bs += row[bf]
            cs_baseline[code] = bs
    
    for name, cs in [('Combo', cs_combo), ('Baseline', cs_baseline)]:
        if len(cs) < 20:
            continue
        sorted_codes = sorted(cs, key=cs.get, reverse=True)
        top_n = max(5, len(sorted_codes) // 5)
        rets = []
        for code in sorted_codes[:top_n]:
            if code in factor_data and next_me in factor_data[code].index:
                sp = factor_data[code].loc[me, 'close']
                ep = factor_data[code].loc[next_me, 'close']
                if sp > 0:
                    rets.append(ep/sp - 1)
        if rets:
            if name == 'Combo':
                monthly_rets_combo.append(np.mean(rets))
            else:
                monthly_rets_baseline.append(np.mean(rets))

print(f"\n{'Strategy':<25s} {'Ann Ret':>8s} {'Vol':>8s} {'Sharpe':>8s} {'MaxDD':>8s} {'Win%':>7s} {'N':>5s}")
print("-" * 75)
for name, mrets in [('Novel Paper Factors', monthly_rets_combo), ('Reversal+LowVol', monthly_rets_baseline)]:
    if len(mrets) >= 12:
        mr = np.array(mrets)
        ann_ret = np.mean(mr) * 12
        ann_vol = np.std(mr) * np.sqrt(12)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        cum = np.cumprod(1 + mr)
        peak = np.maximum.accumulate(cum)
        max_dd = (cum - peak).min() / peak[np.argmin(cum - peak)]
        win_rate = (mr > 0).mean()
        print(f"{name:<25s} {ann_ret:>8.2%} {ann_vol:>8.2%} {sharpe:>8.2f} {max_dd:>8.2%} {win_rate:>7.1%} {len(mr):>5d}")

# Save
results = {
    'date': '2026-07-02',
    'papers_found': [
        {'id': '2606.29591', 'title': 'Sign/Magnitude Decomposition of Return Autocorrelation', 'authors': 'Portnaya', 'factors': ['f1_magnitude_shrink', 'f2_lag3_reversal', 'f3_bounce_proxy']},
        {'id': '2605.12977', 'title': 'Enhancing Risk Model with Transient Statistical Factors', 'authors': 'Tzikas, Candès, Hastie, Boyd, Kahn', 'factors': ['f4_residual_ma20']},
        {'id': '2605.13407', 'title': 'PRISM-VQ: Vector-Quantized Discrete Latent Factors', 'authors': 'Kim, Song', 'factors': ['f5_regime']},
        {'id': '2606.08586', 'title': 'Cross-sectional Topological Anomaly Scores', 'authors': 'Ozimek', 'factors': []},
        {'id': '2606.22719', 'title': 'Leakage-Aware LLM Factor Ranking', 'authors': 'Guan, Chen', 'factors': []},
    ],
    'ic_summary': ic_summary.to_dict('records'),
    'backtest': {k: {kk: float(vv) if isinstance(vv, (np.floating, np.integer)) else vv for kk, vv in v.items()} for k, v in bt_results.items()},
    'combo': {'novel': None, 'baseline': None},
    'universe_size': len(all_data),
    'n_month_ends': len(month_ends),
    'blocked': ['GP mining (115 unreachable)']
}
if monthly_rets_combo:
    mr = np.array(monthly_rets_combo)
    results['combo']['novel'] = {'ann_ret': float(np.mean(mr)*12), 'sharpe': float(np.mean(mr)/np.std(mr)*np.sqrt(12)), 'max_dd': float(np.min(np.cumprod(1+mr) / np.maximum.accumulate(np.cumprod(1+mr)) - 1)), 'n': len(mr)}
if monthly_rets_baseline:
    mr = np.array(monthly_rets_baseline)
    results['combo']['baseline'] = {'ann_ret': float(np.mean(mr)*12), 'sharpe': float(np.mean(mr)/np.std(mr)*np.sqrt(12)), 'max_dd': float(np.min(np.cumprod(1+mr) / np.maximum.accumulate(np.cumprod(1+mr)) - 1)), 'n': len(mr)}

with open('/opt/quant/arxiv_factors_20260702.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\n[DONE] Results saved to /opt/quant/arxiv_factors_20260702.json")
print(f"       Universe: {len(all_data)} stocks, {len(month_ends)} month-ends")
print(f"       GP mining: BLOCKED (115 SSH auth failed)")
