#!/usr/bin/python3
"""
Phoenix v13 — T+1执行下不同短期均线的扫描
MA5在T+1下失效（信号翻转太快），测试MA10/MA20/MA30/无短期均线
全部加入交易成本+T+1+流动性过滤
"""

import akshare as ak
import pandas as pd
import numpy as np
import json, os

REBALANCE_FREQ = 21
TOP_N = 10
MA_SLOW = 250
STOP_LOSS = 0.05
CASH_RETURN = 0.02 / 252
COST_BUY = 0.00126
COST_SELL = 0.00176
MIN_AMOUNT_20D = 50_000_000
DATA_CACHE = "/tmp/phoenix_alla_data.pkl"

def compute_factors(stock_data):
    all_factors = []
    last_dates = {}
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['avg_amount_20d'] = df['amount'].rolling(20).mean() if 'amount' in df.columns else 0
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close', 'ret_1m', 'avg_amount_20d']].copy())
        last_dates[code] = df['date'].iloc[-1]
    return pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m']), last_dates

def run_backtest(index_df, factor_df, stock_data, ma_fast=5, use_fast=True):
    """T+1执行 + 交易成本 + 流动性过滤"""
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['big_trend'] = (idx['close'] > idx['ma250']).astype(int)
    if use_fast:
        idx['ma_fast'] = idx['close'].rolling(ma_fast).mean()
        idx['short_trend'] = (idx['close'] > idx['ma_fast']).astype(int)
        idx['invest'] = idx['big_trend'] * idx['short_trend']
    else:
        idx['invest'] = idx['big_trend']  # 只用MA250

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
    last_rebal_nav = 1.0
    n_stop = 0
    total_cost = 0.0
    n_trades = 0
    n_signal_flips = 0
    prev_invest_state = 0

    for i, date in enumerate(dates):
        if i < start_i: continue

        # T+1: 用昨天的信号
        invest = idx.iloc[i-1]['invest'] if i > 0 else 0

        # 统计信号翻转
        if invest != prev_invest_state:
            n_signal_flips += 1
            prev_invest_state = invest

        # 调仓：用昨天的因子
        signal_date = dates[i-1] if i > 0 else date
        if signal_date in rebalance_dates:
            cross = factor_df[factor_df['date'] == signal_date].copy()
            cross = cross[cross['avg_amount_20d'] >= MIN_AMOUNT_20D]
            if len(cross) >= TOP_N:
                target_stocks = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
            elif len(cross) > 0:
                target_stocks = cross.nlargest(len(cross), 'ret_1m')['code'].tolist()
            month_ret = nav / last_rebal_nav - 1
            if month_ret < -STOP_LOSS and portfolio:
                cost = sum(portfolio.values()) * COST_SELL
                nav *= (1 - cost); total_cost += cost
                portfolio = {}; cash_weight = 1.0; target_stocks = []
                n_stop += 1
            last_rebal_nav = nav

        # 组合执行（成本只扣一次）
        if invest == 1 and target_stocks:
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

        # 日收益
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

    return {
        'ann': ann, 'max_dd': max_dd, 'sharpe': sharpe, 'calmar': calmar,
        'win': win, 'new_high': new_high, 'n_stop': n_stop,
        'cost': total_cost * 100, 'trades': n_trades, 'flips': n_signal_flips,
        'exposure': df['exposure'].mean() * 100
    }

def main():
    print("=" * 70)
    print("🔥 Phoenix v13 — T+1下短期均线扫描")
    print("=" * 70)

    cache = pd.read_pickle(DATA_CACHE)
    index_df, stocks = cache['index'], cache['stocks']
    factor_df, last_dates = compute_factors(stocks)
    print(f"股票: {len(stocks)}只, 因子: {factor_df['code'].nunique()}只\n")

    configs = [
        ("MA250+MA5 (v10原版)", 5, True),
        ("MA250+MA10", 10, True),
        ("MA250+MA20", 20, True),
        ("MA250+MA30", 30, True),
        ("MA250+MA60", 60, True),
        ("MA250 only (无短期)", 0, False),
    ]

    print(f"{'配置':<25} {'年化':>8} {'回撤':>8} {'夏普':>6} {'Calmar':>7} {'月胜率':>7} {'创新高':>7} {'信号翻转':>8} {'交易':>6} {'成本':>7} {'仓位':>6}")
    print("-" * 105)

    all_results = {}
    for label, ma_f, use_f in configs:
        r = run_backtest(index_df, factor_df, stocks, ma_fast=ma_f, use_fast=use_f)
        all_results[label] = r
        print(f"{label:<25} {r['ann']*100:>7.1f}% {r['max_dd']*100:>7.1f}% {r['sharpe']:>6.2f} "
              f"{r['calmar']:>7.2f} {r['win']:>6.1f}% {r['new_high']:>6.1f}% "
              f"{r['flips']:>8} {r['trades']:>6} {r['cost']:>6.1f}% {r['exposure']:>5.1f}%")

    # 目标达成检查
    print(f"\n🎯 目标达成检查 (年化>30%, 回撤<10%, 月胜率>75%):")
    for label, r in all_results.items():
        checks = [
            ("年化>30%", r['ann'] > 0.30),
            ("回撤<10%", r['max_dd'] > -0.10),
            ("月胜率>75%", r['win'] > 75),
        ]
        status = " ".join("✅" if p else "❌" for _, p in checks)
        print(f"  {status}  {label}: 年化{r['ann']*100:.1f}% 回撤{r['max_dd']*100:.1f}% 月胜率{r['win']:.1f}%")

    with open('/opt/quant/phoenix_v13_result.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n结果: /opt/quant/phoenix_v13_result.json")

if __name__ == '__main__':
    main()
