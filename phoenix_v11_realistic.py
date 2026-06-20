#!/usr/bin/python3
"""
Phoenix v11 — 真实成本版
改进 vs v10:
  1. 交易成本：买入0.126% + 卖出0.176%（佣金万2.5+印花税0.05%+过户费0.001%+滑点0.1%）
  2. T+1执行：信号日在D，执行日在D+1（消除前视偏差）
  3. 退市股纳入选股池：拉取2020年后退市的230只股票历史数据
  4. 流动性过滤：20日均成交额>5000万
  5. 退市惩罚：持有退市股时按最后收盘价50%清算
对比：v11(真实) vs v10(理想)
"""

import akshare as ak
import pandas as pd
import numpy as np
import json, os, time, requests

# ===== 策略参数 =====
REBALANCE_FREQ = 21
TOP_N = 10
MA_SLOW = 250
MA_FAST = 5
STOP_LOSS = 0.05
CASH_RETURN = 0.02 / 252

# ===== 真实成本参数 =====
COST_BUY = 0.00126    # 佣金0.025% + 过户费0.001% + 滑点0.1%
COST_SELL = 0.00176   # 佣金0.025% + 印花税0.05% + 过户费0.001% + 滑点0.1%

# 流动性过滤
MIN_AMOUNT_20D = 50_000_000  # 5000万

# 退市惩罚
DELIST_RECOVERY = 0.50  # 退市时仅收回最后收盘价的50%

DATA_CACHE = "/tmp/phoenix_alla_data.pkl"

# ===================================================================
# 第一部分：拉取退市股数据
# ===================================================================

def get_delisted_codes():
    """获取2020年后退市的股票列表"""
    sh = ak.stock_info_sh_delist()
    sz = ak.stock_info_sz_delist()

    sz['终止上市日期'] = pd.to_datetime(sz['终止上市日期'], errors='coerce')
    sz_recent = sz[sz['终止上市日期'] >= '2020-01-01']

    sh['暂停上市日期'] = pd.to_datetime(sh['暂停上市日期'], errors='coerce')
    sh_recent = sh[sh['暂停上市日期'] >= '2020-01-01']

    codes = []
    for _, row in sz_recent.iterrows():
        codes.append(('sz' + str(row['证券代码']).zfill(6), str(row['证券简称']), row['终止上市日期']))
    for _, row in sh_recent.iterrows():
        codes.append(('sh' + str(row['公司代码']).zfill(6), str(row['公司简称']), row['暂停上市日期']))

    return codes

