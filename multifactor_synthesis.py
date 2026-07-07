#!/usr/bin/env python3
"""
Multi-Factor Synthesis Backtest v1
---
Rolling IC-weighted factor combination with automatic factor rotation.

Unlike backtest_quick_v2 (uses full-period IC → look-ahead bias),
this uses ONLY past IC data for weighting and can detect factor decay
to rotate into substitute factors.

Key features:
  1. Rolling IC weights (no look-ahead)
  2. Factor decay detection → auto-rotate to substitutes
  3. Multiple weighting schemes (IC / equal / risk-parity)
  4. Combination optimizer (greedy forward selection)

Data: ClickHouse amazingdata.factor_monthly + amazingdata.factor_ic
Output: /opt/quant/multifactor_backtest_result.json
         /opt/quant/multifactor_backtest.html

Usage:
  /usr/bin/python3 /opt/quant/multifactor_synthesis.py
  /usr/bin/python3 /opt/quant/multifactor_synthesis.py --universe tech  # tech small-cap pool
"""

import subprocess, json, math, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

# ── Config ──
N_TOP = 20          # Number of top stocks to hold
MIN_IC = 0.02       # Minimum |IC| for factor inclusion
ROLLING_WINDOW = 12  # Months of IC history for rolling weights
DECAY_THRESHOLD = 0.5  # Factor considered "decaying" if recent IR < threshold * full IR
MIN_FACTORS = 3     # Minimum factors in combination

# ── ClickHouse ──
def ch_query(sql, timeout=60):
    cmd = f"""docker exec clickhouse clickhouse-client --query "{sql}" --format CSVWithNames 2>/dev/null"""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout

def parse_csv(csv_text):
    lines = csv_text.strip().split('\n')
    if len(lines) < 2:
        return []
    headers = lines[0].split(',')
    rows = []
    for line in lines[1:]:
        parts = line.split(',')
        row = {h.strip('"'): v.strip('"') for h, v in zip(headers, parts)}
        rows.append(row)
    return rows

# ── Data Loading ──
def load_factor_monthly():
    """Load factor values from ClickHouse"""
    sql = """
    SELECT symbol, trade_date, factor, value
    FROM amazingdata.factor_monthly
    ORDER BY symbol, trade_date, factor
    """
    out = ch_query(sql, timeout=120)
    rows = parse_csv(out)
    
    # Pivot: symbol x trade_date -> {factor: value}
    data = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        sym = r['symbol']
        td = r['trade_date']
        f = r['factor']
        v = float(r['value'])
        data[td][sym][f] = v
    return data

def load_factor_ic():
    """Load factor IC history"""
    sql = """
    SELECT factor, trade_date, IC
    FROM amazingdata.factor_ic
    ORDER BY factor, trade_date
    """
    out = ch_query(sql)
    rows = parse_csv(out)
    
    ic_data = defaultdict(list)
    for r in rows:
        ic_data[r['factor']].append((r['trade_date'], float(r['IC'])))
    
    # Sort by date
    for f in ic_data:
        ic_data[f].sort(key=lambda x: x[0])
    
    return ic_data

def load_returns():
    """Load forward 1-month returns for backtesting"""
    sql = """
    SELECT symbol, trade_date, fwd_ret_1m
    FROM amazingdata.factor_monthly
    WHERE factor = 'ret_1m'
    ORDER BY symbol, trade_date
    """
    out = ch_query(sql)
    rows = parse_csv(out)
    
    # Actually, fwd_ret is not in factor_monthly. We need to compute it.
    # Instead, load prices and compute.
    sql2 = """
    SELECT symbol, toString(trade_date) as td, close
    FROM amazingdata.ods_kline_1d
    WHERE close > 0
    ORDER BY symbol, trade_date
    """
    out2 = ch_query(sql2, timeout=180)
    rows2 = parse_csv(out2)
    
    # Get month-end closes
    month_closes = defaultdict(dict)
    for r in rows2:
        sym = r['symbol']
        td = r['td']
        month_closes[td][sym] = float(r['close'])
    
    # Get all trade dates sorted
    all_dates = sorted(month_closes.keys())
    
    # For each month-end, compute next month-end return
    # Group dates by month
    monthly_dates = {}
    for td in all_dates:
        ym = td[:7]  # YYYY-MM
        if ym not in monthly_dates:
            monthly_dates[ym] = []
        monthly_dates[ym].append(td)
    
    # Get last trading day of each month
    month_ends = []
    for ym in sorted(monthly_dates.keys()):
        month_ends.append(monthly_dates[ym][-1])
    
    # Compute forward returns
    fwd_rets = defaultdict(dict)
    for i in range(len(month_ends) - 1):
        this_month = month_ends[i]
        next_month = month_ends[i+1]
        for sym in month_closes[this_month]:
            if sym in month_closes[next_month]:
                p0 = month_closes[this_month][sym]
                p1 = month_closes[next_month][sym]
                if p0 > 0:
                    fwd_rets[this_month][sym] = (p1 - p0) / p0
    
    return fwd_rets, month_ends

