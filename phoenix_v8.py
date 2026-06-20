#!/usr/bin/python3
"""
Phoenix v8 — 双层MA趋势跟踪(替代止损)
Layer 1: MA250大趋势( invest or not)
Layer 2: MA20短期趋势( stay or temporary exit)
+ 月度5%止损作为最后安全网
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
MA_FAST = 20        # 短期趋势
STOP_LOSS = 0.05    # 月度5%安全网
CASH_RETURN = 0.02 / 252
DATA_CACHE = "/tmp/phoenix_data.pkl"

def compute_factors(stock_data):
    all_factors = []
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close', 'ret_1m']])
    return pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m'])

def run_backtest(index_df, factor_df, stock_data):
    print("\n" + "="*60)
    print("🔥 Phoenix v8 — 双层MA趋势跟踪(MA250+MA20)")
    print("="*60)
    
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['ma20'] = idx['close'].rolling(MA_FAST).mean()
    
    # 双层信号
    # 大趋势: close > MA250
    # 短期趋势: close > MA20
    # 投资: 大趋势=多 AND 短期=多
    idx['big_trend'] = (idx['close'] > idx['ma250']).astype(int)
    idx['short_trend'] = (idx['close'] > idx['ma20']).astype(int)
    idx['invest'] = idx['big_trend'] * idx['short_trend']  # 1=投资, 0=空仓
    
    dates = idx['date'].tolist()
    start_i = MA_SLOW
    rebalance_dates = set(dates[i] for i in range(start_i, len(dates), REBALANCE_FREQ))
    price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}
    
    portfolio = {}
    target_stocks = []  # 目标持仓股票列表(调仓时选定)
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_month_nav = 1.0
    n_stop = 0
    
    for i, date in enumerate(dates):
        if i < start_i: continue
        
        invest_signal = idx.iloc[i]['invest']
        
        # 调仓(每21天选股)
        if date in rebalance_dates:
            # 月度止损安全网
            month_ret = nav / last_month_nav - 1
            if month_ret < -STOP_LOSS and portfolio:
                portfolio = {}
                cash_weight = 1.0
                target_stocks = []
                n_stop += 1
                print(f"  ⚠️ 月止损: {date.date()}, 月收益={month_ret*100:.1f}%")
                last_month_nav = nav
                # 记录但不立即重建仓位, 等invest_signal
                continue
            
            # 选股(不管当前信号, 都更新目标列表)
            cross = factor_df[factor_df['date'] == date].copy()
            if len(cross) >= TOP_N:
                target_stocks = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
            
            last_month_nav = nav
        
        # 根据双层信号决定是否持仓
        if invest_signal == 1 and target_stocks:
            # 确保持仓是最新的target
            if set(portfolio.keys()) != set(target_stocks):
                pos_w = 1.0 / len(target_stocks)
                portfolio = {c: pos_w for c in target_stocks}
                cash_weight = 0.0
            else:
                # 已经持有正确股票
                pass
        else:
            # 信号为空, 清仓
            portfolio = {}
            cash_weight = 1.0
        
        # 逐日收益
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
            'big_trend': idx.iloc[i]['big_trend'],
            'short_trend': idx.iloc[i]['short_trend'],
            'invest': invest_signal,
            'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'n_positions': len(portfolio),
        })
    
    print(f"  月止损触发: {n_stop}次")
    return pd.DataFrame(daily_records)

def analyze(records_df, index_df):
    print("\n" + "="*60)
    print("📊 Phoenix v8 Results")
    print("="*60)
    
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
    big_pct = records_df['big_trend'].mean() * 100
    short_pct = records_df['short_trend'].mean() * 100
    
    idx_start = records_df['date'].iloc[0]
    idx_f = index_df[index_df['date'] >= idx_start].copy()
    idx_nav = idx_f['close'].values / idx_f['close'].values[0]
    idx_ann = (idx_nav[-1]) ** (1/years) - 1
    idx_peak = np.maximum.accumulate(idx_nav)
    idx_dd = np.min((idx_nav - idx_peak) / idx_peak)
    
    print(f"\n📅 回测: {records_df['date'].iloc[0].date()} ~ {records_df['date'].iloc[-1].date()} ({years:.1f}年)")
    print(f"\n{'指标':<20} {'Phoenix v8':<18} {'创业板指':<15}")
    print("-" * 55)
    print(f"{'年化收益':<20} {ann_return*100:>7.1f}%          {idx_ann*100:>7.1f}%")
    print(f"{'累计收益':<20} {total_return*100:>7.1f}%          {(idx_nav[-1]-1)*100:>7.1f}%")
    print(f"{'年化波动':<20} {ann_vol*100:>7.1f}%")
    print(f"{'夏普':<20} {sharpe:>7.2f}")
    print(f"{'最大回撤':<20} {max_dd*100:>7.1f}%          {idx_dd*100:>7.1f}%")
    print(f"{'Calmar':<20} {calmar:>7.2f}")
    
    print(f"\n📊 月度:")
    print(f"  总月胜率: {win_rate:.1f}% ({(monthly['ret']>0).sum()}/{len(monthly)})")
    print(f"  实战月胜率(有仓位): {real_win:.1f}% ({(real_invested['ret']>0).sum()}/{len(real_invested)})")
    print(f"  创新高: {new_high_rate:.1f}% ({monthly['new_high'].sum()}/{len(monthly)})")
    print(f"  最佳月: {monthly['ret'].max()*100:.1f}%  最差月: {monthly['ret'].min()*100:.1f}%")
    print(f"  月均: {monthly['ret'].mean()*100:.2f}%")
    print(f"  实战月数: {len(real_invested)}/{len(monthly)}")
    
    print(f"\n📊 择时: 大趋势{big_pct:.1f}% 短期{short_pct:.1f}% 双层共振{invest_pct:.1f}%")
    print(f"  平均仓位: {exposure_avg*100:.1f}%")
    
    print(f"\n{'月份':<10} {'收益':>8} {'仓位':>6} {'新高':>6}")
    print("-" * 32)
    for _, r in monthly.iterrows():
        print(f"{str(r['month']):<10} {r['ret']*100:>6.2f}% {r['avg_exposure']*100:>4.0f}% {'✅' if r['new_high'] else '  ':>6}")
    
    print(f"\n{'='*60}")
    print(f"🎯 目标评估:")
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
            'calmar': calmar, 'win_rate': win_rate, 'new_high_rate': new_high_rate}

def main():
    print("="*60)
    print("🔥 Phoenix v8 — 双层MA(MA250+MA20)趋势跟踪")
    print("="*60)
    
    cache = pd.read_pickle(DATA_CACHE)
    index_df, stock_data = cache['index'], cache['stocks']
    print(f"  指数{len(index_df)}天, 个股{len(stock_data)}只")
    
    print("\n计算因子...")
    factor_df = compute_factors(stock_data)
    print(f"  {len(factor_df)} 行, {factor_df['code'].nunique()} 只")
    
    print("\n回测...")
    records = run_backtest(index_df, factor_df, stock_data)
    if len(records) == 0:
        print("❌ 无数据"); return
    
    results = analyze(records, index_df)
    with open('/opt/quant/phoenix_v8_result.json', 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    records.to_csv('/opt/quant/phoenix_v8_daily.csv', index=False)
    print(f"\n结果: /opt/quant/phoenix_v8_result.json")

if __name__ == '__main__':
    main()
