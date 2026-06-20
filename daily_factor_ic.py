#!/usr/bin/env python3
"""Daily factor Rank IC computation from ClickHouse ods_kline_1d.

Reads CSVWithNames from stdin (piped from docker exec clickhouse), computes 17
price-momentum/volatility/volume factors, daily Rank IC (Spearman correlation
with next-day return), ranks by |IC_IR| descending, outputs TOP10 table +
3-day-decline warnings.

Usage (from Hermes terminal, 43.155 or local):
  sshpass -p '<pwd>' ssh root@115.159.73.134 \
    "docker exec clickhouse clickhouse-client --query \
     \"SELECT symbol, toString(trade_date) as td, open, high, low, close, volume, amount
      FROM amazingdata.ods_kline_1d
      WHERE trade_date >= '<start>' AND trade_date <= '<end>'
        AND close > 0 AND volume > 0
      ORDER BY symbol, trade_date
      FORMAT CSVWithNames\"" 2>/dev/null \
  | /usr/bin/python3 /opt/quant/daily_factor_ic.py

Requires: Python 3.8+ (stdlib only — no pip deps).
"""
import sys, math, csv, io
from collections import defaultdict

# --- Read CSV ---
data = sys.stdin.read()
reader = csv.DictReader(io.StringIO(data))
rows = []
for r in reader:
    rows.append({
        'symbol': r['symbol'], 'trade_date': r['td'],
        'close': float(r['close']), 'open': float(r['open']),
        'high': float(r['high']), 'low': float(r['low']),
        'volume': float(r['volume']), 'amount': float(r['amount']),
    })

rows.sort(key=lambda x: (x['symbol'], x['trade_date']))

by_symbol = defaultdict(list)
for r in rows:
    by_symbol[r['symbol']].append(r)

all_dates = sorted(set(r['trade_date'] for r in rows))
N_dates = len(all_dates)

close_grid = {}; volume_grid = {}; amount_grid = {}; high_grid = {}; low_grid = {}
for sym, sym_rows in by_symbol.items():
    dm = {r['trade_date']: r for r in sym_rows}
    close_grid[sym]  = [dm[d]['close'] if d in dm else None for d in all_dates]
    volume_grid[sym] = [dm[d]['volume'] if d in dm else None for d in all_dates]
    amount_grid[sym] = [dm[d]['amount'] if d in dm else None for d in all_dates]
    high_grid[sym]   = [dm[d]['high'] if d in dm else None for d in all_dates]
    low_grid[sym]    = [dm[d]['low'] if d in dm else None for d in all_dates]

# --- Factor functions ---
def ret_n(c, i, w):
    if i < w: return None
    p, cur = c[i-w], c[i]
    if p is None or cur is None or p == 0: return None
    return (cur - p) / p

def volatility(c, i, w):
    if i < w: return None
    rs = []
    for j in range(i-w+1, i+1):
        if c[j] is None or c[j-1] is None or c[j-1] == 0: return None
        rs.append((c[j] - c[j-1]) / c[j-1])
    if len(rs) < 2: return None
    m = sum(rs) / len(rs)
    return math.sqrt(sum((r-m)**2 for r in rs) / (len(rs)-1))

def avg_amount(a, i, w):
    if i < w: return None
    vs = [a[j] for j in range(i-w+1, i+1) if a[j] is not None]
    return sum(vs) / len(vs) if vs else None

def extreme_ret(c, i, w, mode='max'):
    if i < w: return None
    best = None
    for j in range(i-w+1, i+1):
        if c[j] is None or c[j-1] is None or c[j-1] == 0: return None
        r = (c[j] - c[j-1]) / c[j-1]
        if best is None or (mode == 'max' and r > best) or (mode == 'min' and r < best):
            best = r
    return best

def up_ratio(c, i, w):
    if i < w: return None
    up = total = 0
    for j in range(i-w+1, i+1):
        if c[j] is None or c[j-1] is None: return None
        total += 1
        if c[j] > c[j-1]: up += 1
    return up / total if total > 0 else None

def turnover(vol, amt, i, w):
    if i < w: return None
    rs = []
    for j in range(i-w+1, i+1):
        if vol[j] and amt[j] and amt[j] > 0:
            rs.append(vol[j] / amt[j])
    return sum(rs) / len(rs) if rs else None

def ma(c, i, w):
    if i < w: return None
    vs = [c[j] for j in range(i-w+1, i+1) if c[j] is not None]
    return sum(vs) / len(vs) if vs else None

def stddev(c, i, w):
    if i < w: return None
    vs = [c[j] for j in range(i-w+1, i+1) if c[j] is not None]
    if len(vs) < 2: return None
    m = sum(vs) / len(vs)
    return math.sqrt(sum((v-m)**2 for v in vs) / (len(vs)-1))

def bb_position(c, i, w=20):
    if i < w: return None
    m = ma(c, i, w); s = stddev(c, i, w); cur = c[i]
    if m is None or s is None or s == 0 or cur is None: return None
    return (cur - m) / (2 * s)

