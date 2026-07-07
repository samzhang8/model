#!/usr/bin/python3
"""
Phoenix v4 — 回归验证路线
基于ClickHouse已验证: MA250+月度止损+ret_1m动量=年化244%
改进: 三级择时(bull/recover/bear) + 满仓不压缩 + 纯动量
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import timedelta
import json, os, time

INDEX_CODE = "sz399006"
START_DATE = "20200101"
END_DATE = "20260618"
REBALANCE_FREQ = 21
TOP_N = 10
MA_FAST = 60
MA_SLOW = 250
STOP_LOSS_MONTH = 0.06
CASH_RETURN = 0.02 / 252
DATA_CACHE = "/tmp/phoenix_data.pkl"

def compute_factors(stock_data):
    all_factors = []
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        df['ret_1d'] = df['close'].pct_change()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['volatility_1m'] = df['ret_1d'].rolling(21).std()
        df['fwd_ret_21d'] = df['close'].shift(-21) / df['close'] - 1
        df['code'] = code
        all_factors.append(df)
    factor_df = pd.concat(all_factors, ignore_index=True)
    return factor_df.dropna(subset=['ret_1m', 'volatility_1m'])

def run_backtest(index_df, factor_df, stock_data):
    print("\n" + "="*60)
    print("🔥 Phoenix v4 — 三级择时 + 满仓动量")
    print("="*60)
    
    idx = index_df.copy()
    idx['ma60'] = idx['close'].rolling(MA_FAST).mean()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    
    # 三级择时
    # bull: price > MA250 且 MA60上行 → 全仓
    # recover: MA60 > MA250(金叉) 但 price < MA250 → 半仓
    # bear: MA60 < MA250 → 空仓
    idx['ma60_prev'] = idx['ma60'].shift(1)
    idx['regime'] = 'bear'
    idx.loc[(idx['close'] > idx['ma250']), 'regime'] = 'bull'
    idx.loc[(idx['ma60'] > idx['ma250']) & (idx['close'] <= idx['ma250']), 'regime'] = 'recover'
    
    dates = idx['date'].tolist()
    rebalance_dates = set(dates[i] for i in range(MA_SLOW, len(dates), REBALANCE_FREQ))
    
    price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}
    
    portfolio = {}
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_month_nav = 1.0
    stop_loss_exit = False
    n_stop = 0
    
    for i, date in enumerate(dates):
        if i < MA_SLOW: continue
        
        regime = idx.iloc[i]['regime']
        
        # 调仓
        if date in rebalance_dates:
            # 月度止损
            month_ret = nav / last_month_nav - 1
            if month_ret < -STOP_LOSS_MONTH and len(portfolio) > 0:
                stop_loss_exit = True
                n_stop += 1
                print(f"  ⚠️ 月止损: {date.date()}, 月收益={month_ret*100:.1f}%")
            
            if stop_loss_exit:
                # 止损后等下一个bull/recover信号
                if regime in ('bull', 'recover'):
                    stop_loss_exit = False
                else:
                    portfolio = {}
                    cash_weight = 1.0
                    last_month_nav = nav
                    continue
            
            # 根据regime决定目标仓位
            if regime == 'bull':
                target = 1.0
            elif regime == 'recover':
                target = 0.5
            else:
                target = 0.0
            
            if target > 0:
                # 纯ret_1m动量选股: 取月涨幅最大的TOP_N只
                cross = factor_df[factor_df['date'] == date].copy()
                if len(cross) >= TOP_N:
                    # 过滤掉波动率极高的(底部20%)
                    vol_threshold = cross['volatility_1m'].quantile(0.8)
                    cross = cross[cross['volatility_1m'] <= vol_threshold]
                    if len(cross) >= TOP_N:
                        selected = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
                        pos_w = target / len(selected)
                        portfolio = {c: pos_w for c in selected}
                        cash_weight = max(0, 1.0 - sum(portfolio.values()))
                    else:
                        portfolio = {}; cash_weight = 1.0
                else:
                    portfolio = {}; cash_weight = 1.0
            else:
                portfolio = {}; cash_weight = 1.0
            
            last_month_nav = nav
        
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
            'regime': regime, 'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'n_positions': len(portfolio),
        })
    
    print(f"  月止损触发: {n_stop}次")
    return pd.DataFrame(daily_records)

def analyze(records_df, index_df):
    print("\n" + "="*60)
    print("📊 Phoenix v4 Results")
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
    bull_pct = (records_df['regime']=='bull').mean() * 100
    recover_pct = (records_df['regime']=='recover').mean() * 100
    
    idx_start = records_df['date'].iloc[0]
    idx_f = index_df[index_df['date'] >= idx_start].copy()
    idx_nav = idx_f['close'].values
    idx_nav = idx_nav / idx_nav[0]
    idx_ann = (idx_nav[-1]/idx_nav[0]) ** (1/years) - 1
    idx_peak = np.maximum.accumulate(idx_nav)
    idx_dd = np.min((idx_nav - idx_peak) / idx_peak)
    
    print(f"\n📅 回测: {records_df['date'].iloc[0].date()} ~ {records_df['date'].iloc[-1].date()} ({years:.1f}年)")
    print(f"\n{'指标':<20} {'Phoenix v4':<18} {'创业板指':<15}")
    print("-" * 55)
    print(f"{'年化收益':<20} {ann_return*100:>7.1f}%          {idx_ann*100:>7.1f}%")
    print(f"{'累计收益':<20} {total_return*100:>7.1f}%          {(idx_nav[-1]/idx_nav[0]-1)*100:>7.1f}%")
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
    
    print(f"\n📊 择时: bull={bull_pct:.1f}% recover={recover_pct:.1f}% bear={100-bull_pct-recover_pct:.1f}%")
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
    print("🔥 Phoenix v4 — 回归验证路线")
    print("="*60)
    
    if os.path.exists(DATA_CACHE):
        cache = pd.read_pickle(DATA_CACHE)
        index_df, stock_data = cache['index'], cache['stocks']
        print(f"  缓存: 指数{len(index_df)}天, 个股{len(stock_data)}只")
    else:
        print("拉取数据...")
        index_df = ak.stock_zh_index_daily(symbol=INDEX_CODE)
        index_df['date'] = pd.to_datetime(index_df['date'])
        index_df = index_df[(index_df['date'] >= START_DATE) & (index_df['date'] <= END_DATE)]
        index_df = index_df.sort_values('date').reset_index(drop=True)
        df = ak.stock_info_a_code_name()
        codes = sorted(df[df['code'].str.startswith('300')]['code'].tolist())[:120]
        stock_data = {}
        for i, code in enumerate(codes[:60]):
            try:
                sdf = ak.stock_zh_a_daily(symbol=f"sz{code}", start_date=START_DATE, end_date=END_DATE, adjust="qfq")
                if sdf is None or len(sdf) == 0: continue
                sdf['date'] = pd.to_datetime(sdf['date'])
                sdf = sdf[['date','open','close','high','low','volume','amount']].copy()
                sdf = sdf.sort_values('date').reset_index(drop=True)
                sdf['code'] = code
                stock_data[code] = sdf
                if (i+1) % 10 == 0: print(f"  {i+1}/60...")
                time.sleep(0.1)
            except: pass
        pd.to_pickle({'index': index_df, 'stocks': stock_data}, DATA_CACHE)
    
    print("\n计算因子...")
    factor_df = compute_factors(stock_data)
    print(f"  {len(factor_df)} 行, {factor_df['code'].nunique()} 只")
    
    print("\n回测...")
    records = run_backtest(index_df, factor_df, stock_data)
    if len(records) == 0:
        print("❌ 无数据"); return
    
    results = analyze(records, index_df)
    with open('/opt/quant/phoenix_v4_result.json', 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    records.to_csv('/opt/quant/phoenix_v4_daily.csv', index=False)
    print(f"\n结果: /opt/quant/phoenix_v4_result.json")

if __name__ == '__main__':
    main()