def fetch_delisted_data(codes):
    """通过腾讯接口拉取退市股历史数据"""
    all_data = {}
    failed = 0
    for i, (symbol, name, delist_date) in enumerate(codes):
        try:
            url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,2019-01-01,2026-06-18,640,qfq'
            r = requests.get(url, timeout=10)
            data = r.json()
            if data.get('data') and data['data'].get(symbol):
                kline = data['data'][symbol].get('qfqday') or data['data'][symbol].get('day')
                if kline and len(kline) > 60:
                    df = pd.DataFrame(kline, columns=['date', 'open', 'close', 'high', 'low', 'volume'])
                    df['date'] = pd.to_datetime(df['date'])
                    for col in ['open', 'close', 'high', 'low', 'volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    df['amount'] = df['close'] * df['volume']  # 估算成交额
                    code = symbol[-6:]
                    df['code'] = code
                    all_data[code] = df
                else:
                    failed += 1
            else:
                failed += 1
        except:
            failed += 1

        if (i + 1) % 30 == 0:
            print(f"  退市股拉取进度: {i+1}/{len(codes)}, 成功{len(all_data)}, 失败{failed}")
        time.sleep(0.05)

    return all_data

# ===================================================================
# 第二部分：因子计算（含流动性）
# ===================================================================

def compute_factors(stock_data):
    """计算因子，包含20日均成交额用于流动性过滤"""
    all_factors = []
    last_dates = {}  # 记录每只股票最后交易日（用于检测退市）

    for code, df in stock_data.items():
        if len(df) < 252:
            continue
        df = df.copy()
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        df['avg_amount_20d'] = df['amount'].rolling(20).mean()
        df['code'] = code
        all_factors.append(df[['date', 'code', 'close', 'ret_1m', 'avg_amount_20d']].copy())
        last_dates[code] = df['date'].iloc[-1]

    factor_df = pd.concat(all_factors, ignore_index=True).dropna(subset=['ret_1m'])
    return factor_df, last_dates

# ===================================================================
# 第三部分：回测引擎（含交易成本、T+1、退市处理）
# ===================================================================

def run_backtest(index_df, factor_df, stock_data, last_dates, label="", realistic=True):
    """
    回测引擎
    realistic=True: T+1执行 + 交易成本 + 退市处理 + 流动性过滤
    realistic=False: v10理想版（无成本，当日执行）
    """
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
    last_rebal_nav = 1.0
    n_stop = 0
    n_delist = 0
    total_cost = 0.0
    pending_rebalance = False  # T+1: 信号日标记，次日执行

    for i, date in enumerate(dates):
        if i < start_i:
            continue

        invest_signal = idx.iloc[i]['invest']
        prev_date = dates[i-1] if i > 0 else None

        # --- 退市检测 ---
        if portfolio and realistic:
            for code in list(portfolio.keys()):
                if code in last_dates and date > last_dates[code]:
                    # 退市清算
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

        # --- 调仓逻辑 ---
        if realistic:
            # T+1: 昨天是调仓信号日，今天执行
            if pending_rebalance:
                pending_rebalance = False
                month_ret = nav / last_rebal_nav - 1
                if month_ret < -STOP_LOSS and portfolio:
                    cost = sum(portfolio.values()) * COST_SELL
                    nav *= (1 - cost)
                    total_cost += cost
                    portfolio = {}
                    cash_weight = 1.0
                    target_stocks = []
                    n_stop += 1
                    last_rebal_nav = nav
                else:
                    cross = factor_df[factor_df['date'] == prev_date].copy()
                    # 流动性过滤
                    cross = cross[cross['avg_amount_20d'] >= MIN_AMOUNT_20D]
                    if len(cross) >= TOP_N:
                        new_targets = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
                    elif len(cross) > 0:
                        new_targets = cross.nlargest(len(cross), 'ret_1m')['code'].tolist()
                    else:
                        new_targets = []

                    # 计算换手成本
                    old_set = set(portfolio.keys())
                    new_set = set(new_targets)
                    sold_w = sum(portfolio.get(c, 0) for c in (old_set - new_set))
                    bought_w = sum(1.0 / len(new_targets) for c in (new_set - old_set)) if new_targets else 0
                    cost = sold_w * COST_SELL + bought_w * COST_BUY
                    nav *= (1 - cost)
                    total_cost += cost

                    target_stocks = new_targets
                    last_rebal_nav = nav

            # 标记今天是否为调仓信号日
            if date in rebalance_dates:
                pending_rebalance = True
        else:
            # v10理想版：当日信号当日执行
            if date in rebalance_dates:
                month_ret = nav / last_rebal_nav - 1
                if month_ret < -STOP_LOSS and portfolio:
                    portfolio = {}
                    cash_weight = 1.0
                    target_stocks = []
                    n_stop += 1
                    last_rebal_nav = nav
                else:
                    cross = factor_df[factor_df['date'] == date].copy()
                    if len(cross) >= TOP_N:
                        target_stocks = cross.nlargest(TOP_N, 'ret_1m')['code'].tolist()
                    last_rebal_nav = nav

        # --- 投资信号执行 ---
        if realistic:
            # T+1: 投资信号也延迟一天
            if i > 0:
                prev_invest = idx.iloc[i-1]['invest']
            else:
                prev_invest = 0
            if prev_invest == 1 and target_stocks:
                if set(portfolio.keys()) != set(target_stocks):
                    # 进入或换仓
                    old_set = set(portfolio.keys())
                    new_set = set(target_stocks)
                    if not old_set:  # 从空仓到满仓
                        cost = 1.0 * COST_BUY
                        nav *= (1 - cost)
                        total_cost += cost
                    portfolio = {c: 1.0 / len(target_stocks) for c in target_stocks}
                    cash_weight = 0.0
            else:
                if portfolio:
                    cost = sum(portfolio.values()) * COST_SELL
                    nav *= (1 - cost)
                    total_cost += cost
                    portfolio = {}
                cash_weight = 1.0
        else:
            if invest_signal == 1 and target_stocks:
                if set(portfolio.keys()) != set(target_stocks):
                    pos_w = 1.0 / len(target_stocks)
                    portfolio = {c: pos_w for c in target_stocks}
                    cash_weight = 0.0
            else:
                portfolio = {}
                cash_weight = 1.0

        # --- 日收益 ---
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
            'invest': invest_signal, 'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'n_positions': len(portfolio),
        })

    return pd.DataFrame(daily_records), n_stop, n_delist, total_cost