def rsi_14(c, i):
    w = 14
    if i < w + 1: return None
    gains, losses = [], []
    for j in range(i-w, i+1):
        if c[j] is None or c[j-1] is None: return None
        d = c[j] - c[j-1]
        if d >= 0: gains.append(d); losses.append(0)
        else: gains.append(0); losses.append(-d)
    ag = sum(gains) / len(gains); al = sum(losses) / len(losses)
    if al == 0: return 100.0
    return 100.0 - 100.0 / (1 + ag / al)

def amplitude(h, l, c, i, w):
    if i < w: return None
    vs = []
    for j in range(i-w+1, i+1):
        if h[j] is None or l[j] is None or c[j-1] is None or c[j-1] == 0: return None
        vs.append((h[j] - l[j]) / c[j-1])
    return sum(vs) / len(vs) if vs else None

def high_low_range(h, l, c, i, w):
    if i < w: return None
    hs = [h[j] for j in range(i-w+1, i+1) if h[j] is not None]
    ls = [l[j] for j in range(i-w+1, i+1) if l[j] is not None]
    cs = [c[j] for j in range(i-w+1, i+1) if c[j] is not None]
    if not hs or not ls or not cs: return None
    ac = sum(cs) / len(cs)
    return (max(hs) - min(ls)) / ac if ac > 0 else None

def illiquidity(c, amt, i, w):
    if i < w: return None
    vs = []
    for j in range(i-w+1, i+1):
        if c[j] is None or c[j-1] is None or amt[j] is None: return None
        if amt[j] == 0 or c[j-1] == 0: return None
        vs.append(abs((c[j]-c[j-1])/c[j-1]) / amt[j] * 1e8)
    return sum(vs) / len(vs) if vs else None

def vol_convergence(vol, i, short=5, long=20):
    if i < long: return None
    sv = [vol[j] for j in range(i-short+1, i+1) if vol[j] is not None]
    lv = [vol[j] for j in range(i-long+1, i+1) if vol[j] is not None]
    if not sv or not lv: return None
    sa = sum(sv) / len(sv); la = sum(lv) / len(lv)
    return sa / la if la > 0 else None

def volume_ratio(vol, i, w=5):
    if i < w: return None
    pv = [vol[j] for j in range(i-w, i) if vol[j] is not None]
    if not pv or vol[i] is None: return None
    a = sum(pv) / len(pv)
    return vol[i] / a if a > 0 else None

def ret_vol_adj(c, i, w=20):
    if i < w: return None
    r = ret_n(c, i, w); v = volatility(c, i, w)
    return r / v if r is not None and v is not None and v > 0 else None

# --- Factor definitions ---
FACTORS = {
    'ret_5d':           lambda a,i: ret_n(close_grid[a], i, 5),
    'ret_1m':           lambda a,i: ret_n(close_grid[a], i, 20),
    'volatility_1m':    lambda a,i: volatility(close_grid[a], i, 20),
    'reversal':         lambda a,i: (-ret_n(close_grid[a], i, 5) if ret_n(close_grid[a], i, 5) is not None else None),
    'avg_amount_log':   lambda a,i: (math.log(x) if (x:=avg_amount(amount_grid[a], i, 20)) and x>0 else None),
    'max_ret_1m':       lambda a,i: extreme_ret(close_grid[a], i, 20, 'max'),
    'min_ret_1m':       lambda a,i: extreme_ret(close_grid[a], i, 20, 'min'),
    'up_ratio_1m':      lambda a,i: up_ratio(close_grid[a], i, 20),
    'bb_position':      lambda a,i: bb_position(close_grid[a], i, 20),
    'rsi_14':           lambda a,i: rsi_14(close_grid[a], i),
    'amplitude_1m':     lambda a,i: amplitude(high_grid[a], low_grid[a], close_grid[a], i, 20),
    'high_low_1m':      lambda a,i: high_low_range(high_grid[a], low_grid[a], close_grid[a], i, 20),
    'illiquidity':      lambda a,i: illiquidity(close_grid[a], amount_grid[a], i, 20),
    'vol_convergence':  lambda a,i: vol_convergence(volume_grid[a], i, 5, 20),
    'volume_ratio':     lambda a,i: volume_ratio(volume_grid[a], i, 5),
    'turnover':         lambda a,i: turnover(volume_grid[a], amount_grid[a], i, 20),
    'ret_vol_adj':      lambda a,i: ret_vol_adj(close_grid[a], i, 20),
}

LOOKBACK = 20  # trading days for factor computation
start_idx = LOOKBACK + 1          # need LOOKBACK prior days
end_idx = N_dates - 2             # need next day for return

