#!/usr/bin/env python3
"""
Enhanced Factor Monitor v2 — Rolling IC Windows + Factor Momentum + Substitute Ranking
Generates a standalone HTML dashboard with:
  1. Factor ranking with rolling IC (3m/6m/12m/Full)
  2. Factor momentum (rising/falling indicators)
  3. Substitute recommendations when primary factors decay
  4. Decay alerts for factors losing effectiveness

Data source: ClickHouse amazingdata.factor_ic (all 33 factors, monthly)
Output: /opt/quant/factor_monitor_v2.html

Usage:
  # On 115 server (full data):
  /usr/bin/python3 /opt/quant/factor_monitor_v2.py
  
  # With embedded data (backup):
  /usr/bin/python3 /opt/quant/factor_monitor_v2.py --embedded
"""

import subprocess, json, math, sys, os
from datetime import datetime

# ── ClickHouse query ──
def ch_query(sql):
    """Run SQL against ClickHouse via docker exec"""
    cmd = f"""docker exec clickhouse clickhouse-client --query "{sql}" --format CSVWithNames 2>/dev/null"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    return result.stdout

# ── Data loading ──
def load_ic_data_clickhouse():
    """Load all factor IC data from ClickHouse"""
    sql = """
    SELECT factor, trade_date, IC 
    FROM amazingdata.factor_ic 
    ORDER BY factor, trade_date
    """
    out = ch_query(sql)
    if not out.strip():
        raise RuntimeError("No data from ClickHouse")
    
    lines = out.strip().split('\n')
    header = lines[0]
    data = {}
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) < 3:
            continue
        factor = parts[0].strip('"')
        date = parts[1].strip('"')
        ic = float(parts[2])
        if factor not in data:
            data[factor] = []
        data[factor].append((date, ic))
    return data

def load_ic_summary_clickhouse():
    """Load factor IC summary stats"""
    sql = """
    SELECT 
        factor,
        avg(IC) as ic_mean,
        avg(IC) / stddevSamp(IC) as ic_ir,
        stddevSamp(IC) as ic_std,
        countIf(IC > 0) * 100.0 / count() as win_rate,
        count() as n_months
    FROM amazingdata.factor_ic
    GROUP BY factor
    ORDER BY abs(ic_ir) DESC
    """
    out = ch_query(sql)
    if not out.strip():
        raise RuntimeError("No summary data from ClickHouse")
    
    lines = out.strip().split('\n')
    factors = {}
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) < 6:
            continue
        name = parts[0].strip('"')
        factors[name] = {
            'ic_mean': float(parts[1]),
            'ic_ir': float(parts[2]),
            'ic_std': float(parts[3]),
            'win_rate': float(parts[4]),
            'n_months': int(parts[5])
        }
    return factors

# ── Analysis ──
def calc_window(vals):
    """Calculate mean and IC_IR for a window"""
    n = len(vals)
    if n < 2:
        return sum(vals)/n if n>0 else 0, 0
    m = sum(vals)/n
    s = math.sqrt(sum((v-m)**2 for v in vals)/(n-1))
    ir = m/s if s>0 else 0
    return m, ir

def analyze_factors(ic_data, summary):
    """Compute rolling windows and momentum for all factors"""
    results = {}
    for factor, series in ic_data.items():
        # Sort by date
        series.sort(key=lambda x: x[0])
        dates = [s[0] for s in series]
        ics = [s[1] for s in series]
        n = len(ics)
        
        if n < 12:
            continue
        
        # Full period
        full_m, full_ir = calc_window(ics)
        
        # Rolling windows
        m12, ir12 = calc_window(ics[-12:]) if n>=12 else (full_m, full_ir)
        m6, ir6 = calc_window(ics[-6:]) if n>=6 else (m12, ir12)
        m3, ir3 = calc_window(ics[-3:]) if n>=3 else (m6, ir6)
        
        latest_ic = ics[-1]
        latest_date = dates[-1]
        
        # Momentum: change in absolute IC_IR
        mom_3m = abs(ir3) - abs(full_ir)
        
        # Direction (positive = original direction, negative = reversed)
        orig_dir = "positive" if full_m > 0 else "negative"
        recent_dir = "positive" if m3 > 0 else "negative"
        dir_flip = orig_dir != recent_dir
        
        # Decay warning
        is_decaying = abs(ir3) < abs(full_ir) * 0.3
        
        # Strength category
        if abs(full_ir) > 0.5:
            strength = "strong"
        elif abs(full_ir) > 0.2:
            strength = "moderate"
        else:
            strength = "weak"
        
        results[factor] = {
            'full_m': full_m, 'full_ir': full_ir,
            'm12': m12, 'ir12': ir12,
            'm6': m6, 'ir6': ir6,
            'm3': m3, 'ir3': ir3,
            'latest_ic': latest_ic, 'latest_date': latest_date,
            'momentum': mom_3m,
            'dir_flip': dir_flip,
            'is_decaying': is_decaying,
            'strength': strength,
            'orig_dir': orig_dir,
            'recent_dir': recent_dir,
            'n_months': n,
            'summary': summary.get(factor, {})
        }
    
    return results

# ── Substitute recommendations ──
def find_substitutes(results, target_dir='positive', exclude=None):
    """
    Find substitute factors when primary is decaying.
    Target: factors with same direction as target, strong recent IR, rising momentum.
    """
    candidates = []
    for name, r in results.items():
        if exclude and name in exclude:
            continue
        if r['orig_dir'] != target_dir:
            continue
        if r['is_decaying']:
            continue
        # Score: recent IR * momentum bonus
        score = abs(r['ir3']) * (1 + max(0, r['momentum']))
        candidates.append((name, score, r))
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:10]

# ── HTML Generation ──
def generate_html(results, substitute_map):
    """Generate enhanced dashboard HTML"""
    
    # Sort factors by |full_ir| descending
    ranked = sorted(results.items(), key=lambda x: abs(x[1]['full_ir']), reverse=True)
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # Build factor table rows
    factor_rows = ""
    for i, (name, r) in enumerate(ranked):
        full_m = r['full_m']
        full_ir = r['full_ir']
        ir3 = r['ir3']
        ir6 = r['ir6']
        ir12 = r['ir12']
        mom = r['momentum']
        latest = r['latest_ic']
        
        # Color coding
        def ir_color(ir):
            if abs(ir) > 0.5: return '#3fb950'
            if abs(ir) > 0.2: return '#d2991d'
            return '#f85149'
        
        def mom_badge(mom):
            if mom > 0.1: return '<span class="badge badge-green">🔥RISING</span>'
            if mom < -0.1: return '<span class="badge badge-red">📉FALLING</span>'
            return '<span class="badge badge-blue">→STABLE</span>'
        
        def decay_badge(r):
            if r['is_decaying']:
                return '<span class="badge badge-red">⚠️DECAY</span>'
            if r['dir_flip']:
                return '<span class="badge badge-yellow">↻FLIP</span>'
            return ''
        
        dir_label = "正向" if r['orig_dir'] == 'positive' else "反向"
        dir_cls = 'positive' if r['orig_dir'] == 'positive' else 'negative'
        str_label = r['strength'].upper()
        
        factor_rows += f"""<tr>
            <td style="font-weight:500">{name}</td>
            <td class="{dir_cls}">{full_m:+.4f}</td>
            <td style="color:{ir_color(full_ir)}">{full_ir:+.4f}</td>
            <td style="color:{ir_color(ir12)}">{ir12:+.4f}</td>
            <td style="color:{ir_color(ir6)}">{ir6:+.4f}</td>
            <td style="color:{ir_color(ir3)}">{ir3:+.4f}</td>
            <td class="{dir_cls}">{latest:+.4f}</td>
            <td>{mom_badge(mom)}</td>
            <td>{decay_badge(r)}</td>
            <td><span class="badge badge-blue">{str_label}</span></td>
            <td><span class="badge badge-{'green' if dir_label=='正向' else 'red'}">{dir_label}</span></td>
        </tr>"""
    
    # Substitute recommendations
    sub_html = ""
    for target, subs in substitute_map.items():
        sub_html += f'<div class="card"><h2>🔄 替补推荐: 当 <span class="badge badge-red">{target}</span> 衰退时</h2><table><thead><tr><th>排名</th><th>因子</th><th>3m IC_IR</th><th>全周期IC_IR</th><th>动量</th><th>类型</th></tr></thead><tbody>'
        for j, (name, score, r) in enumerate(subs[:5]):
            sub_html += f"""<tr>
                <td>{j+1}</td>
                <td style="font-weight:500">{name}</td>
                <td style="color:{ir_color(r['ir3'])}">{r['ir3']:+.4f}</td>
                <td style="color:{ir_color(r['full_ir'])}">{r['full_ir']:+.4f}</td>
                <td>{mom_badge(r['momentum'])}</td>
                <td><span class="badge badge-blue">{r['strength'].upper()}</span></td>
            </tr>"""
        sub_html += '</tbody></table></div>'
    
    # Decay alerts
    decaying = [(name, r) for name, r in results.items() if r['is_decaying']]
    decay_html = ""
    if decaying:
        decay_html = '<div class="card" style="border-color:#f85149"><h2>⚠️ 因子衰退预警</h2><table><thead><tr><th>因子</th><th>全周期IC_IR</th><th>3月IC_IR</th><th>最新IC</th><th>方向</th></tr></thead><tbody>'
        for name, r in decaying:
            decay_html += f"""<tr>
                <td style="font-weight:500">{name}</td>
                <td>{r['full_ir']:+.4f}</td>
                <td style="color:#f85149">{r['ir3']:+.4f}</td>
                <td style="color:#f85149">{r['latest_ic']:+.4f}</td>
                <td>{'正向' if r['orig_dir']=='positive' else '反向'}</td>
            </tr>"""
        decay_html += '</tbody></table></div>'
    
    # Rising stars
    rising = [(name, r) for name, r in results.items() if r['momentum'] > 0.1]
    rising.sort(key=lambda x: x[1]['momentum'], reverse=True)
    rising_html = ""
    if rising:
        rising_html = '<div class="card" style="border-color:#3fb950"><h2>🔥 动量上升因子 (3m IR > 全周期)</h2><table><thead><tr><th>因子</th><th>全周期IC_IR</th><th>3月IC_IR</th><th>动量提升</th><th>方向</th></tr></thead><tbody>'
        for name, r in rising[:10]:
            rising_html += f"""<tr>
                <td style="font-weight:500">{name}</td>
                <td>{r['full_ir']:+.4f}</td>
                <td style="color:#3fb950">{r['ir3']:+.4f}</td>
                <td style="color:#3fb950">+{r['momentum']:.3f}</td>
                <td>{'正向' if r['orig_dir']=='positive' else '反向'}</td>
            </tr>"""
        rising_html += '</tbody></table></div>'
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>因子监控面板 v2 · 滚动IC + 动量 + 替补</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;max-width:1600px;margin:0 auto}}
h1{{color:#58a6ff;margin-bottom:4px;font-size:22px}}
h2{{color:#58a6ff;margin:24px 0 12px;font-size:16px;border-bottom:1px solid #30363d;padding-bottom:6px}}
.sub{{color:#8b949e;font-size:13px;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th,td{{padding:5px 8px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-weight:500;font-size:10px;text-transform:uppercase;position:sticky;top:0;background:#161b22;z-index:1;white-space:nowrap}}
td:first-child,th:first-child{{text-align:left;font-family:monospace;font-size:11px}}
tr:hover{{background:#1c2333}}
.positive{{color:#3fb950}}
.negative{{color:#f85149}}
.neutral{{color:#8b949e}}
.badge{{display:inline-block;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:600;white-space:nowrap}}
.badge-green{{background:#122d1e;color:#3fb950}}
.badge-red{{background:#2d1215;color:#f85149}}
.badge-yellow{{background:#2d2a12;color:#d2991d}}
.badge-blue{{background:#121e2d;color:#58a6ff}}
.highlight{{background:#1a2733;border-left:3px solid #58a6ff;padding:10px 14px;border-radius:4px;margin:10px 0;font-size:12px;line-height:1.5}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.summary-num{{font-size:24px;font-weight:700}}
.summary-label{{font-size:10px;color:#8b949e}}
</style>
</head>
<body>

<h1>🧬 因子监控面板 v2</h1>
<p class="sub">
  Data: ClickHouse amazingdata.factor_ic · Rolling IC Windows (3/6/12 month) · Factor Momentum · Substitute Ranking
  <br>Generated: {now} · {len(results)} factors
</p>

<!-- Key Metrics -->
<div class="card">
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;text-align:center">
<div><div class="summary-num" style="color:#3fb950">{len(rising)}</div><div class="summary-label">🔥 Rising Factors</div></div>
<div><div class="summary-num" style="color:#f85149">{len(decaying)}</div><div class="summary-label">⚠️ Decaying Factors</div></div>
<div><div class="summary-num" style="color:#58a6ff">{len(results)}</div><div class="summary-label">Total Factors</div></div>
<div><div class="summary-num" style="color:#d2991d">{sum(1 for _,r in results.items() if r['dir_flip'])}</div><div class="summary-label">↻ Direction Flips</div></div>
</div>
</div>

<!-- Decay Alerts -->
{decay_html}

<!-- Rising Stars -->
{rising_html}

<!-- Substitute Recommendations -->
{sub_html}

<!-- Factor Ranking Table -->
<div class="card">
<h2>📊 全因子排名 (按 |全周期 IC_IR| 降序)</h2>
<div style="max-height:600px;overflow-y:auto">
<table>
<thead><tr>
  <th>因子</th><th>全周期IC</th><th>全周期IR</th><th>12m IR</th><th>6m IR</th><th>3m IR</th><th>最新IC</th><th>动量</th><th>状态</th><th>强度</th><th>方向</th>
</tr></thead>
<tbody>
{factor_rows}
</tbody>
</table>
</div>
</div>

<p style="color:#484f58;font-size:10px;text-align:center;margin-top:24px">
  Factor Monitor v2 · Rolling windows: 3-month, 6-month, 12-month · 
  Momentum = |3m IC_IR| - |Full IC_IR| · 
  Rising = momentum > 0.1 · Decaying = |3m IR| < 30% of |Full IR|
</p>

</body>
</html>"""
    return html

