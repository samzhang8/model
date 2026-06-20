#!/usr/bin/python3
"""
Phoenix v14 — 降低交易频率
v13证明T+1下每天检查MA信号导致频繁whipsaw。
本版测试：
  1. 只在调仓日(每21天)检查投资信号 → 大幅减少交易
  2. 信号确认期(信号持续N天才执行)
  3. 不同调仓频率(14天/21天/42天/63天)
全部T+1+成本+流动性过滤
"""
import akshare as ak
import pandas as pd
import numpy as np
import json

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
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['avg_amount_20d'] = df['amount'].rolling(20).mean() if 'amount' in df.columns else 0
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close', 'ret_1m', 'avg_amount_20d']].copy())
    return pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m'])

def run_backtest(index_df, factor_df, stock_data,
                 ma_fast=20, rebal_freq=21, signal_mode='daily',
                 confirm_days=0):
    """
    signal_mode:
      'daily'    = 每天检查MA信号
      'rebal'    = 只在调仓日检查MA信号
      'confirm'  = 信号持续confirm_days天才执行
    """
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['ma_fast'] = idx['close'].rolling(ma_fast).mean()
    idx['big_trend'] = (idx['close'] > idx['ma250']).astype(int)
    idx['short_trend'] = (idx['close'] > idx['ma_fast']).astype(int)
    idx['raw_signal'] = idx['big_trend'] * idx['short_trend']

    # 信号确认
    if confirm_days > 0:
        idx['confirm_signal'] = idx['raw_signal'].rolling(confirm_days).min()
        idx['invest_raw'] = idx['confirm_signal']
    else:
        idx['invest_raw'] = idx['raw_signal']

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
    held_invest = 0  # 当前持仓状态（用于rebal模式）

    for i, date in enumerate(dates):
        if i < start_i: continue

        # T+1: 用昨天的信号
        raw_signal = idx.iloc[i-1]['invest_raw'] if i > 0 else 0

        if signal_mode == 'rebal':
            # 只在调仓日更新投资状态
            signal_date = dates[i-1] if i > 0 else date
            if signal_date in rebalance_dates:
                held_invest = raw_signal
            invest = held_invest
        else:
            invest = raw_signal

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
                portfolio = {}; cash_weight = 1.0; target_stocks = []; held_invest = 0
                n_stop += 1
            last_rebal_nav = nav

        # 组合执行
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
        'cost': total_cost * 100, 'trades': n_trades,
        'exposure': df['exposure'].mean() * 100
    }

def main():
    print("=" * 70)
    print("🔥 Phoenix v14 — 降低交易频率扫描")
    print("=" * 70)

    cache = pd.read_pickle(DATA_CACHE)
    index_df, stocks = cache['index'], cache['stocks']
    factor_df = compute_factors(stocks)
    print(f"股票: {len(stocks)}只\n")

    configs = [
        # (label, ma_fast, rebal_freq, signal_mode, confirm_days)
        ("v10原版: 日检MA5",          5,  21, 'daily', 0),
        ("日检MA20",                  20, 21, 'daily', 0),
        ("调仓日检MA20(21天)",        20, 21, 'rebal', 0),
        ("调仓日检MA20(42天)",        20, 42, 'rebal', 0),
        ("调仓日检MA20(63天)",        20, 63, 'rebal', 0),
        ("调仓日检MA5(21天)",         5,  21, 'rebal', 0),
        ("日检MA20+3日确认",          20, 21, 'daily', 3),
        ("日检MA20+5日确认",          20, 21, 'daily', 5),
        ("调仓日检MA10(21天)",        10, 21, 'rebal', 0),
        ("调仓日检MA30(21天)",        30, 21, 'rebal', 0),
        ("调仓日检MA20+止损5%(42天)", 20, 42, 'rebal', 0),
    ]

    print(f"{'配置':<30} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6} {'创新高':>6} {'交易':>5} {'成本':>6} {'仓位':>5}")
    print("-" * 100)

    all_results = {}
    for label, ma_f, rf, sm, cd in configs:
        r = run_backtest(index_df, factor_df, stocks,
                         ma_fast=ma_f, rebal_freq=rf, signal_mode=sm, confirm_days=cd)
        all_results[label] = r
        print(f"{label:<30} {r['ann']*100:>6.1f}% {r['max_dd']*100:>6.1f}% {r['sharpe']:>5.2f} "
              f"{r['calmar']:>6.2f} {r['win']:>5.1f}% {r['new_high']:>5.1f}% "
              f"{r['trades']:>5} {r['cost']:>5.1f}% {r['exposure']:>4.1f}%")

    # 目标达成
    print(f"\n🎯 目标达成 (年化>30% & 回撤<10% & 月胜率>75%):")
    for label, r in all_results.items():
        pass_all = r['ann'] > 0.30 and r['max_dd'] > -0.10 and r['win'] > 75
        checks = f"年化{'✅' if r['ann']>0.30 else '❌'} 回撤{'✅' if r['max_dd']>-0.10 else '❌'} 月胜{'✅' if r['win']>75 else '❌'}"
        marker = " ★" if pass_all else ""
        print(f"  {checks}  {label}{marker}")

    with open('/opt/quant/phoenix_v14_result.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n结果: /opt/quant/phoenix_v14_result.json")

if __name__ == '__main__':
    main()