# --- Compute factors ---
factor_daily = {f: defaultdict(list) for f in FACTORS}
for sym in close_grid:
    closes = close_grid[sym]
    if sum(1 for c in closes if c is not None) < LOOKBACK + 2: continue
    for day_i in range(start_idx, end_idx + 1):
        if closes[day_i] is None or closes[day_i+1] is None or closes[day_i] == 0: continue
        nxt = (closes[day_i+1] - closes[day_i]) / closes[day_i]
        for fn, ff in FACTORS.items():
            fv = ff(sym, day_i)
            if fv is not None and math.isfinite(fv):
                factor_daily[fn][day_i].append((fv, nxt))

# --- Spearman Rank IC ---
def spearman_ic(pairs):
    n = len(pairs)
    if n < 30: return None
    fv = [p[0] for p in pairs]; nr = [p[1] for p in pairs]

    def rank_vals(vals):
        o = sorted(range(n), key=lambda i: vals[i])
        r = [0]*n; i = 0
        while i < n:
            j = i
            while j < n and vals[o[j]] == vals[o[i]]: j += 1
            ar = (i+j-1)/2.0 + 1
            for k in range(i, j): r[o[k]] = ar
            i = j
        return r

    rf = rank_vals(fv); rr = rank_vals(nr)
    mf = sum(rf)/n; mr = sum(rr)/n
    cov = sum((rf[i]-mf)*(rr[i]-mr) for i in range(n))
    sf = math.sqrt(sum((x-mf)**2 for x in rf))
    sr = math.sqrt(sum((x-mr)**2 for x in rr))
    return cov/(sf*sr) if sf>0 and sr>0 else None

# --- Compute ICs ---
ic_results = {}
for fn in FACTORS:
    ic_results[fn] = {}
    for di, vals in factor_daily[fn].items():
        ic = spearman_ic(vals)
        if ic is not None:
            ic_results[fn][di] = ic

# --- Summarize ---
summary = []
for fn in FACTORS:
    ics = ic_results[fn]
    if not ics: continue
    iv = list(ics.values())
    n = len(iv)
    m = sum(iv)/n
    s = math.sqrt(sum((v-m)**2 for v in iv)/(n-1)) if n>1 else 0
    ir = m/s if s>0 else 0
    l5 = iv[-5:] if n>=5 else iv
    d3 = n>=3 and iv[-3]>iv[-2]>iv[-1]
    summary.append(dict(factor=fn, ic_mean=m, ic_std=s, ic_ir=ir,
                        n_days=n, ic_vals=iv, last_5=l5, decline_3d=d3))

summary.sort(key=lambda x: abs(x['ic_ir']), reverse=True)
top10 = summary[:10]

# --- Output ---
print()
print("="*115)
print(f"  因子 Rank IC 日报 — ClickHouse amazingdata.ods_kline_1d")
print(f"  数据区间: {all_dates[0]} ~ {all_dates[-1]} | {N_dates}个交易日 | {len(by_symbol)}只股票")
print(f"  IC计算窗口: {all_dates[start_idx]} ~ {all_dates[end_idx]} ({end_idx-start_idx+1}天)")
print("="*115)
print(f"{'因子':<22} {'IC均值':>8} {'IC_IR':>8} {'IC标准差':>8} {'方向':>6} {'N天':>5}  {'最近5日IC趋势':>50} {'警示'}")
print("-"*115)

for s in top10:
    d = '正向' if s['ic_mean'] > 0 else '负向'
    l5 = "  ".join(f"{v:+.4f}" for v in s['last_5'])
    w = "⚠️连跌3天" if s['decline_3d'] else ""
    print(f"{s['factor']:<22} {s['ic_mean']:>+8.4f} {s['ic_ir']:>+8.4f} {s['ic_std']:>8.4f} {d:>6} {s['n_days']:>5}  {l5:<50} {w}")

print("-"*115)

top = top10[0]
dw = "正向追涨" if top['ic_mean'] > 0 else "负向反转"
print(f"\n📊 {top['factor']}因子IC_IR最强({top['ic_ir']:+.3f})，方向为{dw}。")

warns = [s for s in top10 if s['decline_3d']]
if warns:
    print("\n⚠️ IC连续下跌警示:")
    for w in warns:
        print(f"  {w['factor']}: {w['ic_vals'][-3]:+.4f} → {w['ic_vals'][-2]:+.4f} → {w['ic_vals'][-1]:+.4f}")
else:
    print("\n✅ 无因子IC连续3日下跌。")

# Daily detail table
print(f"\n{'─'*115}")
print(f"每日IC明细 (TOP10因子)")
print(f"{'日期':<12}", end="")
for s in top10:
    print(f" {s['factor']:>16}", end="")
print()
print(f"{'─'*115}")
for day_i in range(start_idx, end_idx+1):
    print(f"{all_dates[day_i]:<12}", end="")
    for s in top10:
        ic = ic_results[s['factor']].get(day_i)
        print(f" {ic:>+16.4f}" if ic is not None else f" {'N/A':>16}", end="")
    print()
print(f"{'─'*115}")
