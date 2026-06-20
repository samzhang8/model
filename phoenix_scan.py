#!/usr/bin/python3
"""Phoenix 参数扫描: 测试不同短期MA"""
import akshare as ak
import pandas as pd
import numpy as np
import json, os

DATA_CACHE = "/tmp/phoenix_data.pkl"
cache = pd.read_pickle(DATA_CACHE)
index_df, stock_data = cache['index'], cache['stocks']

# 因子
all_factors = []
for code, df in stock_data.items():
    if len(df) < 252: continue
    df = df.copy()
    df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
    df['code'] = code
    all_factors.append(df[['date', 'code', 'close', 'ret_1m']])
factor_df = pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m'])

idx = index_df.copy()
idx['ma250'] = idx['close'].rolling(250).mean()
dates = idx['date'].tolist()
start_i = 250
rebalance_dates = set(dates[i] for i in range(start_i, len(dates), 21))
price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}

print(f"{'MA_fast':<10} {'年化%':<8} {'回撤%':<8} {'夏普':<6} {'Calmar':<7} {'胜率%':<7} {'新高%':<7} {'仓位%':<7}")
print("-" * 70)

for ma_fast in [5, 8, 10, 15, 20, 25, 30]:
    idx['ma_fast'] = idx['close'].rolling(ma_fast).mean()
    idx['invest'] = ((idx['close'] > idx['ma250']) & (idx['close'] > idx['ma_fast'])).astype(int)
    
    portfolio = {}
    target_stocks = []
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_month_nav = 1.0
    
    for i, date in enumerate(dates):
        if i < start_i: continue
        invest_signal = idx.iloc[i]['invest']
        
        if date in rebalance_dates:
            month_ret = nav / last_month_nav - 1
            if month_ret < -0.05 and portfolio:
                portfolio = {}; cash_weight = 1.0; target_stocks = []
                last_month_nav = nav
                continue
            cross = factor_df[factor_df['date'] == date].copy()
            if len(cross) >= 10:
                target_stocks = cross.nlargest(10, 'ret_1m')['code'].tolist()
            last_month_nav = nav
        
        if invest_signal == 1 and target_stocks:
            if set(portfolio.keys()) != set(target_stocks):
                pos_w = 1.0 / len(target_stocks)
                portfolio = {c: pos_w for c in target_stocks}
                cash_weight = 0.0
        else:
            portfolio = {}; cash_weight = 1.0
        
        daily_ret = 0.0
        for code, weight in portfolio.items():
            if code in price_lookup:
                tp = price_lookup[code].get(date)
                yp = price_lookup[code].get(dates[i-1])
                if tp is not None and yp is not None and yp > 0:
                    daily_ret += weight * (tp / yp - 1)
        daily_ret += cash_weight * (0.02 / 252)
        nav *= (1 + daily_ret)
        peak_nav = max(peak_nav, nav)
        daily_records.append({'nav': nav, 'daily_ret': daily_ret, 'exposure': 1-cash_weight})
    
    nav_arr = np.array([r['nav'] for r in daily_records])
    years = len(nav_arr) / 252
    ann_return = (nav_arr[-1]/nav_arr[0]) ** (1/years) - 1
    daily_rets = np.array([r['daily_ret'] for r in daily_records])
    ann_vol = np.std(daily_rets) * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0
    peak = np.maximum.accumulate(nav_arr)
    max_dd = np.min((nav_arr - peak) / peak)
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0
    
    df_r = pd.DataFrame(daily_records)
    actual_dates = dates[start_i:start_i+len(daily_records)]
    df_r['month'] = pd.to_datetime(actual_dates).to_period('M')
    monthly = df_r.groupby('month').agg(ret=('daily_ret', lambda x: np.prod(1+x)-1), end_nav=('nav','last'))
    win_rate = (monthly['ret']>0).sum()/len(monthly)*100
    monthly['prev_max'] = monthly['end_nav'].cummax().shift(1).fillna(0)
    new_high = (monthly['end_nav']>monthly['prev_max']).sum()/len(monthly)*100
    exposure = np.mean([r['exposure'] for r in daily_records]) * 100
    
    print(f"MA{ma_fast:<8} {ann_return*100:>6.1f}  {max_dd*100:>6.1f}  {sharpe:>5.2f}  {calmar:>6.2f}  {win_rate:>6.1f}  {new_high:>6.1f}  {exposure:>6.1f}")
