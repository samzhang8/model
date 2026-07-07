#!/usr/bin/python3
"""
Phoenix v10 — 全A版本
  择时: 国证A指 MA250 + MA5 双层
  选股: 500只全A股票, ret_1m动量TOP 10
  风控: 月度5%止损安全网
对比: v9(创业板200只) vs v10(全A500只)
"""

import akshare as ak
import pandas as pd
import numpy as np
import json, os

START_DATE = "20200101"
END_DATE = "20260618"
REBALANCE_FREQ = 21
TOP_N = 10
MA_SLOW = 250
MA_FAST = 5
STOP_LOSS = 0.05
CASH_RETURN = 0.02 / 252
DATA_CACHE = "/tmp/phoenix_alla_data.pkl"
# 同时加载创业板数据做对比
DATA_CACHE_GEM = "/tmp/phoenix_data.pkl"

def compute_factors(stock_data):
    all_factors = []
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close', 'ret_1m']])
    return pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m'])

def run_backtest(index_df, factor_df, stock_data, label=""):
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['ma5'] = idx['close'].rolling(MA_FAST).mean()
    idx['big_trend'] = (idx['close'] > idx['ma250']).astype(int)
    idx['short_trend'] = (idx['close'] > idx['ma5']).astype(int)
    idx['invest'] = idx['big_trend'] * idx['short_trend']
    
    dates = idx['date'].tolist()
    start_i = MA_SLOW
    rebalance_dates = set(dates[i] for i in range(start_i, len(dates), REBALANCE_FREQ))
    price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}
    
    portfolio = {}
    target_stocks = []
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_month_nav = 1.0
    n_stop = 0
    
    for i, date in enumerate(dates):
        if i < start_i: continue
        invest_signal = idx.iloc[i]['invest']
        
        if date in rebalance_dates:
            month_ret = nav / last_month_nav - 1
            if month_ret < -STOP_LOSS and portfolio:
                portfolio = {}; cash_weight = 1.0; target_stocks = []
                n_stop += 1
                last_month_nav = nav
                continue
            cross = factor_df[factor_df['date'] == date].copy()
            if len(cross) >= TOP_N:
                target_stocks = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
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
        daily_ret += cash_weight * CASH_RETURN
        nav *= (1 + daily_ret)
        peak_nav = max(peak_nav, nav)
        daily_records.append({
            'date': date, 'nav': nav, 'daily_ret': daily_ret,
            'invest': invest_signal, 'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'n_positions': len(portfolio),
        })
    
    return pd.DataFrame(daily_records), n_stop

def analyze(records_df, index_df, label="", n_stop=0):
    nav = records_df['nav'].values
    total_days = len(nav)
    years = total_days / 252
    ann_return = (nav[-1]/nav[0]) ** (1/years) - 1
    total_return = nav[-1]/nav[0] - 1
    
    daily_rets = records_df['daily_ret'].values
    ann_vol = np.std(daily_rets) * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0
    
    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_dd = np.min(drawdown)
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0
    
    records_df['month'] = records_df['date'].dt.to_period('M')
    monthly = records_df.groupby('month').agg(
        ret=('daily_ret', lambda x: np.prod(1+x) - 1),
        end_nav=('nav', 'last'),
        avg_exposure=('exposure', 'mean')
    ).reset_index()
    
    real_invested = monthly[monthly['avg_exposure'] > 0.1]
    real_win = (real_invested['ret'] > 0).sum() / len(real_invested) * 100 if len(real_invested) > 0 else 0
    
    win_rate = (monthly['ret'] > 0).sum() / len(monthly) * 100
    monthly['prev_max'] = monthly['end_nav'].cummax().shift(1).fillna(0)
    monthly['new_high'] = monthly['end_nav'] > monthly['prev_max']
    new_high_rate = monthly['new_high'].sum() / len(monthly) * 100
    
    exposure_avg = records_df['exposure'].mean()
    invest_pct = records_df['invest'].mean() * 100
    
    idx_start = records_df['date'].iloc[0]
    idx_f = index_df[index_df['date'] >= idx_start].copy()
    idx_nav = idx_f['close'].values / idx_f['close'].values[0]
    idx_ann = (idx_nav[-1]) ** (1/years) - 1
    idx_peak = np.maximum.accumulate(idx_nav)
    idx_dd = np.min((idx_nav - idx_peak) / idx_peak)
    
    print(f"\n{'='*60}")
    print(f"📊 {label}")
    print(f"{'='*60}")
    print(f"\n📅 回测: {records_df['date'].iloc[0].date()} ~ {records_df['date'].iloc[-1].date()} ({years:.1f}年)")
    print(f"\n{'指标':<20} {label:<18} {'基准指数':<15}")
    print("-" * 55)
    print(f"{'年化收益':<20} {ann_return*100:>7.1f}%          {idx_ann*100:>7.1f}%")
    print(f"{'累计收益':<20} {total_return*100:>7.1f}%          {(idx_nav[-1]-1)*100:>7.1f}%")
    print(f"{'年化波动':<20} {ann_vol*100:>7.1f}%")
    print(f"{'夏普':<20} {sharpe:>7.2f}")
    print(f"{'最大回撤':<20} {max_dd*100:>7.1f}%          {idx_dd*100:>7.1f}%")
    print(f"{'Calmar':<20} {calmar:>7.2f}")
    print(f"{'止损次数':<20} {n_stop}")
    
    print(f"\n📊 月度:")
    print(f"  总月胜率: {win_rate:.1f}% ({(monthly['ret']>0).sum()}/{len(monthly)})")
    print(f"  实战月胜率(有仓位): {real_win:.1f}% ({(real_invested['ret']>0).sum()}/{len(real_invested)})")
    print(f"  创新高: {new_high_rate:.1f}% ({monthly['new_high'].sum()}/{len(monthly)})")
    print(f"  最佳月: {monthly['ret'].max()*100:.1f}%  最差月: {monthly['ret'].min()*100:.1f}%")
    print(f"  月均: {monthly['ret'].mean()*100:.2f}%")
    print(f"  实战月数: {len(real_invested)}/{len(monthly)}")
    print(f"  仓位: 平均{exposure_avg*100:.1f}%  在场{invest_pct:.1f}%")
    
    print(f"\n{'月份':<10} {'收益':>8} {'仓位':>6} {'新高':>6}")
    print("-" * 32)
    for _, r in monthly.iterrows():
        print(f"{str(r['month']):<10} {r['ret']*100:>6.2f}% {r['avg_exposure']*100:>4.0f}% {'✅' if r['new_high'] else '  ':>6}")
    
    print(f"\n🎯 目标评估:")
    for name, passed, value in [
        ("年化>30%", ann_return > 0.30, f"{ann_return*100:.1f}%"),
        ("回撤<10%", max_dd > -0.10, f"{max_dd*100:.1f}%"),
        ("月胜率>75%", win_rate > 75, f"{win_rate:.1f}%"),
        ("创新高>70%", new_high_rate > 70, f"{new_high_rate:.1f}%"),
        ("夏普>2.0", sharpe > 2.0, f"{sharpe:.2f}"),
        ("Calmar>3.0", calmar > 3.0, f"{calmar:.2f}"),
    ]:
        print(f"  {'✅' if passed else '❌'} {name}: {value}")
    
    return {'ann_return': ann_return, 'max_dd': max_dd, 'sharpe': sharpe,
            'calmar': calmar, 'win_rate': win_rate, 'new_high_rate': new_high_rate,
            'n_stop': n_stop}