# ── Main ──
def main():
    embedded = '--embedded' in sys.argv
    
    if embedded:
        # Use embedded data from existing dashboard (fallback)
        print("Using embedded data mode...")
        # This is for demo when ClickHouse is unreachable
        # We'll load from existing dashboard HTML
        import re
        dashboard_path = '/opt/openhuman/dashboard/index.html'
        if not os.path.exists(dashboard_path):
            print("No existing dashboard found")
            return
        
        with open(dashboard_path) as f:
            content = f.read()
        
        # Extract IC series
        ic_match = re.search(r'const icSeries = {(.*?)};', content, re.DOTALL)
        labels_match = re.search(r'const icLabels = \[(.*?)\];', content, re.DOTALL)
        
        if not ic_match or not labels_match:
            print("No embedded IC data in dashboard")
            return
        
        ic_text = ic_match.group(1)
        labels_text = labels_match.group(1)
        dates = re.findall(r'"([^"]*)"', labels_text)
        
        ic_data = {}
        summary = {}
        
        for series_match in re.finditer(r'"(\w+)":\s*\[([^\]]+)\]', ic_text):
            name = series_match.group(1)
            vals = [float(x) for x in series_match.group(2).split(',')]
            ic_data[name] = [(dates[i], vals[i]) for i in range(len(vals))]
            
            m = sum(vals)/len(vals)
            s = math.sqrt(sum((v-m)**2 for v in vals)/(len(vals)-1))
            summary[name] = {
                'ic_mean': m, 'ic_ir': m/s if s>0 else 0,
                'ic_std': s, 'win_rate': sum(1 for v in vals if v>0)*100/len(vals),
                'n_months': len(vals)
            }
        
        # Also parse factorData for full factor list
        factor_match = re.search(r'const factorData = \[(.*?)\];', content, re.DOTALL)
        if factor_match:
            factor_lines = factor_match.group(1)
            for line in factor_lines.split('\n'):
                line = line.strip()
                if not line.startswith('['):
                    continue
                parts = re.findall(r'"([^"]*)"|(-?\d+\.?\d*)', line)
                parts = [p[0] if p[0] else float(p[1]) if p[1] else None for p in parts]
                if len(parts) >= 6 and parts[0] not in summary:
                    summary[parts[0]] = {
                        'ic_mean': parts[1], 'ic_ir': parts[2],
                        'ic_std': parts[3], 'win_rate': parts[4],
                        'n_months': int(parts[5])
                    }
        
        print(f"Loaded {len(ic_data)} factors with IC series, {len(summary)} total")
    else:
        # Load from ClickHouse
        print("Loading from ClickHouse...")
        ic_data = load_ic_data_clickhouse()
        summary = load_ic_summary_clickhouse()
        print(f"Loaded {len(ic_data)} factors with IC series, {len(summary)} total")
    
    # Analyze
    results = analyze_factors(ic_data, summary)
    print(f"Analyzed {len(results)} factors")
    
    # Find substitutes for key factors
    substitute_map = {}
    
    # When small-cap proxy (avg_amount_1m/log_amount_1m) decays:
    small_cap_factors = ['avg_amount_1m', 'log_amount_1m', 'log_price']
    for target in small_cap_factors:
        if target in results:
            subs = find_substitutes(results, target_dir=results[target]['orig_dir'], exclude=small_cap_factors)
            substitute_map[target] = subs
    
    # When low-vol factors decay:
    low_vol_factors = ['volatility_1m', 'volatility_3m', 'volatility_6m']
    for target in low_vol_factors:
        if target in results:
            subs = find_substitutes(results, target_dir=results[target]['orig_dir'], exclude=low_vol_factors)
            if target not in substitute_map:
                substitute_map[target] = subs
    
    # Generate HTML
    html = generate_html(results, substitute_map)
    
    output_path = '/opt/quant/factor_monitor_v2.html'
    with open(output_path, 'w') as f:
        f.write(html)
    
    print(f"Dashboard written to {output_path} ({len(html)} bytes)")
    
    # Print key findings
    decaying = [(name, r) for name, r in results.items() if r['is_decaying']]
    rising = [(name, r) for name, r in results.items() if r['momentum'] > 0.1]
    
    print(f"\n=== Summary ===")
    print(f"Total factors: {len(results)}")
    print(f"Rising: {len(rising)}")
    print(f"Decaying: {len(decaying)}")
    print(f"Direction flips: {sum(1 for _,r in results.items() if r['dir_flip'])}")
    
    if rising:
        print(f"\nTop rising factors:")
        rising.sort(key=lambda x: x[1]['momentum'], reverse=True)
        for name, r in rising[:5]:
            print(f"  {name}: IR {r['full_ir']:+.3f} → {r['ir3']:+.3f} (momentum +{r['momentum']:.3f})")
    
    if decaying:
        print(f"\nDecaying factors:")
        for name, r in decaying[:5]:
            print(f"  {name}: IR {r['full_ir']:+.3f} → {r['ir3']:+.3f} (latest IC: {r['latest_ic']:+.3f})")

if __name__ == '__main__':
    main()
