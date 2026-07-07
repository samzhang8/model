#!/usr/bin/python3
"""
Phoenix v15 — 多因子合成版
放弃均线择时，靠多因子选股alpha控回撤。

因子（全部从价量数据计算）:
  1. mom_1m:   1月动量 (21日收益)
  2. mom_3m:   3月动量 (63日收益)
  3. rev_1w:   1周反转 (5日收益, 取负 = 买跌卖涨)
  4. low_vol:  60日低波 (取负 = 低波得分高)
  5. low_beta: 60日低beta (取负 = 防御型)
  6. stability: 60日最大回撤 (取负 = 稳定得分高)
  7. amplitude: 20日振幅 (取负 = 低振幅得分高)

合成方法: 横截面z-score等权合成
回测: T+1 + 真实成本 + 流动性过滤
测试: 始终满仓 vs MA250过滤; TOP 10/20/30
"""
import akshare as ak
import pandas as pd
import numpy as np
import json, os, time

REBALANCE_FREQ = 21
MA_SLOW = 250
STOP_LOSS = 0.08  # 组合级止损8%（比5%宽松，减少误杀）
CASH_RETURN = 0.02 / 252
COST_BUY = 0.00126
COST_SELL = 0.00176
MIN_AMOUNT_20D = 50_000_000
DATA_CACHE = "/tmp/phoenix_alla_data.pkl"

# ===================================================================
# 因子计算
# ===================================================================

def compute_all_factors(stock_data, index_df):
    """计算全部7个因子"""
    all_factors = []
    index_ret = index_df['close'].pct_change()
    
    for code, df in stock_data.items():
        if len(df) < 252:
            continue
        df = df.copy()
        c = df['close']
        
        # 动量因子
        df['mom_1m'] = c / c.shift(21) - 1
        df['mom_3m'] = c / c.shift(63) - 1
        
        # 短期反转（5日收益取负）
        df['rev_1w'] = -(c / c.shift(5) - 1)
        
        # 低波动（60日收益率标准差取负）
        ret = c.pct_change()
        df['low_vol'] = -ret.rolling(60).std()
        
        # 低beta（60日beta vs 指数取负）
        cov = ret.rolling(60).cov(index_ret)
        var = index_ret.rolling(60).var()
        df['low_beta'] = -(cov / var)
        
        # 价格稳定性（60日最大回撤取负）
        roll_max = c.rolling(60).max()
        df['stability'] = -((c - roll_max) / roll_max)
        
        # 低振幅（20日平均振幅取负）
        df['amplitude'] = -((df['high'] - df['low']) / c).rolling(20).mean()
        
        # 流动性
        df['avg_amount_20d'] = df['amount'].rolling(20).mean()
        
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close',
                               'mom_1m', 'mom_3m', 'rev_1w',
                               'low_vol', 'low_beta', 'stability', 'amplitude',
                               'avg_amount_20d']].copy())
    
    factor_df = pd.concat(all_factors, ignore_index=True)
    # 去掉NaN
    factor_df = factor_df.dropna(subset=['mom_1m', 'mom_3m', 'rev_1w',
                                          'low_vol', 'low_beta', 'stability', 'amplitude'])
    return factor_df

def zscore_composite(factor_cross, factor_names, weights=None):
    """
    横截面z-score合成
    factor_cross: 某一天的横截面数据
    factor_names: 使用的因子列表
    weights: 因子权重，默认等权
    """
    if weights is None:
        weights = {f: 1.0 for f in factor_names}
    
    composite = pd.Series(0.0, index=factor_cross.index)
    total_w = sum(weights.values())
    
    for f in factor_names:
        vals = factor_cross[f]
        # 截面z-score
        mu = vals.mean()
        sigma = vals.std()
        if sigma > 0:
            z = (vals - mu) / sigma
        else:
            z = pd.Series(0.0, index=factor_cross.index)
        composite += weights[f] * z
    
    return composite / total_w

