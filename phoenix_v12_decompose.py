#!/usr/bin/python3
"""
Phoenix v12 — 真实成本版（修正版）+ 因素拆解
修复v11的双重计费bug，并拆解各因素对收益的影响：
  A: v10理想版（基准）
  B: +交易成本
  C: +T+1执行
  D: +流动性过滤
  E: +退市股（完整真实版）
"""

import akshare as ak
import pandas as pd
import numpy as np
import json, os

# ===== 策略参数 =====
REBALANCE_FREQ = 21
TOP_N = 10
MA_SLOW = 250
MA_FAST = 5
STOP_LOSS = 0.05
CASH_RETURN = 0.02 / 252

# 交易成本
COST_BUY = 0.00126    # 佣金0.025%+过户0.001%+滑点0.1%
COST_SELL = 0.00176   # 佣金0.025%+印花税0.05%+过户0.001%+滑点0.1%

# 流动性
MIN_AMOUNT_20D = 50_000_000

# 退市惩罚
DELIST_RECOVERY = 0.50

DATA_CACHE = "/tmp/phoenix_alla_data.pkl"

def compute_factors(stock_data):
    all_factors = []
    last_dates = {}
    for code, df in stock_data.items():
        if len(df) < 252:
            continue
        df = df.copy()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['avg_amount_20d'] = df['amount'].rolling(20).mean() if 'amount' in df.columns else 0
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close', 'ret_1m', 'avg_amount_20d']].copy())
        last_dates[code] = df['date'].iloc[-1]
    factor_df = pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m'])
    return factor_df, last_dates

def run_backtest(index_df, factor_df, stock_data, last_dates,
                 use_cost=False, t_plus_1=False, use_liq_filter=False, use_delist=False):
    """
    统一回测引擎，通过参数控制各真实因素
    成本只在portfolio实际变化时扣一次（修复v11双重计费）
    """
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['ma5'] = idx['close'].rolling(MA_FAST).mean()
    idx['invest'] = ((idx['close'] > idx['ma250']) & (idx['close'] > idx['ma5'])).astype(int)

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
    n_delist = 0
    total_cost = 0.0
    n_trades = 0  # 换手次数

    for i, date in enumerate(dates):
        if i < start_i:
            continue

        # === 1. 退市检测 ===
        if use_delist and portfolio:
            for code in list(portfolio.keys()):
                if code in last_dates and date > last_dates[code]:
                    last_price = price_lookup[code].get(last_dates[code])
                    if last_price is not None:
                        delist_loss = portfolio[code] * (1 - DELIST_RECOVERY)
                        nav *= (1 - delist_loss)
                        total_cost += delist_loss
                    n_delist += 1
                    del portfolio[code]
            if portfolio:
                total_w = sum(portfolio.values())
                if total_w > 0:
                    portfolio = {c: w / total_w for c, w in portfolio.items()}
                else:
                    cash_weight = 1.0
            else:
                cash_weight = 1.0

        # === 2. 调仓：更新target_stocks ===
        # T+1: 用昨天的因子数据；当日: 用今天的
        signal_date = dates[i-1] if (t_plus_1 and i > 0) else date
        if signal_date in rebalance_dates:
            cross = factor_df[factor_df['date'] == signal_date].copy()
            if use_liq_filter:
                cross = cross[cross['avg_amount_20d'] >= MIN_AMOUNT_20D]
            if len(cross) >= TOP_N:
                new_targets = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
            elif len(cross) > 0:
                new_targets = cross.nlargest(len(cross), 'ret_1m')['code'].tolist()
            else:
                new_targets = target_stocks  # 保持不变

            # 止损检查
            month_ret = nav / last_rebal_nav - 1
            if month_ret < -STOP_LOSS and portfolio:
                if use_cost:
                    cost = sum(portfolio.values()) * COST_SELL
                    nav *= (1 - cost)
                    total_cost += cost
                portfolio = {}
                cash_weight = 1.0
                target_stocks = []
                n_stop += 1
            else:
                target_stocks = new_targets
            last_rebal_nav = nav

        # === 3. 投资信号 + 组合执行（成本只在这里扣一次）===
        invest = idx.iloc[i-1]['invest'] if (t_plus_1 and i > 0) else idx.iloc[i]['invest']

        if invest == 1 and target_stocks:
            new_portfolio = {c: 1.0 / len(target_stocks) for c in target_stocks}
            if not portfolio:
                # 从空仓进入：买入成本
                cost = COST_BUY if use_cost else 0
                n_trades += 1
            elif set(portfolio.keys()) != set(new_portfolio.keys()):
                # 换仓：卖出离开的 + 买入新进的
                old_set = set(portfolio.keys())
                new_set = set(new_portfolio.keys())
                sold_w = sum(portfolio.get(c, 0) for c in (old_set - new_set))
                bought_w = sum(new_portfolio[c] for c in (new_set - old_set))
                cost = (sold_w * COST_SELL + bought_w * COST_BUY) if use_cost else 0
                n_trades += 1
            else:
                cost = 0
            nav *= (1 - cost)
            total_cost += cost
            portfolio = new_portfolio
            cash_weight = 0.0
        else:
            # 空仓
            if portfolio:
                cost = sum(portfolio.values()) * COST_SELL if use_cost else 0
                nav *= (1 - cost)
                total_cost += cost
                n_trades += 1
            portfolio = {}
            cash_weight = 1.0

        # === 4. 日收益 ===
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
            'invest': invest, 'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'n_positions': len(portfolio),
        })

    return pd.DataFrame(daily_records), n_stop, n_delist, total_cost, n_trades

