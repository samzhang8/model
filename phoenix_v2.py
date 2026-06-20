#!/usr/bin/python3
"""
Phoenix Strategy v2 — 激进Alpha + 精准风控
优化: 去掉冗余波动率压缩, 择时为多时全仓, 放宽回撤阈值
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
MA_WINDOW = 250
STOP_LOSS = 0.06          # 月止损6%(放宽一点减少假触发)
DD_WARN = 0.08            # 回撤预警8%
DD_REDUCE = 0.11          # 回撤减仓11%
DD_KILL = 0.13            # 回撤清仓13%
MAX_POSITION = 0.15
CASH_RETURN = 0.02 / 252
DATA_CACHE = "/tmp/phoenix_data.pkl"

def fetch_index_data():
    df = ak.stock_zh_index_daily(symbol=INDEX_CODE)
    df['date'] = pd.to_datetime(df['date'])
    df = df[(df['date'] >= START_DATE) & (df['date'] <= END_DATE)]
    return df.sort_values('date').reset_index(drop=True)

def fetch_stock_universe():
    df = ak.stock_info_a_code_name()
    gem = df[df['code'].str.startswith('300')]
    return sorted(gem['code'].tolist())[:120]

def fetch_stock_data(codes, max_stocks=60):
    all_data = {}
    for i, code in enumerate(codes[:max_stocks]):
        try:
            df = ak.stock_zh_a_daily(symbol=f"sz{code}", 
                                     start_date=START_DATE, end_date=END_DATE,
                                     adjust="qfq")
            if df is None or len(df) == 0: continue
            df['date'] = pd.to_datetime(df['date'])
            df = df[['date','open','close','high','low','volume','amount']].copy()
            df = df.sort_values('date').reset_index(drop=True)
            df['code'] = code
            all_data[code] = df
            if (i+1) % 10 == 0:
                print(f"  {i+1}/{max_stocks}...")
            time.sleep(0.1)
        except:
            pass
    return all_data

def compute_factors(stock_data):
    all_factors = []
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        df['ret_1d'] = df['close'].pct_change()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['volatility_1m'] = df['ret_1d'].rolling(21).std()
        ma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        df['bb_position'] = (df['close'] - ma20) / (2 * std20)
        avg_amount = df['amount'].rolling(21).mean()
        df['illiquidity'] = (df['ret_1m'].abs() / avg_amount) * 1e8
        df['turnover'] = df['amount'] / df['close']
        df['max_ret_1m'] = df['ret_1d'].rolling(21).max()
        df['min_ret_1m'] = df['ret_1d'].rolling(21).min()
        df['volume_ratio'] = df['volume'].rolling(5).mean() / df['volume'].rolling(20).mean()
        df['fwd_ret_21d'] = df['close'].shift(-21) / df['close'] - 1
        df['code'] = code
        all_factors.append(df)
    factor_df = pd.concat(all_factors, ignore_index=True)
    return factor_df.dropna(subset=['ret_1m', 'volatility_1m', 'bb_position'])

def compute_rolling_ic_weights(factor_df, current_date, lookback_days=365):
    hist = factor_df[(factor_df['date'] <= current_date) & 
                     (factor_df['date'] >= current_date - timedelta(days=lookback_days))]
    if len(hist) < 100: return None
    
    factor_cols = ['ret_1m','volatility_1m','bb_position','illiquidity',
                   'turnover','max_ret_1m','min_ret_1m','volume_ratio']
    weights = {}
    for col in factor_cols:
        valid = hist[[col, 'fwd_ret_21d']].dropna()
        if len(valid) < 50: continue
        ic = valid[col].corr(valid['fwd_ret_21d'], method='spearman')
        if abs(ic) > 0.02:
            weights[col] = ic
    if not weights: return None
    total = sum(abs(v) for v in weights.values())
    return {k: v/total for k, v in weights.items()}

def select_stocks(factor_df, current_date, weights, top_n=10):
    cross = factor_df[factor_df['date'] == current_date].copy()
    if len(cross) < top_n: return []
    cross['score'] = 0.0
    for col, w in weights.items():
        cross['score'] += cross[col].rank(pct=True) * w
    return cross.nlargest(top_n, 'score')['code'].tolist()

def run_backtest(index_df, factor_df, stock_data):
    print("\n" + "="*60)
    print("🔥 Phoenix v2 Backtest")
    print("="*60)
    
    index_df = index_df.copy()
    index_df['ma250'] = index_df['close'].rolling(MA_WINDOW).mean()
    index_df['timing'] = (index_df['close'] > index_df['ma250']).astype(int)
    
    # 调仓日
    dates = index_df['date'].tolist()
    rebalance_dates = set(dates[i] for i in range(MA_WINDOW, len(dates), REBALANCE_FREQ))
    
    # 价格lookup
    price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}
    
    portfolio = {}
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_month_nav = 1.0
    stop_loss_active = False
    
    for i, date in enumerate(dates):
        if i < MA_WINDOW: continue
        
        timing = index_df.iloc[i]['timing']
        
        # 回撤控制
        current_dd = (nav - peak_nav) / peak_nav if peak_nav > 0 else 0
        dd_scaler = 1.0
        if current_dd <= -DD_KILL:
            dd_scaler = 0.0
        elif current_dd <= -DD_REDUCE:
            dd_scaler = 0.3
        elif current_dd <= -DD_WARN:
            dd_scaler = 0.6
        
        # 调仓
        if date in rebalance_dates:
            # 检查上月是否止损
            month_ret = nav / last_month_nav - 1
            if month_ret < -STOP_LOSS:
                stop_loss_active = True
                print(f"  ⚠️ 止损: {date.date()}, 月收益={month_ret*100:.1f}%")
            
            if stop_loss_active:
                # 止损后空仓一个月
                portfolio = {}
                cash_weight = 1.0
                stop_loss_active = False  # 下个月恢复
            elif timing == 1 and dd_scaler > 0:
                weights = compute_rolling_ic_weights(factor_df, date)
                if weights:
                    selected = select_stocks(factor_df, date, weights, TOP_N)
                    if selected:
                        # 全仓(受dd_scaler约束), 不再压缩
                        base_w = min(1.0 / len(selected), MAX_POSITION) * dd_scaler
                        portfolio = {c: base_w for c in selected}
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
            'timing': timing, 'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'n_positions': len(portfolio),
        })
    
    return pd.DataFrame(daily_records)

def analyze_results(records_df, index_df):
    print("\n" + "="*60)
    print("📊 Phoenix v2 Results")
    print("="*60)
    
    nav = records_df['nav'].values
    total_days = len(nav)
    years = total_days / 252
    ann_return = (nav[-1] / nav[0]) ** (1/years) - 1
    total_return = nav[-1] / nav[0] - 1
    
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
        end_nav=('nav', 'last')
    ).reset_index()
    
    win_rate = (monthly['ret'] > 0).sum() / len(monthly) * 100
    monthly['prev_max'] = monthly['end_nav'].cummax().shift(1).fillna(0)
    monthly['new_high'] = monthly['end_nav'] > monthly['prev_max']
    new_high_rate = monthly['new_high'].sum() / len(monthly) * 100
    
    exposure_avg = records_df['exposure'].mean()
    timing_pct = records_df['timing'].mean() * 100
    
    # 创业板指对比
    idx_start = records_df['date'].iloc[0]
    idx_f = index_df[index_df['date'] >= idx_start].copy()
    idx_nav = idx_f['close'].values
    idx_nav = idx_nav / idx_nav[0]
    idx_ann = (idx_nav[-1]/idx_nav[0]) ** (1/years) - 1
    idx_peak = np.maximum.accumulate(idx_nav)
    idx_dd = np.min((idx_nav - idx_peak) / idx_peak)
    
    print(f"\n📅 回测: {records_df['date'].iloc[0].date()} ~ {records_df['date'].iloc[-1].date()} ({years:.1f}年)")
    print(f"\n{'指标':<20} {'Phoenix v2':<18} {'创业板指':<15}")
    print("-" * 55)
    print(f"{'年化收益':<20} {ann_return*100:>7.1f}%          {idx_ann*100:>7.1f}%")
    print(f"{'累计收益':<20} {total_return*100:>7.1f}%          {(idx_nav[-1]/idx_nav[0]-1)*100:>7.1f}%")
    print(f"{'年化波动':<20} {ann_vol*100:>7.1f}%")
    print(f"{'夏普':<20} {sharpe:>7.2f}")
    print(f"{'最大回撤':<20} {max_dd*100:>7.1f}%          {idx_dd*100:>7.1f}%")
    print(f"{'Calmar':<20} {calmar:>7.2f}")
    
    print(f"\n📊 月度:")
    print(f"  胜率: {win_rate:.1f}% ({(monthly['ret']>0).sum()}/{len(monthly)})")
    print(f"  创新高: {new_high_rate:.1f}% ({monthly['new_high'].sum()}/{len(monthly)})")
    print(f"  最佳月: {monthly['ret'].max()*100:.1f}%  最差月: {monthly['ret'].min()*100:.1f}%")
    print(f"  月均: {monthly['ret'].mean()*100:.2f}%")
    print(f"\n📊 持仓: 平均仓位{exposure_avg*100:.1f}%  择时在场{timing_pct:.1f}%")
    
    print(f"\n{'月份':<10} {'收益':>8} {'新高':>6}")
    print("-" * 26)
    for _, r in monthly.iterrows():
        print(f"{str(r['month']):<10} {r['ret']*100:>6.2f}% {'✅' if r['new_high'] else '  ':>6}")
    
    print(f"\n{'='*60}")
    print(f"🎯 目标评估:")
    targets = [
        ("年化>30%", ann_return > 0.30, f"{ann_return*100:.1f}%"),
        ("回撤<10%", max_dd > -0.10, f"{max_dd*100:.1f}%"),
        ("月胜率>75%", win_rate > 75, f"{win_rate:.1f}%"),
        ("创新高>70%", new_high_rate > 70, f"{new_high_rate:.1f}%"),
        ("夏普>2.0", sharpe > 2.0, f"{sharpe:.2f}"),
        ("Calmar>3.0", calmar > 3.0, f"{calmar:.2f}"),
    ]
    for name, passed, value in targets:
        print(f"  {'✅' if passed else '❌'} {name}: {value}")
    
    return {'ann_return': ann_return, 'max_dd': max_dd, 'sharpe': sharpe,
            'calmar': calmar, 'win_rate': win_rate, 'new_high_rate': new_high_rate}

def main():
    print("="*60)
    print("🔥 Phoenix v2 — 激进Alpha + 精准风控")
    print("="*60)
    
    if os.path.exists(DATA_CACHE):
        cache = pd.read_pickle(DATA_CACHE)
        index_df, stock_data = cache['index'], cache['stocks']
        print(f"  缓存: 指数{len(index_df)}天, 个股{len(stock_data)}只")
    else:
        print("\n[1] 拉取创业板指...")
        index_df = fetch_index_data()
        print(f"  {len(index_df)} 天")
        print("\n[2] 获取股票列表...")
        codes = fetch_stock_universe()
        print(f"  {len(codes)} 只")
        print("\n[3] 拉取个股数据...")
        stock_data = fetch_stock_data(codes, max_stocks=60)
        print(f"  成功 {len(stock_data)} 只")
        pd.to_pickle({'index': index_df, 'stocks': stock_data}, DATA_CACHE)
    
    print("\n[4] 计算因子...")
    factor_df = compute_factors(stock_data)
    print(f"  {len(factor_df)} 行, {factor_df['code'].nunique()} 只")
    
    print("\n[5] 回测...")
    records = run_backtest(index_df, factor_df, stock_data)
    
    if len(records) == 0:
        print("❌ 无回测数据")
        return
    
    results = analyze_results(records, index_df)
    
    with open('/opt/quant/phoenix_v2_result.json', 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    records.to_csv('/opt/quant/phoenix_v2_daily.csv', index=False)
    print(f"\n结果: /opt/quant/phoenix_v2_result.json")

if __name__ == '__main__':
    main()