# ===================================================================
# 回测引擎
# ===================================================================

def run_backtest(index_df, factor_df, stock_data,
                 factor_names, weights=None,
                 top_n=20, use_ma250=False, use_stoploss=True,
                 rebal_freq=21):
    """
    多因子回测
    use_ma250: 是否用MA250做简单 regime filter
    use_stoploss: 是否用组合级止损
    """
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['regime'] = (idx['close'] > idx['ma250']).astype(int)
    
    dates = idx['date'].tolist()
    start_i = MA_SLOW
    rebalance_dates = set(dates[i] for i in range(start_i, len(dates), rebal_freq))
    price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}
    
    portfolio = {}
    target_stocks = []
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_rebal_nav = 1.0
    n_stop = 0
    total_cost = 0.0
    n_trades = 0
    in_stopout = False  # 止损后冷却期
    stopout_until = None
    
    for i, date in enumerate(dates):
        if i < start_i:
            continue
        
        # T+1: 用昨天的数据
        signal_date = dates[i-1] if i > 0 else date
        regime = idx.iloc[i-1]['regime'] if i > 0 else 0
        
        # === 调仓 ===
        if signal_date in rebalance_dates:
            # 止损检查
            month_ret = nav / last_rebal_nav - 1
            if use_stoploss and month_ret < -STOP_LOSS and portfolio:
                cost = sum(portfolio.values()) * COST_SELL
                nav *= (1 - cost); total_cost += cost
                portfolio = {}; cash_weight = 1.0; target_stocks = []
                n_stop += 1
                in_stopout = True
                stopout_until = dates[min(i + rebal_freq, len(dates)-1)]  # 冷却一个调仓周期
            else:
                # 选股：横截面因子合成
                cross = factor_df[factor_df['date'] == signal_date].copy()
                cross = cross[cross['avg_amount_20d'] >= MIN_AMOUNT_20D]
                if len(cross) >= top_n:
                    cross['score'] = zscore_composite(cross, factor_names, weights)
                    target_stocks = cross.nlargest(top_n, 'score')['code'].tolist()
                elif len(cross) > 0:
                    cross['score'] = zscore_composite(cross, factor_names, weights)
                    target_stocks = cross.nlargest(len(cross), 'score')['code'].tolist()
            last_rebal_nav = nav
        
        # 止损冷却期解除
        if in_stopout and date >= stopout_until:
            in_stopout = False
        
        # === 组合执行 ===
        should_invest = (not in_stopout) and len(target_stocks) > 0
        if use_ma250:
            should_invest = should_invest and (regime == 1)
        
        if should_invest:
            new_portfolio = {c: 1.0 / len(target_stocks) for c in target_stocks}
            if not portfolio:
                cost = COST_BUY; n_trades += 1
            elif set(portfolio.keys()) != set(new_portfolio.keys()):
                old_set, new_set = set(portfolio.keys()), set(new_portfolio.keys())
                sold_w = sum(portfolio.get(c, 0) for c in (old_set - new_set))
                bought_w = sum(new_portfolio[c] for c in (new_set - old_set))
                cost = sold_w * COST_SELL + bought_w * COST_BUY; n_trades += 1
            else:
                cost = 0
            nav *= (1 - cost); total_cost += cost
            portfolio = new_portfolio; cash_weight = 0.0
        else:
            if portfolio:
                cost = sum(portfolio.values()) * COST_SELL
                nav *= (1 - cost); total_cost += cost; n_trades += 1
            portfolio = {}; cash_weight = 1.0
        
        # === 日收益 ===
        daily_ret = 0.0
        for code, weight in portfolio.items():
            if code in price_lookup:
                tp = price_lookup[code].get(date)
                yp = price_lookup[code].get(dates[i-1]) if i > 0 else None
                if tp is not None and yp is not None and yp > 0:
                    daily_ret += weight * (tp / yp - 1)
        daily_ret += cash_weight * CASH_RETURN
        nav *= (1 + daily_ret)
        peak_nav = max(peak_nav, nav)
        daily_records.append({
            'date': date, 'nav': nav, 'daily_ret': daily_ret,
            'exposure': 1 - cash_weight, 'dd': (nav - peak_nav) / peak_nav
        })
    
    df = pd.DataFrame(daily_records)
    nav = df['nav'].values
    years = len(nav) / 252
    ann = (nav[-1] / nav[0]) ** (1 / years) - 1
    vol = np.std(df['daily_ret'].values) * np.sqrt(252)
    sharpe = ann / vol if vol > 0 else 0
    peak = np.maximum.accumulate(nav)
    max_dd = np.min((nav - peak) / peak)
    calmar = ann / abs(max_dd) if max_dd != 0 else 0
    df['month'] = df['date'].dt.to_period('M')
    monthly = df.groupby('month')['daily_ret'].apply(lambda x: np.prod(1 + x) - 1)
    win = (monthly > 0).sum() / len(monthly) * 100
    monthly_nav = df.groupby('month')['nav'].last()
    new_high = (monthly_nav > monthly_nav.cummax().shift(1).fillna(0)).sum() / len(monthly) * 100
    real_inv = df[df['exposure'] > 0.1]
    real_monthly = real_inv.groupby('month')['daily_ret'].apply(lambda x: np.prod(1 + x) - 1)
    real_win = (real_monthly > 0).sum() / len(real_monthly) * 100 if len(real_monthly) > 0 else 0
    
    return {
        'ann': ann, 'max_dd': max_dd, 'sharpe': sharpe, 'calmar': calmar,
        'win': win, 'real_win': real_win, 'new_high': new_high,
        'n_stop': n_stop, 'cost': total_cost * 100, 'trades': n_trades,
        'exposure': df['exposure'].mean() * 100
    }

