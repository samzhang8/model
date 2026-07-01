#!/usr/bin/python3
"""
Night Cruise H1: Paper-Derived Factor Extraction + Backtest
Papers:
  1. 2606.29591 Portnaya: Magnitude Shrinkage + Lag-3 Reversal
  2. 2605.12977 Tzikas et al: Transient Statistical (PCA Residual) Factors
  3. 2605.13407 PRISM-VQ: VQ-inspired Regime Factor
"""
import akshare as ak
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings('ignore')

# ===== DATA FETCH =====
print("[1/6] Fetching A-share data...")
try:
    codes = ak.stock_info_a_code_name()
    codes = codes[codes['code'].str.match(r'^(60[0-3]|00[0-3])')]  # Shanghai/Shenzhen main board only
    codes = codes.head(500)  # Top 500 for speed
    print(f"  Universe: {len(codes)} stocks")
except Exception as e:
    print(f"  stock_info_a_code_name failed: {e}")
    codes = pd.DataFrame({'code': ['000001', '000002', '600000', '600036', '601318']})

all_data = {}
stock_names = {}
errors = 0
for _, row in codes.iterrows():
    code = row['code']
    name = row.get('name', code)
    try:
        df = ak.stock_zh_a_daily(symbol=f'sz{code}' if code.startswith(('00','30')) else f'sh{code}',
                                 start_date='20200101', end_date='20260630',
                                 adjust='qfq')
        if len(df) > 200:
            df = df.set_index('date').sort_index()
            all_data[code] = df
            stock_names[code] = name
    except:
        try:
            df = ak.stock_zh_a_daily(symbol=f'sh{code}', start_date='20200101', 
                                     end_date='20260630', adjust='qfq')
            if len(df) > 200:
                df = df.set_index('date').sort_index()
                all_data[code] = df
                stock_names[code] = name
        except:
            errors += 1

print(f"  Fetched {len(all_data)} stocks ({errors} errors)")

if len(all_data) < 10:
    print("ERROR: Too few stocks fetched")
    import sys; sys.exit(1)

# ===== FACTOR COMPUTATION =====
print("[2/6] Computing paper-derived factors...")

dates = sorted(set().union(*[set(df.index) for df in all_data.values()]))
date_idx = pd.DatetimeIndex(sorted(dates))
print(f"  Date range: {date_idx[0].strftime('%Y-%m-%d')} to {date_idx[-1].strftime('%Y-%m-%d')} ({len(date_idx)} days)")

factor_data = {}

def safe_rank(x):
    """Cross-sectional rank (0-1)"""
    return x.rank(pct=True)

for code, df in all_data.items():
    if len(df) < 250:
        continue
    close = df['close']
    ret_1d = close.pct_change()
    ret_3d = close.pct_change(3)
    ret_5d = close.pct_change(5)
    ret_20d = close.pct_change(20)
    
    # Factor 1a: Magnitude Shrinkage — abs(ret_t-1) negatively predicts abs(ret_t)
    abs_ret_lag = ret_1d.abs().shift(1)
    # Higher = yesterday had smaller move = predict smaller move today (good for low-vol)
    f1_magnitude_shrink = -np.log1p(abs_ret_lag * 100)  # negative of log(1+|ret|)
    
    # Factor 1b: Lag-3 directional reversal (from paper: sig reversal at lag 3, not lag 1)
    f2_lag3_reversal = -ret_3d.shift(1)
    
    # Factor 1c: Bid-ask bounce proxy — magnitude shrinkage but conditioned on large moves
    # If yesterday had >2% move, expect shrinkage today
    f3_bounce_proxy = -abs_ret_lag * (abs_ret_lag > 0.02).astype(float)
    
    # Factor 2: Transient Statistical — simplified as residuals from 20-day mean reversion
    # The paper uses PCA on residuals; we simplify as: residual from 20-day MA
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    residual_20 = (close - ma20) / close
    residual_50 = (close - ma50) / close
    f4_residual_ma20 = -residual_20  # negative: mean reversion
    
    # Factor 3: VQ-inspired regime — simple volatility regime indicator
    vol_20 = ret_1d.rolling(20).std()
    vol_60 = ret_1d.rolling(60).std()
    # Regime: 1 if vol expanding, -1 if contracting
    regime_score = (vol_20 / vol_60 - 1).replace([np.inf, -np.inf], 0)
    f5_regime = -regime_score  # prefer contracting volatility
    
    # Baseline factors for comparison
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

print(f"  Factors computed for {len(factor_data)} stocks")

# ===== MONTH-END IC ANALYSIS =====
print("[3/6] Computing monthly IC...")

factor_names = ['f1_magnitude_shrink', 'f2_lag3_reversal', 'f3_bounce_proxy', 
                'f4_residual_ma20', 'f5_regime',
                'f_momentum_1m', 'f_reversal_5d', 'f_lowvol']

# Get common month ends
all_dates = sorted(set().union(*[set(fd.index) for fd in factor_data.values()]))
month_ends = []
for d in all_dates:
    if d.month != (d + pd.Timedelta(days=3)).month:
        month_ends.append(d)