def quick_stats(records_df, n_stop=0, n_delist=0, total_cost=0, n_trades=0):
    """快速统计关键指标"""
    nav = records_df['nav'].values
    years = len(nav) / 252
    ann_return = (nav[-1] / nav[0]) ** (1 / years) - 1
    daily_rets = records_df['daily_ret'].values
    ann_vol = np.std(daily_rets) * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0
    peak = np.maximum.accumulate(nav)
    max_dd = np.min((nav - peak) / peak)
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    records_df['month'] = records_df['date'].dt.to_period('M')
    monthly = records_df.groupby('month').agg(
        ret=('daily_ret', lambda x: np.prod(1 + x) - 1),
        avg_exp=('exposure', 'mean')
    ).reset_index()
    win_rate = (monthly['ret'] > 0).sum() / len(monthly) * 100
    monthly['prev_max'] = monthly['end_nav'] = records_df.groupby('month')['nav'].last().values
    monthly['prev_max'] = monthly['end_nav'].cummax().shift(1).fillna(0)
    new_high = (monthly['end_nav'] > monthly['prev_max']).sum() / len(monthly) * 100

    return {
        'ann_return': ann_return, 'max_dd': max_dd, 'sharpe': sharpe,
        'calmar': calmar, 'win_rate': win_rate, 'new_high': new_high,
        'n_stop': n_stop, 'n_delist': n_delist,
        'total_cost_pct': total_cost * 100, 'n_trades': n_trades
    }

def print_stats(label, s):
    print(f"\n{'='*60}")
    print(f"📊 {label}")
    print(f"{'='*60}")
    print(f"  年化收益:   {s['ann_return']*100:>8.1f}%")
    print(f"  最大回撤:   {s['max_dd']*100:>8.1f}%")
    print(f"  夏普:       {s['sharpe']:>8.2f}")
    print(f"  Calmar:     {s['calmar']:>8.2f}")
    print(f"  月胜率:     {s['win_rate']:>8.1f}%")
    print(f"  创新高:     {s['new_high']:>8.1f}%")
    print(f"  止损次数:   {s['n_stop']:>8}")
    print(f"  退市事件:   {s['n_delist']:>8}")
    print(f"  总成本:     {s['total_cost_pct']:>8.2f}%")
    print(f"  交易次数:   {s['n_trades']:>8}")