# ===================================================================
# 第四部分：分析输出
# ===================================================================

def analyze(records_df, index_df, label="", n_stop=0, n_delist=0, total_cost=0.0):
    nav = records_df['nav'].values
    total_days = len(nav)
    years = total_days / 252
    ann_return = (nav[-1] / nav[0]) ** (1 / years) - 1
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
        ret=('daily_ret', lambda x: np.prod(1 + x) - 1),
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
    idx_ann = (idx_nav[-1]) ** (1 / years) - 1

    print(f"\n{'='*60}")
    print(f"📊 {label}")
    print(f"{'='*60}")
    print(f"\n📅 回测: {records_df['date'].iloc[0].date()} ~ {records_df['date'].iloc[-1].date()} ({years:.1f}年)")
    print(f"\n{'指标':<20} {label:<22} {'基准指数':<15}")
    print("-" * 58)
    print(f"{'年化收益':<20} {ann_return*100:>7.1f}%               {idx_ann*100:>7.1f}%")
    print(f"{'累计收益':<20} {total_return*100:>7.1f}%               {(idx_nav[-1]-1)*100:>7.1f}%")
    print(f"{'年化波动':<20} {ann_vol*100:>7.1f}%")
    print(f"{'夏普':<20} {sharpe:>7.2f}")
    print(f"{'最大回撤':<20} {max_dd*100:>7.1f}%               ")
    print(f"{'Calmar':<20} {calmar:>7.2f}")
    print(f"{'止损次数':<20} {n_stop}")
    if n_delist > 0:
        print(f"{'退市事件':<20} {n_delist}")
    if total_cost > 0:
        print(f"{'总交易成本':<20} {total_cost*100:.2f}%")

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
            'n_stop': n_stop, 'n_delist': n_delist, 'total_cost': total_cost}

# ===================================================================
# 第五部分：主流程
# ===================================================================

