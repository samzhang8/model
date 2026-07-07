"""
低换手率策略 — pandas本地对照版本
用于验证掘金回测结果的正确性
"""
import pandas as pd, numpy as np, json
from pathlib import Path
from datetime import datetime

KDIR = Path('/home/data/RQdata_files/kline')
print('[1/4] Loading data...')
kline = pd.concat([pd.read_parquet(f) for f in sorted(KDIR.glob('kline_*.parquet'))], ignore_index=True)
kline=kline.rename(columns={'order_book_id':'code'})
kline['date']=pd.to_datetime(kline['date'])
kline=kline.sort_values(['code','date'])

# Use all stocks (not just top 200) for realistic simulation
close=kline.pivot_table(index='date',columns='code',values='close')
vol=kline.pivot_table(index='date',columns='code',values='volume')

COMMISSION = 0.0003
STAMP_TAX = 0.0005
SLIPPAGE = 0.001
TRADE_COST = COMMISSION + SLIPPAGE
SELL_COST = COMMISSION + STAMP_TAX + SLIPPAGE
TOP_N = 20
LOOKBACK = 20

print(f'Data: {close.shape[0]}d x {close.shape[1]} stocks')

# Monthly rebalance
dates = sorted(close.index)
monthly_dates = []
for i, d in enumerate(dates):
    if i == 0 or d.month != dates[i-1].month:
        monthly_dates.append(d)

print(f'Rebalance dates: {len(monthly_dates)} months')

# Simulate
nav = 1.0
peak = 1.0
mdd = 0.0
holdings = []
nav_history = []
turnover_log = []

for mi in range(1, len(monthly_dates)):
    d = monthly_dates[mi]
    
    # Get available stocks (not NaN on this date)
    avail = close.loc[d].dropna()
    avail = avail.index[avail.index.isin(vol.columns)]
    
    if len(avail) < TOP_N * 2:
        continue
    
    # Compute turnover for each stock
    turnover = {}
    for c in avail:
        try:
            v_hist = vol[c].loc[:d].iloc[-LOOKBACK:]
            if len(v_hist) < LOOKBACK:
                continue
            avg_v = v_hist.mean()
            if avg_v > 0:
                turnover[c] = avg_v
        except:
            pass
    
    if len(turnover) < TOP_N:
        continue
    
    # Select lowest turnover
    selected = sorted(turnover.items(), key=lambda x: x[1])[:TOP_N]
    target = [s for s, _ in selected]
    
    # Compute turnover cost
    prev_holdings = holdings[-1] if holdings else []
    if prev_holdings:
        new_set = set(target)
        old_set = set(prev_holdings)
        changes = len(new_set - old_set) + len(old_set - new_set)
        turnover_pct = changes / (2 * TOP_N)
    else:
        turnover_pct = 1.0
    
    holdings.append(target)
    turnover_log.append({'date': d, 'turnover': round(turnover_pct*100,1), 'holdings': target[:5]})
    
    # Next rebalance date (next month)
    next_mi = mi + 1 if mi + 1 < len(monthly_dates) else mi
    end_d = monthly_dates[next_mi]
    
    # Simulate period
    period_dates = [pd_idx for pd_idx in dates if monthly_dates[mi] < pd_idx <= end_d]
    
    for j, pd_idx in enumerate(period_dates):
        day_rets = []
        for c in target:
            if c in close.columns and pd_idx in close.index:
                try:
                    prev_p = close[c].loc[:pd_idx].iloc[-2]
                    curr_p = close[c].loc[pd_idx]
                    if prev_p > 0:
                        ret = curr_p / prev_p - 1
                        day_rets.append(ret)
                except:
                    pass
        
        if day_rets:
            dr = sum(day_rets) / len(day_rets)
            
            # Cost on first day
            if j == 0 and turnover_pct > 0:
                dr -= turnover_pct * (TRADE_COST + SELL_COST)
            
            nav *= (1 + dr)
            nav_history.append({'date': pd_idx, 'nav': round(nav, 4)})
        
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak
        if dd > mdd:
            mdd = dd

total_days = len(nav_history)
yrs = total_days / 252
ann = round((nav ** (1 / max(yrs, 0.5)) - 1) * 100, 1)
mdd_pct = round(mdd * 100, 1)
sharpe = round(ann / max(mdd_pct, 1), 2)
total_ret = round((nav - 1) * 100, 1)
v10k = round(10000 * nav)

print(f'\n=== 低换手率策略 对照回测 ===')
print(f'年化: {ann}%')
print(f'累计: {total_ret}%')
print(f'最大回撤: -{mdd_pct}%')
print(f'夏普: {sharpe}')
print(f'1万→: ¥{v10k:,}')
print(f'调仓次数: {len(holdings)}')
print(f'平均换仓率: {np.mean([t["turnover"] for t in turnover_log])}%')

# Save NAV for comparison
output = {'nav': nav_history[-50:], 'ann': ann, 'mdd': mdd_pct, 'sharpe': sharpe}
with open('/tmp/low_turnover_result.json','w') as f: json.dump(output,f)
print('Result saved')