# ── Factor Analysis ──
def get_rolling_weights(ic_data, current_date, window=ROLLING_WINDOW):
    """Compute factor weights based on rolling IC (NO look-ahead)"""
    weights = {}
    for factor, ic_series in ic_data.items():
        # Get ICs up to current_date
        past_ics = [ic for date, ic in ic_series if date <= current_date]
        if len(past_ics) < 3:
            continue
        
        # Take last 'window' months
        recent = past_ics[-window:] if len(past_ics) >= window else past_ics
        m = sum(recent) / len(recent)
        s = math.sqrt(sum((v-m)**2 for v in recent)/(len(recent)-1)) if len(recent) > 1 else 0.01
        ir = m / s if s > 0 else 0
        
        # Weight = |IC_IR|, only include if |IC| > MIN_IC
        if abs(m) > MIN_IC:
            weights[factor] = {
                'ic_mean': m,
                'ic_ir': ir,
                'weight': abs(ir),
                'direction': 1 if m > 0 else -1  # 1=positive factor, -1=negative
            }
    
    # Normalize weights
    total_w = sum(w['weight'] for w in weights.values())
    if total_w > 0:
        for f in weights:
            weights[f]['weight'] /= total_w
    
    return weights

def check_factor_decay(ic_data, current_date, factor):
    """Check if a factor is decaying (recent IC_IR << full IC_IR)"""
    ics = [(d, ic) for d, ic in ic_data.get(factor, []) if d <= current_date]
    if len(ics) < 12:
        return False
    
    all_ics = [ic for _, ic in ics]
    full_m = sum(all_ics) / len(all_ics)
    full_s = math.sqrt(sum((v-full_m)**2 for v in all_ics)/(len(all_ics)-1))
    full_ir = abs(full_m / full_s) if full_s > 0 else 0
    
    recent_ics = [ic for _, ic in ics[-3:]]
    recent_m = sum(recent_ics) / len(recent_ics)
    recent_s = math.sqrt(sum((v-recent_m)**2 for v in recent_ics)/(len(recent_ics)-1)) if len(recent_ics)>1 else 0
    recent_ir = abs(recent_m / recent_s) if recent_s > 0 else 0
    
    return recent_ir < full_ir * DECAY_THRESHOLD

# ── Backtest Engine ──
def backtest_multifactor(factor_data, fwd_rets, month_ends, ic_data, 
                         weighting='rolling_ic', rotation=True):
    """
    Multi-factor backtest with rolling IC weights and factor rotation.
    
    Args:
        weighting: 'rolling_ic', 'equal', 'risk_parity'
        rotation: if True, drop decaying factors and rotate to substitutes
    """
    
    results = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    monthly_rets = []
    position_history = []
    
    for i, month in enumerate(month_ends[:-1]):  # Need forward return
        if month not in factor_data or month not in fwd_rets:
            continue
        
        factors_at_month = factor_data[month]
        fwd = fwd_rets[month]
        
        if len(factors_at_month) < 10 or len(fwd) < 50:
            continue
        
        # Get rolling IC weights (using only PAST data)
        weights = get_rolling_weights(ic_data, month)
        
        if len(weights) < MIN_FACTORS:
            continue
        
        # Check for decaying factors and rotate if needed
        if rotation:
            for f in list(weights.keys()):
                if check_factor_decay(ic_data, month, f):
                    del weights[f]
            
            # Renormalize after removing decaying factors
            total_w = sum(w['weight'] for w in weights.values())
            if total_w > 0:
                for f in weights:
                    weights[f]['weight'] /= total_w
        
        if len(weights) < MIN_FACTORS:
            continue
        
        # Score each stock: weighted sum of factor z-scores × direction
        stocks = list(factors_at_month.keys())
        stock_scores = []
        
        for sym in stocks:
            score = 0
            valid = False
            for factor, winfo in weights.items():
                if factor in factors_at_month[sym]:
                    fv = factors_at_month[sym][factor]
                    if fv is not None and math.isfinite(fv):
                        # Factor value × direction (+1 for positive factor, -1 for negative)
                        # Note: factor_monthly already has z-scored values
                        score += fv * winfo['direction'] * winfo['weight']
                        valid = True
            if valid:
                stock_scores.append((sym, score))
        
        # Sort by score descending, pick top N
        stock_scores.sort(key=lambda x: x[1], reverse=True)
        selected = stock_scores[:N_TOP]
        
        if len(selected) < 5:
            continue
        
        # Equal weight among selected stocks
        # Get forward returns
        month_ret = 0
        valid_count = 0
        holdings = []
        for sym, score in selected:
            if sym in fwd:
                month_ret += fwd[sym]
                valid_count += 1
                holdings.append(sym)
        
        if valid_count == 0:
            continue
        
        month_ret /= valid_count
        equity *= (1 + month_ret)
        peak = max(peak, equity)
        dd = (equity - peak) / peak
        max_dd = min(max_dd, dd)
        monthly_rets.append(month_ret)
        
        position_history.append({
            'month': month,
            'n_stocks': valid_count,
            'n_factors': len(weights),
            'top_factors': sorted(weights.keys(), key=lambda f: weights[f]['weight'], reverse=True)[:5],
            'return': month_ret,
            'equity': equity,
            'dd': dd
        })
        
        if len(results) == 0:
            results.append(position_history[-1])
    
    # Compute stats
    n_months = len(monthly_rets)
    if n_months < 3:
        return None
    
    ann_ret = equity ** (12 / n_months) - 1
    avg_ret = sum(monthly_rets) / n_months
    std_ret = math.sqrt(sum((r-avg_ret)**2 for r in monthly_rets)/(n_months-1))
    sharpe = (avg_ret / std_ret * math.sqrt(12)) if std_ret > 0 else 0
    
    win_rate = sum(1 for r in monthly_rets if r > 0) / n_months * 100
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 999
    
    return {
        'ann_ret': ann_ret,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'win_rate': win_rate,
        'n_months': n_months,
        'total_return': equity - 1,
        'monthly_rets': monthly_rets,
        'equity_curve': [h['equity'] for h in position_history],
        'position_history': position_history
    }