def main():
    print("=" * 60)
    print("🔥 Phoenix v11 — 真实成本版 vs v10 理想版")
    print("=" * 60)

    # --- 加载现有490只活跃股 ---
    print("\n[1] 加载全A活跃股数据...")
    cache = pd.read_pickle(DATA_CACHE)
    index_df, stocks_active = cache['index'], cache['stocks']
    print(f"  国证A指: {len(index_df)}天, 活跃股{len(stocks_active)}只")

    # --- 拉取退市股 ---
    print("\n[2] 拉取退市股数据...")
    delist_cache = "/tmp/phoenix_delist_data.pkl"
    if os.path.exists(delist_cache):
        stocks_delist = pd.read_pickle(delist_cache)
        print(f"  从缓存加载退市股: {len(stocks_delist)}只")
    else:
        codes = get_delisted_codes()
        print(f"  2020年后退市股票: {len(codes)}只")
        stocks_delist = fetch_delisted_data(codes)
        print(f"  成功拉取: {len(stocks_delist)}只")
        pd.to_pickle(stocks_delist, delist_cache)

    # --- 合并选股池 ---
    all_stocks = {}
    all_stocks.update(stocks_active)
    all_stocks.update(stocks_delist)
    print(f"\n[3] 合并选股池: {len(all_stocks)}只 (活跃{len(stocks_active)} + 退市{len(stocks_delist)})")

    # --- 计算因子 ---
    print("  计算因子(含流动性)...")
    factor_df, last_dates = compute_factors(all_stocks)
    n_delist_in_pool = sum(1 for c in last_dates if c in stocks_delist)
    print(f"  因子数据: {len(factor_df)}行, {factor_df['code'].nunique()}只")
    print(f"  其中退市股: {n_delist_in_pool}只")

    # --- 流动性统计 ---
    sample = factor_df[factor_df['date'] == factor_df['date'].iloc[-1]]
    liquid_count = (sample['avg_amount_20d'] >= MIN_AMOUNT_20D).sum()
    illiquid_count = len(sample) - liquid_count
    print(f"  流动性过滤(>{MIN_AMOUNT_20D/1e6:.0f}万): 通过{liquid_count}只, 过滤{illiquid_count}只")

    # --- 回测 v11 真实版 ---
    print("\n[4] 回测 v11 真实版 (T+1+成本+退市+流动性)...")
    records_v11, n_stop_v11, n_delist_v11, cost_v11 = run_backtest(
        index_df, factor_df, all_stocks, last_dates, "v11真实", realistic=True)
    results_v11 = analyze(records_v11, index_df, f"Phoenix v11 真实版({len(all_stocks)}只)",
                          n_stop_v11, n_delist_v11, cost_v11)

    # --- 回测 v10 理想版（同选股池，无成本）---
    print("\n[5] 回测 v10 理想版 (同选股池, 无成本)...")
    factor_v10, last_dates_v10 = compute_factors(stocks_active)
    records_v10, n_stop_v10, _, _ = run_backtest(
        index_df, factor_v10, stocks_active, {}, "v10理想", realistic=False)
    results_v10 = analyze(records_v10, index_df, f"Phoenix v10 理想版({len(stocks_active)}只)",
                          n_stop_v10)

    # --- 对比总结 ---
    print(f"\n{'='*60}")
    print(f"📊 v11真实 vs v10理想 对比总结")
    print(f"{'='*60}")
    print(f"\n{'指标':<20} {'v11真实':<18} {'v10理想':<18} {'差异':<12}")
    print("-" * 66)
    for key, lbl in [('ann_return', '年化收益'), ('max_dd', '最大回撤'), ('sharpe', '夏普'),
                     ('calmar', 'Calmar'), ('win_rate', '月胜率'), ('new_high_rate', '创新高')]:
        v11 = results_v11[key] * 100
        v10 = results_v10[key] * 100
        diff = v11 - v10
        print(f"{lbl:<20} {v11:>7.1f}%{'':>9} {v10:>7.1f}%{'':>9} {diff:>+7.1f}")
    print(f"{'止损次数':<20} {results_v11['n_stop']:<18} {results_v10['n_stop']:<18}")
    print(f"{'退市事件':<20} {results_v11.get('n_delist', 0):<18} {'0':<18}")
    print(f"{'总交易成本':<20} {results_v11.get('total_cost', 0)*100:.2f}%{'':>12} {'0':<18}")

    # --- 保存 ---
    with open('/opt/quant/phoenix_v11_result.json', 'w') as f:
        json.dump({'v11_realistic': results_v11, 'v10_ideal': results_v10},
                  f, indent=2, default=str, ensure_ascii=False)
    records_v11.to_csv('/opt/quant/phoenix_v11_daily.csv', index=False)
    print(f"\n结果: /opt/quant/phoenix_v11_result.json")

if __name__ == '__main__':
    main()