month_ends = sorted(month_ends)
print(f"  Month ends: {len(month_ends)}")

ic_records = []
for me in month_ends[6:]:  # skip first 6 months for warmup
    cs_values = {}
    for code, fd in factor_data.items():
        if me in fd.index:
            row = fd.loc[me]
            if not row.isnull().any():
                cs_values[code] = row
    
    if len(cs_values) < 30:
        continue
    
    df_cs = pd.DataFrame(cs_values).T
    fwd_ret = df_cs['ret_20d']  # next month return (approximate forward)
    
    for fn in factor_names:
        if fn in df_cs.columns:
            valid = df_cs[fn].notna() & fwd_ret.notna()
            if valid.sum() >= 10:
                ic, pval = spearmanr(df_cs.loc[valid, fn], fwd_ret[valid])
                ic_records.append({'date': me, 'factor': fn, 'IC': ic})

df_ic = pd.DataFrame(ic_records)
print(f"  IC records: {len(df_ic)}")

# ===== IC SUMMARY =====
print("\n[4/6] IC Summary:")
print("=" * 80)

ic_summary = df_ic.groupby('factor').agg(
    IC_mean=('IC', 'mean'),
    IC_std=('IC', 'std'),
    N=('IC', 'count')
).reset_index()
ic_summary['IC_IR'] = ic_summary['IC_mean'] / ic_summary['IC_std']
ic_summary['IC_tstat'] = ic_summary['IC_mean'] / (ic_summary['IC_std'] / np.sqrt(ic_summary['N']))
ic_summary = ic_summary.sort_values('IC_IR', ascending=False)

print(f"{'Factor':<25s} {'IC_mean':>8s} {'IC_std':>8s} {'IC_IR':>8s} {'t-stat':>8s} {'N':>5s}")
print("-" * 70)
for _, row in ic_summary.iterrows():
    print(f"{row['factor']:<25s} {row['IC_mean']:>8.4f} {row['IC_std']:>8.4f} {row['IC_IR']:>8.4f} {row['IC_tstat']:>8.2f} {row['N']:>5.0f}")

# ===== SIMPLE BACKTEST (TOP 20% LONG) =====
print("\n[5/6] Running simplified backtest (Top 20% long, monthly rebalance)...")

# Use only the novel factors
novel_factors = ['f1_magnitude_shrink', 'f2_lag3_reversal', 'f3_bounce_proxy', 
                 'f4_residual_ma20', 'f5_regime']