# ===================================================================
# 主流程
# ===================================================================

def main():
    print("=" * 70)
    print("🔥 Phoenix v15 — 多因子合成版")
    print("=" * 70)
    
    cache = pd.read_pickle(DATA_CACHE)
    index_df, stocks = cache['index'], cache['stocks']
    print(f"\n股票: {len(stocks)}只")
    
    print("计算7个因子...")
    factor_df = compute_all_factors(stocks, index_df)
    print(f"因子数据: {len(factor_df)}行, {factor_df['code'].nunique()}只")
    print(f"日期范围: {factor_df['date'].min().date()} ~ {factor_df['date'].max().date()}")
    
    # 因子列表
    ALL_FACTORS = ['mom_1m', 'mom_3m', 'rev_1w', 'low_vol', 'low_beta', 'stability', 'amplitude']
    
    # ===== 测试1: 单因子表现 =====
    print(f"\n{'='*70}")
    print(f"📊 测试1: 单因子表现 (TOP20, 满仓, T+1+成本)")
    print(f"{'='*70}")
    print(f"\n{'因子':<15} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6} {'交易':>5} {'成本':>6}")
    print("-" * 60)
    
    for f in ALL_FACTORS:
        r = run_backtest(index_df, factor_df, stocks, [f], top_n=20,
                        use_ma250=False, use_stoploss=False)
        print(f"{f:<15} {r['ann']*100:>6.1f}% {r['max_dd']*100:>6.1f}% {r['sharpe']:>5.2f} "
              f"{r['calmar']:>6.2f} {r['win']:>5.1f}% {r['trades']:>5} {r['cost']:>5.1f}%")
    
    # ===== 测试2: 因子组合 =====
    print(f"\n{'='*70}")
    print(f"📊 测试2: 因子组合 (TOP20, 满仓)")
    print(f"{'='*70}")
    
    combos = [
        ("动量+低波",       ['mom_1m', 'low_vol']),
        ("动量+反转+低波",  ['mom_1m', 'rev_1w', 'low_vol']),
        ("动量+低波+低beta", ['mom_1m', 'low_vol', 'low_beta']),
        ("动量+低波+稳定",  ['mom_1m', 'low_vol', 'stability']),
        ("动量+反转+低波+低beta", ['mom_1m', 'rev_1w', 'low_vol', 'low_beta']),
        ("动量+反转+低波+稳定+低beta+振幅", ALL_FACTORS),
        ("3月动量+低波+反转", ['mom_3m', 'low_vol', 'rev_1w']),
        ("纯防御(低波+低beta+稳定+振幅)", ['low_vol', 'low_beta', 'stability', 'amplitude']),
    ]
    
    print(f"\n{'组合':<35} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6} {'交易':>5} {'成本':>6}")
    print("-" * 85)
    for label, factors in combos:
        r = run_backtest(index_df, factor_df, stocks, factors, top_n=20,
                        use_ma250=False, use_stoploss=False)
        print(f"{label:<35} {r['ann']*100:>6.1f}% {r['max_dd']*100:>6.1f}% {r['sharpe']:>5.2f} "
              f"{r['calmar']:>6.2f} {r['win']:>5.1f}% {r['trades']:>5} {r['cost']:>5.1f}%")
    
    # ===== 测试3: 最优组合 + 不同TOP_N =====
    print(f"\n{'='*70}")
    print(f"📊 测试3: 最优组合 + TOP_N扫描 + MA250/止损")
    print(f"{'='*70}")
    
    # 用动量+反转+低波+低beta
    best_factors = ['mom_1m', 'rev_1w', 'low_vol', 'low_beta']
    
    configs = [
        # (label, top_n, use_ma250, use_stoploss)
        ("TOP10 满仓",            10, False, False),
        ("TOP20 满仓",            20, False, False),
        ("TOP30 满仓",            30, False, False),
        ("TOP20 满仓+止损8%",     20, False, True),
        ("TOP20 MA250+止损8%",    20, True,  True),
        ("TOP30 MA250+止损8%",    30, True,  True),
        ("TOP10 MA250+止损8%",    10, True,  True),
        ("TOP30 满仓+止损8%",     30, False, True),
        ("TOP20 满仓+止损5%",     20, False, True),
    ]
    
    # 先修正止损参数
    global STOP_LOSS
    
    print(f"\n{'配置':<25} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6} {'实战胜率':>7} {'创新高':>6} {'止损':>4} {'交易':>5} {'成本':>6} {'仓位':>5}")
    print("-" * 110)
    
    all_results = {}
    for label, tn, ma, sl in configs:
        if '5%' in label:
            STOP_LOSS = 0.05
        else:
            STOP_LOSS = 0.08
        r = run_backtest(index_df, factor_df, stocks, best_factors, top_n=tn,
                        use_ma250=ma, use_stoploss=sl)
        all_results[label] = r
        print(f"{label:<25} {r['ann']*100:>6.1f}% {r['max_dd']*100:>6.1f}% {r['sharpe']:>5.2f} "
              f"{r['calmar']:>6.2f} {r['win']:>5.1f}% {r['real_win']:>6.1f}% {r['new_high']:>5.1f}% "
              f"{r['n_stop']:>4} {r['trades']:>5} {r['cost']:>5.1f}% {r['exposure']:>4.1f}%")
    
    # ===== 目标检查 =====
    print(f"\n🎯 目标达成 (年化>30% & 回撤<10% & 月胜率>75%):")
    for label, r in all_results.items():
        checks = f"年化{'✅' if r['ann']>0.30 else '❌'} 回撤{'✅' if r['max_dd']>-0.10 else '❌'} 月胜{'✅' if r['win']>75 else '❌'}"
        pass_all = r['ann']>0.30 and r['max_dd']>-0.10 and r['win']>75
        marker = " ★★★ 达标!" if pass_all else ""
        print(f"  {checks}  {label}{marker}")
    
    with open('/opt/quant/phoenix_v15_result.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n结果: /opt/quant/phoenix_v15_result.json")

if __name__ == '__main__':
    main()
