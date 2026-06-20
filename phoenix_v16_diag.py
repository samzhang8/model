#!/usr/bin/python3
"""
Phoenix v16 — 因子alpha诊断 + 低波因子深挖
1. T+0 vs T+1 对比：区分因子alpha和执行损耗
2. 低波因子是最强单因子(7.2%/-17.2%)，深挖不同配置
3. 扩大TOP_N到50/100看分散效果
"""
import akshare as ak
import pandas as pd
import numpy as np
import json

REBALANCE_FREQ = 21
MA_SLOW = 250
CASH_RETURN = 0.02 / 252
COST_BUY = 0.00126
COST_SELL = 0.00176
MIN_AMOUNT_20D = 50_000_000
DATA_CACHE = "/tmp/phoenix_alla_data.pkl"

def compute_all_factors(stock_data, index_df):
    all_factors = []
    index_ret = index_df['close'].pct_change()
    for code, df in stock_data.items():
        if len(df) < 252: continue
        df = df.copy()
        c = df['close']
        ret = c.pct_change()
        df['mom_1m'] = c / c.shift(21) - 1
        df['rev_1w'] = -(c / c.shift(5) - 1)
        df['low_vol'] = -ret.rolling(60).std()
        cov = ret.rolling(60).cov(index_ret)
        var = index_ret.rolling(60).var()
        df['low_beta'] = -(cov / var)
        roll_max = c.rolling(60).max()
        df['stability'] = -((c - roll_max) / roll_max)
        df['amplitude'] = -((df['high'] - df['low']) / c).rolling(20).mean()
        df['avg_amount_20d'] = df['amount'].rolling(20).mean()
        df['code'] = code
        all_factors.append(df[['date','code','close','mom_1m','rev_1w','low_vol',
                               'low_beta','stability','amplitude','avg_amount_20d']].copy())
    return pd.concat(all_factors, ignore_index=True).dropna(
        subset=['mom_1m','rev_1w','low_vol','low_beta','stability','amplitude'])

def zscore_cross(vals):
    mu, sigma = vals.mean(), vals.std()
    return (vals - mu) / sigma if sigma > 0 else pd.Series(0.0, index=vals.index)