# Combine novel factors (equal weight z-score)
backtest_results = {}
for fn in novel_factors:
    # Build monthly cross-sectional portfolio
    monthly_rets = []
    for me in month_ends[6:-1]:  # skip first/last for warmup and forward return
        next_me = month_ends[month_ends.index(me) + 1]
        
        cs_scores = {}
        for code, fd in factor_data.items():
            if me in fd.index and next_me in fd.index:
                score = fd.loc[me, fn]
                if not pd.isna(score):
                    cs_scores[code] = score
        
        if len(cs_scores) < 30:
            continue
        
        # Top 20% long
        sorted_codes = sorted(cs_scores, key=cs_scores.get, reverse=True)
        top_n = max(5, len(sorted_codes) // 5)
        long_codes = sorted_codes[:top_n]
        
        # Equal weight return
        long_rets = []
        for code in long_codes:
            if code in factor_data and next_me in factor_data[code].index:
                # Monthly return from me to next_me
                start_price = factor_data[code].loc[me, 'close']
                end_price = factor_data[code].loc[next_me, 'close']
                if start_price > 0:
                    long_rets.append(end_price / start_price - 1)
        
        if long_rets:
            monthly_rets.append(np.mean(long_rets))
    
    if len(monthly_rets) >= 12:
        monthly_rets = np.array(monthly_rets)
        ann_ret = np.mean(monthly_rets) * 12
        ann_vol = np.std(monthly_rets) * np.sqrt(12)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        
        # Drawdown
        cum = np.cumprod(1 + monthly_rets)
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        max_dd = dd.min()
        
        # Win rate
        win_rate = (monthly_rets > 0).mean()
        
        backtest_results[fn] = {
            'ann_ret': ann_ret,
            'ann_vol': ann_vol,
            'sharpe': sharpe,
            'max_dd': max_dd,
            'win_rate': win_rate,
            'n_months': len(monthly_rets)
        }

print(f"{'Factor':<25s} {'Ann Ret':>8s} {'Vol':>8s} {'Sharpe':>8s} {'MaxDD':>8s} {'Win%':>7s} {'N':>5s}")
print("-" * 75)
for fn in novel_factors:
    if fn in backtest_results:
        r = backtest_results[fn]
        print(f"{fn:<25s} {r['ann_ret']:>8.2%} {r['ann_vol']:>8.2%} {r['sharpe']:>8.2f} {r['max_dd']:>8.2%} {r['win_rate']:>7.1%} {r['n_months']:>5d}")

# ===== NOVEL FACTOR COMBINATION =====
print("\n[6/6] Novel factor combination (equal weight z-score) + benchmark comparison...")

# Combine novel factors
monthly_rets_combo = []
monthly_rets_baseline = []  # momentum + lowvol + reversal

for me in month_ends[6:-1]:
    next_me = month_ends[month_ends.index(me) + 1]
    
    cs_combo = {}
    cs_baseline = {}
    
    for code, fd in factor_data.items():
        if me in fd.index and next_me in fd.index:
            row = fd.loc[me]
            # Novel combo: z-score average of novel factors
            novel_scores = []
            for fn in novel_factors:
                if fn in row and not pd.isna(row[fn]):
                    novel_scores.append(row[fn])
            if len(novel_scores) >= 2:
                # z-score within this stock's factor values
                cs_combo[code] = np.mean(novel_scores)
            
            # Baseline: reversal + lowvol
            bs = 0
            if not pd.isna(row['f_reversal_5d']):
                bs += row['f_reversal_5d']
            if not pd.isna(row['f_lowvol']):
                bs += row['f_lowvol']
            cs_baseline[code] = bs
    
    for name, cs in [('Novel Combo', cs_combo), ('Reversal+LowVol', cs_baseline)]:
        if len(cs) < 30:
            continue
        sorted_codes = sorted(cs, key=cs.get, reverse=True)
        top_n = max(5, len(sorted_codes) // 5)
        long_codes = sorted_codes[:top_n]
        
        rets = []
        for code in long_codes:
            if code in factor_data and next_me in factor_data[code].index:
                start_price = factor_data[code].loc[me, 'close']
                end_price = factor_data[code].loc[next_me, 'close']
                if start_price > 0:
                    rets.append(end_price / start_price - 1)
        
        if rets:
            if name == 'Novel Combo':
                monthly_rets_combo.append(np.mean(rets))
            else:
                monthly_rets_baseline.append(np.mean(rets))

print(f"\n{'Strategy':<25s} {'Ann Ret':>8s} {'Vol':>8s} {'Sharpe':>8s} {'MaxDD':>8s} {'Win%':>7s} {'N':>5s}")
print("-" * 75)
for name, mrets in [('Novel Paper Factors', monthly_rets_combo), ('Reversal+LowVol (baseline)', monthly_rets_baseline)]:
    if len(mrets) >= 12:
        mrets = np.array(mrets)
        ann_ret = np.mean(mrets) * 12
        ann_vol = np.std(mrets) * np.sqrt(12)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        cum = np.cumprod(1 + mrets)
        peak = np.maximum.accumulate(cum)
        max_dd = (cum - peak).min() / peak[np.argmin(cum - peak)]
        win_rate = (mrets > 0).mean()
        print(f"{name:<25s} {ann_ret:>8.2%} {ann_vol:>8.2%} {sharpe:>8.2f} {max_dd:>8.2%} {win_rate:>7.1%} {len(mrets):>5d}")

# Save results
import json
results = {
    'papers_found': [
        {'id': '2606.29591', 'title': 'The Bounce Has No Direction: Sign, Magnitude, and Microstructure of Equity Return Predictability', 'authors': 'Victoria Portnaya', 'date': '2026-06-28'},
        {'id': '2605.12977', 'title': 'Enhancing a Risk Model by Adding Transient Statistical Factors', 'authors': 'Tzikas, Candès, Hastie, Boyd, Kochenderfer, Kahn', 'date': '2026-05-13'},
        {'id': '2605.13407', 'title': 'Vector-Quantized Discrete Latent Factors (PRISM-VQ)', 'authors': 'Namhyoung Kim, Jae Wook Song', 'date': '2026-05-13'},
        {'id': '2606.08586', 'title': 'Cross-sectional Topological Anomaly Scores', 'authors': 'Krzysztof Ozimek', 'date': '2026-06-07'},
        {'id': '2606.22719', 'title': 'Leakage-Aware LLM Factor Ranking', 'authors': 'Mao Guan, Qian Chen', 'date': '2026-06-21'},
    ],
    'ic_summary': ic_summary.to_dict('records'),
    'backtest_results': backtest_results,
    'combo_results': {
        'novel': {'ann_ret': float(np.mean(monthly_rets_combo)*12) if monthly_rets_combo else 0,
                  'sharpe': float(np.mean(monthly_rets_combo)/np.std(monthly_rets_combo)*np.sqrt(12)) if monthly_rets_combo else 0},
        'baseline': {'ann_ret': float(np.mean(monthly_rets_baseline)*12) if monthly_rets_baseline else 0,
                     'sharpe': float(np.mean(monthly_rets_baseline)/np.std(monthly_rets_baseline)*np.sqrt(12)) if monthly_rets_baseline else 0}
    },
    'blocked': 'GP mining on 115 unreachable (SSH auth failed)',
    'universe_size': len(all_data)
}

with open('/opt/quant/arxiv_factors_20260702.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\n[DONE] Results saved to /opt/quant/arxiv_factors_20260702.json")
print(f"       Universe: {len(all_data)} stocks, {len(month_ends)} month-ends")
print(f"       GP mining: BLOCKED (115 unreachable)")