def main():
    print("="*60)
    print("🔥 Phoenix v10 — 全A vs 创业板对比")
    print("="*60)
    
    # --- 全A ---
    print("\n[1] 加载全A数据...")
    cache_a = pd.read_pickle(DATA_CACHE)
    index_a, stocks_a = cache_a['index'], cache_a['stocks']
    idx_name = cache_a.get('index_name', '国证A指')
    print(f"  {idx_name}: {len(index_a)}天, 个股{len(stocks_a)}只")
    
    print("  计算因子...")
    factor_a = compute_factors(stocks_a)
    print(f"  {len(factor_a)}行, {factor_a['code'].nunique()}只")
    
    print("  回测...")
    records_a, n_stop_a = run_backtest(index_a, factor_a, stocks_a, "全A")
    results_a = analyze(records_a, index_a, f"Phoenix v10 全A({len(stocks_a)}只)", n_stop_a)
    
    # --- 创业板对比 ---
    print("\n[2] 加载创业板数据(对比)...")
    cache_g = pd.read_pickle(DATA_CACHE_GEM)
    index_g, stocks_g = cache_g['index'], cache_g['stocks']
    print(f"  创业板指: {len(index_g)}天, 个股{len(stocks_g)}只")
    
    print("  计算因子...")
    factor_g = compute_factors(stocks_g)
    print(f"  {len(factor_g)}行, {factor_g['code'].nunique()}只")
    
    print("  回测...")
    records_g, n_stop_g = run_backtest(index_g, factor_g, stocks_g, "创业板")
    results_g = analyze(records_g, index_g, f"Phoenix v9 创业板({len(stocks_g)}只)", n_stop_g)
    
    # --- 对比总结 ---
    print(f"\n{'='*60}")
    print(f"📊 全A vs 创业板 对比总结")
    print(f"{'='*60}")
    print(f"\n{'指标':<20} {'全A(500只)':<18} {'创业板(200只)':<18} {'差异':<10}")
    print("-" * 66)
    for key, label in [('ann_return','年化收益'), ('max_dd','最大回撤'), ('sharpe','夏普'), 
                        ('calmar','Calmar'), ('win_rate','月胜率'), ('new_high_rate','创新高')]:
        va = results_a[key] * 100
        vg = results_g[key] * 100
        diff = va - vg
        print(f"{label:<20} {va:>7.1f}%{'':>9} {vg:>7.1f}%{'':>9} {diff:>+7.1f}")
    print(f"{'止损次数':<20} {results_a['n_stop']:<18} {results_g['n_stop']:<18}")
    
    # 保存
    with open('/opt/quant/phoenix_v10_result.json', 'w') as f:
        json.dump({'alla': results_a, 'gem': results_g}, f, indent=2, ensure_ascii=False)
    records_a.to_csv('/opt/quant/phoenix_v10_daily.csv', index=False)
    print(f"\n结果: /opt/quant/phoenix_v10_result.json")

if __name__ == '__main__':
    main()