def run_backtest(index_df, factor_df, stock_data,
                 factor_names, top_n=30, use_ma250=True, use_stoploss=True,
                 stop_loss=0.08, t_plus_1=True, use_cost=True, rebal_freq=21):
    idx = index_df.copy()
    idx['ma250'] = idx['close'].rolling(MA_SLOW).mean()
    idx['regime'] = (idx['close'] > idx['ma250']).astype(int)
    dates = idx['date'].tolist()
    start_i = MA_SLOW
    rebalance_dates = set(dates[i] for i in range(start_i, len(dates), rebal_freq))
    price_lookup = {code: df.set_index('date')['close'] for code, df in stock_data.items()}
    
    portfolio = {}; target_stocks = []; cash_weight = 1.0
    nav = 1.0; peak_nav = 1.0; daily_records = []
    last_rebal_nav = 1.0; n_stop = 0; total_cost = 0.0; n_trades = 0
    in_stopout = False; stopout_until = None
    
    for i, date in enumerate(dates):
        if i < start_i: continue
        
        # T+1或T+0
        signal_date = dates[i-1] if (t_plus_1 and i > 0) else date
        regime = idx.iloc[i-1]['regime'] if t_plus_1 else idx.iloc[i]['regime']
        
        if signal_date in rebalance_dates:
            month_ret = nav / last_rebal_nav - 1
            if use_stoploss and month_ret < -stop_loss and portfolio:
                cost = sum(portfolio.values()) * COST_SELL if use_cost else 0
                nav *= (1 - cost); total_cost += cost
                portfolio = {}; cash_weight = 1.0; target_stocks = []
                n_stop += 1; in_stopout = True
                stopout_until = dates[min(i + rebal_freq, len(dates)-1)]
            else:
                cross = factor_df[factor_df['date'] == signal_date].copy()
                cross = cross[cross['avg_amount_20d'] >= MIN_AMOUNT_20D]
                if len(cross) >= top_n:
                    composite = pd.Series(0.0, index=cross.index)
                    for f in factor_names:
                        composite += zscore_cross(cross[f])
                    cross['score'] = composite / len(factor_names)
                    target_stocks = cross.nlargest(top_n, 'score')['code'].tolist()
                elif len(cross) > 0:
                    composite = pd.Series(0.0, index=cross.index)
                    for f in factor_names:
                        composite += zscore_cross(cross[f])
                    cross['score'] = composite / len(factor_names)
                    target_stocks = cross.nlargest(len(cross), 'score')['code'].tolist()
            last_rebal_nav = nav
        
        if in_stopout and date >= stopout_until:
            in_stopout = False
        
        should_invest = (not in_stopout) and len(target_stocks) > 0
        if use_ma250:
            should_invest = should_invest and (regime == 1)
        
        if should_invest:
            new_portfolio = {c: 1.0/len(target_stocks) for c in target_stocks}
            if not portfolio:
                cost = COST_BUY if use_cost else 0; n_trades += 1
            elif set(portfolio.keys()) != set(new_portfolio.keys()):
                old_s, new_s = set(portfolio.keys()), set(new_portfolio.keys())
                sold_w = sum(portfolio.get(c,0) for c in (old_s-new_s))
                bought_w = sum(new_portfolio[c] for c in (new_s-old_s))
                cost = (sold_w*COST_SELL + bought_w*COST_BUY) if use_cost else 0; n_trades += 1
            else:
                cost = 0
            nav *= (1-cost); total_cost += cost
            portfolio = new_portfolio; cash_weight = 0.0
        else:
            if portfolio:
                cost = sum(portfolio.values()) * COST_SELL if use_cost else 0
                nav *= (1-cost); total_cost += cost; n_trades += 1
            portfolio = {}; cash_weight = 1.0
        
        daily_ret = 0.0
        for code, weight in portfolio.items():
            if code in price_lookup:
                tp = price_lookup[code].get(date)
                yp = price_lookup[code].get(dates[i-1]) if i > 0 else None
                if tp is not None and yp is not None and yp > 0:
                    daily_ret += weight * (tp/yp - 1)
        daily_ret += cash_weight * CASH_RETURN
        nav *= (1+daily_ret); peak_nav = max(peak_nav, nav)
        daily_records.append({'date':date,'nav':nav,'daily_ret':daily_ret,
                              'exposure':1-cash_weight,'dd':(nav-peak_nav)/peak_nav})
    
    df = pd.DataFrame(daily_records)
    nav = df['nav'].values; years = len(nav)/252
    ann = (nav[-1]/nav[0])**(1/years)-1
    vol = np.std(df['daily_ret'].values)*np.sqrt(252)
    sharpe = ann/vol if vol > 0 else 0
    peak = np.maximum.accumulate(nav); max_dd = np.min((nav-peak)/peak)
    calmar = ann/abs(max_dd) if max_dd != 0 else 0
    df['month'] = df['date'].dt.to_period('M')
    monthly = df.groupby('month')['daily_ret'].apply(lambda x: np.prod(1+x)-1)
    win = (monthly>0).sum()/len(monthly)*100
    monthly_nav = df.groupby('month')['nav'].last()
    new_high = (monthly_nav > monthly_nav.cummax().shift(1).fillna(0)).sum()/len(monthly)*100
    real_inv = df[df['exposure']>0.1]
    real_m = real_inv.groupby('month')['daily_ret'].apply(lambda x: np.prod(1+x)-1)
    real_win = (real_m>0).sum()/len(real_m)*100 if len(real_m)>0 else 0
    return {'ann':ann,'max_dd':max_dd,'sharpe':sharpe,'calmar':calmar,
            'win':win,'real_win':real_win,'new_high':new_high,
            'n_stop':n_stop,'cost':total_cost*100,'trades':n_trades,
            'exposure':df['exposure'].mean()*100}