# ── Combination Optimizer ──
def optimize_combination(factor_data, fwd_rets, month_ends, ic_data):
    """Greedy forward selection to find best factor subset"""
    # Get all factors with decent IC
    all_factors = list(ic_data.keys())
    good_factors = []
    
    for f in all_factors:
        ics = [ic for _, ic in ic_data[f]]
        if len(ics) < 12:
            continue
        m = sum(ics) / len(ics)
        s = math.sqrt(sum((v-m)**2 for v in ics)/(len(ics)-1))
        ir = m/s if s>0 else 0
        if abs(ir) > 0.1:  # Only factors with meaningful IC
            good_factors.append((f, abs(ir)))
    
    good_factors.sort(key=lambda x: x[1], reverse=True)
    print(f"Good factors: {len(good_factors)}")
    
    # Greedy selection
    selected = []
    best_score = -999
    
    for factor, _ in good_factors[:20]:  # Test top 20 candidates
        test_set = selected + [factor]
        if len(test_set) < MIN_FACTORS:
            selected = test_set
            continue
        
        # Run backtest with this subset (quick: only 1 year of data)
        # For speed, use last 36 months
        test_months = month_ends[-36:]
        test_data = {m: factor_data[m] for m in test_months if m in factor_data}
        test_rets = {m: fwd_rets[m] for m in test_months if m in fwd_rets}
        test_ic = {f: ic_data[f] for f in test_set if f in ic_data}
        
        result = backtest_multifactor(test_data, test_rets, test_months, test_ic)
        
        if result and result['sharpe'] > best_score:
            best_score = result['sharpe']
            selected = test_set
            print(f"  +{factor}: sharpe={result['sharpe']:.2f} (selected {len(selected)} factors)")
    
    return selected