def main():
    print("=" * 60)
    print("🔥 Phoenix v12 — 真实成本版 + 因素拆解")
    print("=" * 60)

    # 加载数据
    print("\n[1] 加载数据...")
    cache = pd.read_pickle(DATA_CACHE)
    index_df, stocks_active = cache['index'], cache['stocks']
    print(f"  活跃股: {len(stocks_active)}只")

    # 退市股
    delist_cache = "/tmp/phoenix_delist_data.pkl"
    if os.path.exists(delist_cache):
        stocks_delist = pd.read_pickle(delist_cache)
        print(f"  退市股: {len(stocks_delist)}只 (缓存)")
    else:
        print("  退市股缓存不存在，跳过退市测试")
        stocks_delist = {}

    all_stocks = {}
    all_stocks.update(stocks_active)
    all_stocks.update(stocks_delist)

    # 计算因子
    print("\n[2] 计算因子...")
    factor_all, last_dates_all = compute_factors(all_stocks)
    factor_active, last_dates_active = compute_factors(stocks_active)
    print(f"  全池: {factor_all['code'].nunique()}只")
    print(f"  活跃: {factor_active['code'].nunique()}只")

    # ===== 因素拆解 =====
    print("\n[3] 因素拆解回测...")

    # A: v10理想版（基准）
    rec_A, stop_A, dl_A, cost_A, trades_A = run_backtest(
        index_df, factor_active, stocks_active, {},
        use_cost=False, t_plus_1=False, use_liq_filter=False, use_delist=False)
    s_A = quick_stats(rec_A, stop_A, dl_A, cost_A, trades_A)
    print_stats("A: v10理想版（基准）", s_A)

    # B: +交易成本
    rec_B, stop_B, dl_B, cost_B, trades_B = run_backtest(
        index_df, factor_active, stocks_active, {},
        use_cost=True, t_plus_1=False, use_liq_filter=False, use_delist=False)
    s_B = quick_stats(rec_B, stop_B, dl_B, cost_B, trades_B)
    print_stats("B: +交易成本", s_B)

    # C: +T+1执行（在B基础上）
    rec_C, stop_C, dl_C, cost_C, trades_C = run_backtest(
        index_df, factor_active, stocks_active, {},
        use_cost=True, t_plus_1=True, use_liq_filter=False, use_delist=False)
    s_C = quick_stats(rec_C, stop_C, dl_C, cost_C, trades_C)
    print_stats("C: +T+1执行", s_C)

    # D: +流动性过滤（在C基础上）
    rec_D, stop_D, dl_D, cost_D, trades_D = run_backtest(
        index_df, factor_active, stocks_active, {},
        use_cost=True, t_plus_1=True, use_liq_filter=True, use_delist=False)
    s_D = quick_stats(rec_D, stop_D, dl_D, cost_D, trades_D)
    print_stats("D: +流动性过滤(5000万)", s_D)

    # E: 完整真实版（+退市股）
    rec_E, stop_E, dl_E, cost_E, trades_E = run_backtest(
        index_df, factor_all, all_stocks, last_dates_all,
        use_cost=True, t_plus_1=True, use_liq_filter=True, use_delist=True)
    s_E = quick_stats(rec_E, stop_E, dl_E, cost_E, trades_E)
    print_stats("E: 完整真实版(+退市股)", s_E)

    # ===== 对比汇总 =====
    print(f"\n{'='*70}")
    print(f"📊 因素拆解汇总")
    print(f"{'='*70}")
    print(f"\n{'版本':<25} {'年化':>8} {'回撤':>8} {'夏普':>6} {'月胜率':>8} {'成本':>8} {'交易':>6}")
    print("-" * 73)
    for label, s in [("A: 理想基准", s_A), ("B: +交易成本", s_B),
                      ("C: +T+1执行", s_C), ("D: +流动性过滤", s_D),
                      ("E: 完整真实版", s_E)]:
        print(f"{label:<25} {s['ann_return']*100:>7.1f}% {s['max_dd']*100:>7.1f}% "
              f"{s['sharpe']:>6.2f} {s['win_rate']:>7.1f}% {s['total_cost_pct']:>7.2f}% {s['n_trades']:>6}")

    print(f"\n📉 各因素影响（年化收益损失）:")
    print(f"  交易成本:     {s_A['ann_return']*100:.1f}% → {s_B['ann_return']*100:.1f}% (损失{(s_A['ann_return']-s_B['ann_return'])*100:.1f}%)")
    print(f"  T+1执行:      {s_B['ann_return']*100:.1f}% → {s_C['ann_return']*100:.1f}% (损失{(s_B['ann_return']-s_C['ann_return'])*100:.1f}%)")
    print(f"  流动性过滤:   {s_C['ann_return']*100:.1f}% → {s_D['ann_return']*100:.1f}% (损失{(s_C['ann_return']-s_D['ann_return'])*100:.1f}%)")
    print(f"  退市股:       {s_D['ann_return']*100:.1f}% → {s_E['ann_return']*100:.1f}% (损失{(s_D['ann_return']-s_E['ann_return'])*100:.1f}%)")
    print(f"  ─────────────────────────")
    print(f"  总损失:       {s_A['ann_return']*100:.1f}% → {s_E['ann_return']*100:.1f}% (损失{(s_A['ann_return']-s_E['ann_return'])*100:.1f}%)")

    # 保存
    results = {'A_ideal': s_A, 'B_cost': s_B, 'C_t1': s_C, 'D_liq': s_D, 'E_full': s_E}
    with open('/opt/quant/phoenix_v12_result.json', 'w') as f:
        json.dump(results, f, indent=2, default=str, ensure_ascii=False)
    rec_E.to_csv('/opt/quant/phoenix_v12_daily.csv', index=False)
    print(f"\n结果: /opt/quant/phoenix_v12_result.json")

if __name__ == '__main__':
    main()