def main():
    print("="*70)
    print("🔥 Phoenix v16 — 因子alpha诊断 + 低波深挖")
    print("="*70)
    
    cache = pd.read_pickle(DATA_CACHE)
    index_df, stocks = cache['index'], cache['stocks']
    print(f"股票: {len(stocks)}只")
    factor_df = compute_all_factors(stocks, index_df)
    print(f"因子: {factor_df['code'].nunique()}只\n")
    
    LV = ['low_vol']
    LV_MOM = ['low_vol', 'mom_1m']
    LV_REV = ['low_vol', 'rev_1w']
    DEFENSE = ['low_vol', 'low_beta', 'stability', 'amplitude']
    ALL = ['mom_1m', 'rev_1w', 'low_vol', 'low_beta', 'stability', 'amplitude']
    
    # ===== 1. T+0 vs T+1 alpha诊断 =====
    print(f"{'='*70}")
    print(f"📊 T+0(理想) vs T+1(真实) — 因子alpha诊断")
    print(f"{'='*70}")
    print(f"\n{'配置':<30} {'模式':>5} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6}")
    print("-"*75)
    
    diag_configs = [
        ("低波 TOP30 MA250",     LV, 30),
        ("低波+动量 TOP30 MA250", LV_MOM, 30),
        ("纯防御 TOP30 MA250",   DEFENSE, 30),
        ("全因子 TOP30 MA250",   ALL, 30),
    ]
    
    for label, factors, tn in diag_configs:
        for t1_label, t1 in [("T+0", False), ("T+1", True)]:
            r = run_backtest(index_df, factor_df, stocks, factors, top_n=tn,
                           use_ma250=True, use_stoploss=True, t_plus_1=t1, use_cost=True)
            print(f"{label:<30} {t1_label:>5} {r['ann']*100:>6.1f}% {r['max_dd']*100:>6.1f}% "
                  f"{r['sharpe']:>5.2f} {r['calmar']:>6.2f} {r['win']:>5.1f}%")
    
    # ===== 2. 低波因子深挖 =====
    print(f"\n{'='*70}")
    print(f"📊 低波因子深挖 — 不同TOP_N + MA250 + 止损")
    print(f"{'='*70}")
    print(f"\n{'配置':<35} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6} {'实战胜':>6} {'创新高':>6} {'止损':>4} {'仓位':>5}")
    print("-"*100)
    
    deep_configs = [
        ("低波 TOP10 MA250 止损8%",     LV, 10, True, 0.08),
        ("低波 TOP20 MA250 止损8%",     LV, 20, True, 0.08),
        ("低波 TOP30 MA250 止损8%",     LV, 30, True, 0.08),
        ("低波 TOP50 MA250 止损8%",     LV, 50, True, 0.08),
        ("低波 TOP100 MA250 止损8%",    LV, 100, True, 0.08),
        ("低波 TOP30 MA250 止损5%",     LV, 30, True, 0.05),
        ("低波 TOP30 MA250 止损10%",    LV, 30, True, 0.10),
        ("低波 TOP30 MA250 无止损",     LV, 30, False, 0),
        ("低波 TOP30 满仓 止损8%",      LV, 30, True, 0.08),
        ("低波 TOP50 MA250 止损5%",     LV, 50, True, 0.05),
        ("低波 TOP50 MA250 无止损",     LV, 50, False, 0),
        ("低波+反转 TOP30 MA250 止损8%", LV_REV, 30, True, 0.08),
        ("低波+反转 TOP50 MA250 止损8%", LV_REV, 50, True, 0.08),
        ("纯防御 TOP50 MA250 止损8%",   DEFENSE, 50, True, 0.08),
    ]
    
    all_results = {}
    best_ann = -999; best_label = ""
    for label, factors, tn, sl, sl_val in deep_configs:
        r = run_backtest(index_df, factor_df, stocks, factors, top_n=tn,
                       use_ma250=True, use_stoploss=sl, stop_loss=sl_val,
                       t_plus_1=True, use_cost=True)
        # 控制use_ma250
        if "满仓" in label:
            r = run_backtest(index_df, factor_df, stocks, factors, top_n=tn,
                           use_ma250=False, use_stoploss=sl, stop_loss=sl_val,
                           t_plus_1=True, use_cost=True)
        all_results[label] = r
        print(f"{label:<35} {r['ann']*100:>6.1f}% {r['max_dd']*100:>6.1f}% {r['sharpe']:>5.2f} "
              f"{r['calmar']:>6.2f} {r['win']:>5.1f}% {r['real_win']:>5.1f}% {r['new_high']:>5.1f}% "
              f"{r['n_stop']:>4} {r['exposure']:>4.1f}%")
        if r['ann'] > best_ann:
            best_ann = r['ann']; best_label = label
    
    # ===== 3. 最优配置详细月度 =====
    print(f"\n{'='*70}")
    print(f"📊 最优配置: {best_label} (年化{best_ann*100:.1f}%)")
    print(f"{'='*70}")
    
    # 找最优配置的参数
    for label, factors, tn, sl, sl_val in deep_configs:
        if label == best_label:
            use_ma = "满仓" not in label
            r = run_backtest(index_df, factor_df, stocks, factors, top_n=tn,
                           use_ma250=use_ma, use_stoploss=sl, stop_loss=sl_val,
                           t_plus_1=True, use_cost=True)
            print(f"\n  年化: {r['ann']*100:.1f}%")
            print(f"  回撤: {r['max_dd']*100:.1f}%")
            print(f"  夏普: {r['sharpe']:.2f}")
            print(f"  Calmar: {r['calmar']:.2f}")
            print(f"  月胜率: {r['win']:.1f}%")
            print(f"  实战胜率: {r['real_win']:.1f}%")
            print(f"  创新高: {r['new_high']:.1f}%")
            print(f"  止损次数: {r['n_stop']}")
            print(f"  交易次数: {r['trades']}")
            print(f"  总成本: {r['cost']:.1f}%")
            print(f"  平均仓位: {r['exposure']:.1f}%")
            break
    
    # ===== 目标检查 =====
    print(f"\n🎯 全部配置目标达成检查:")
    for label, r in all_results.items():
        checks = f"年化{'✅' if r['ann']>0.30 else '❌'} 回撤{'✅' if r['max_dd']>-0.10 else '❌'} 月胜{'✅' if r['win']>75 else '❌'}"
        print(f"  {checks}  {label}: {r['ann']*100:.1f}%/{r['max_dd']*100:.1f}%/{r['win']:.1f}%")
    
    with open('/opt/quant/phoenix_v16_result.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n结果: /opt/quant/phoenix_v16_result.json")

if __name__ == '__main__':
    main()