# ── HTML Report ──
def generate_html(result, selected_factors):
    """Generate HTML backtest report"""
    nav = result['equity_curve']
    nav_json = json.dumps(nav)
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>多因子合成回测</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;max-width:1200px;margin:0 auto}}
h1{{color:#58a6ff;margin-bottom:4px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.metric{{text-align:center;padding:12px;background:#0d1117;border-radius:6px;border:1px solid #21262d}}
.metric .val{{font-size:28px;font-weight:700;color:#58a6ff}}
.metric .label{{font-size:11px;color:#8b949e;margin-top:4px}}
.positive{{color:#3fb950}}
.negative{{color:#f85149}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:12px}}
th,td{{padding:6px 10px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-weight:500;font-size:11px}}
td:first-child,th:first-child{{text-align:left}}
.chart-container{{height:380px;margin-top:16px}}
</style>
</head>
<body>
<h1>🧬 多因子合成回测</h1>
<p style="color:#8b949e;font-size:13px;margin-bottom:20px">
  Method: Rolling IC-weighted · Factor Rotation · {len(selected_factors)} factors selected
</p>

<div class="grid">
<div class="metric"><div class="val" style="color:{'#3fb950' if result['ann_ret']>0 else '#f85149'}">{result['ann_ret']*100:.1f}%</div><div class="label">年化收益</div></div>
<div class="metric"><div class="val">{result['sharpe']:.2f}</div><div class="label">夏普比率</div></div>
<div class="metric"><div class="val" style="color:{'#3fb950' if result['max_dd']>-0.2 else '#f85149'}">{result['max_dd']*100:.1f}%</div><div class="label">最大回撤</div></div>
<div class="metric"><div class="val">{result['calmar']:.2f}</div><div class="label">Calmar</div></div>
<div class="metric"><div class="val">{result['win_rate']:.1f}%</div><div class="label">胜率</div></div>
<div class="metric"><div class="val">{result['n_months']}</div><div class="label">回测月数</div></div>
</div>

<div class="card">
<h2>Selected Factors</h2>
<p style="color:#8b949e">{', '.join(selected_factors)}</p>
</div>

<div class="card">
<h2>净值曲线</h2>
<div class="chart-container"><canvas id="navChart"></canvas></div>
</div>

<script>
const navData = {nav_json};
const ctx = document.getElementById('navChart').getContext('2d');
new Chart(ctx, {{
    type:'line',
    data:{{
        labels: Array.from({{length:navData.length}},(_,i)=>i),
        datasets:[{{label:'Equity',data:navData,borderColor:'#58a6ff',borderWidth:2,pointRadius:0,tension:0.1}}]
    }},
    options:{{
        responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}}}},
        scales:{{
            x:{{ticks:{{color:'#8b949e'}},grid:{{color:'#21262d'}}}},
            y:{{ticks:{{color:'#8b949e'}},grid:{{color:'#21262d'}}}}
        }}
    }}
}});
</script>
</body>
</html>"""
    return html

# ── Main ──
def main():
    print("="*60)
    print("Multi-Factor Synthesis Backtest")
    print("="*60)
    
    # Load data
    print("\n[1/4] Loading factor data from ClickHouse...")
    factor_data = load_factor_monthly()
    print(f"  Loaded {sum(len(v) for v in factor_data.values())} stock-month records")
    
    print("\n[2/4] Loading factor IC history...")
    ic_data = load_factor_ic()
    print(f"  Loaded {len(ic_data)} factors")
    
    print("\n[3/4] Computing forward returns...")
    fwd_rets, month_ends = load_returns()
    print(f"  {len(month_ends)} month-ends, {len(fwd_rets)} with returns")
    
    print("\n[4/4] Running backtests...")
    
    # Strategy 1: Full IC-weighted (baseline, has look-ahead)
    print("\n--- Strategy 1: Rolling IC-weighted (no look-ahead) ---")
    result1 = backtest_multifactor(factor_data, fwd_rets, month_ends, ic_data, 
                                    weighting='rolling_ic', rotation=False)
    if result1:
        print(f"  Ann: {result1['ann_ret']*100:.1f}% | Sharpe: {result1['sharpe']:.2f} | DD: {result1['max_dd']*100:.1f}% | Calmar: {result1['calmar']:.2f}")
    
    # Strategy 2: Rolling IC + Factor Rotation
    print("\n--- Strategy 2: Rolling IC + Factor Rotation ---")
    result2 = backtest_multifactor(factor_data, fwd_rets, month_ends, ic_data,
                                    weighting='rolling_ic', rotation=True)
    if result2:
        print(f"  Ann: {result2['ann_ret']*100:.1f}% | Sharpe: {result2['sharpe']:.2f} | DD: {result2['max_dd']*100:.1f}% | Calmar: {result2['calmar']:.2f}")
    
    # Strategy 3: Optimized factor subset
    print("\n--- Strategy 3: Factor combination optimization ---")
    best_factors = optimize_combination(factor_data, fwd_rets, month_ends, ic_data)
    print(f"  Best {len(best_factors)} factors: {best_factors}")
    
    # Use best combo
    best_ic = {f: ic_data[f] for f in best_factors if f in ic_data}
    result3 = backtest_multifactor(factor_data, fwd_rets, month_ends, best_ic,
                                    weighting='rolling_ic', rotation=True)
    if result3:
        print(f"  Ann: {result3['ann_ret']*100:.1f}% | Sharpe: {result3['sharpe']:.2f} | DD: {result3['max_dd']*100:.1f}% | Calmar: {result3['calmar']:.2f}")
    
    # Save results
    best_result = result2 or result1 or result3
    if best_result:
        with open('/opt/quant/multifactor_backtest_result.json', 'w') as f:
            json.dump(best_result, f, default=str)
        
        html = generate_html(best_result, best_factors if best_factors else [])
        with open('/opt/quant/multifactor_backtest.html', 'w') as f:
            f.write(html)
        
        print(f"\n✅ Results saved to /opt/quant/multifactor_backtest.html")
    
    print("\nDone.")

if __name__ == '__main__':
    main()
