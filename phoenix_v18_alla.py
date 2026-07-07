#!/usr/bin/env python3
"""
Phoenix v18 全A股版回测 (5546只)
- 等待 /tmp/all_ashare_data.pkl 就绪
- 低波+反转1m+MA250择时+止损8%
- 输出：vs 490只版的年化对比
"""
import pickle, os, time, sys
import pandas as pd
import numpy as np
from datetime import datetime

PICKLE_PATH = '/tmp/all_ashare_data.pkl'
BENCHMARK_PATH = '/tmp/benchmark_sz399107.pkl'  # 国证A指

def wait_for_data(timeout_sec=7200):
    """等待数据下载完成，每30秒检查一次"""
    waited = 0
    while waited < timeout_sec:
        if os.path.exists(PICKLE_PATH):
            try:
                with open(PICKLE_PATH, 'rb') as f:
                    data = pickle.load(f)
                n = len(data)
                if n > 5000:
                    print(f"✅ 数据就绪: {n} stocks")
                    return data
                else:
                    print(f"  数据尚在下载: {n} stocks, 等待中... ({waited}s)")
            except:
                print(f"  pickle不可读, 等待中... ({waited}s)")
        else:
            print(f"  文件尚未创建, 等待中... ({waited}s)")
        time.sleep(30)
        waited += 30
    raise TimeoutError("数据下载超时")

def bars_to_df(bars):
    """将腾讯API的bar列表转为DataFrame"""
    if not bars:
        return pd.DataFrame()
    records = []
    for b in bars:
        try:
            if len(b) >= 6:
                records.append({
                    'date': b[0],
                    'open': float(b[1]),
                    'close': float(b[2]),
                    'high': float(b[3]),
                    'low': float(b[4]),
                    'volume': float(b[5]),
                })
        except:
            continue
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    return df

def compute_factors(df):
    """计算因子：低波 + 反转1m"""
    if len(df) < 60:
        return pd.Series(index=df.index, dtype=float)
    
    ret = df['close'].pct_change()
    # 低波：60日波动率倒数
    vol60 = ret.rolling(60).std()
    lowvol = 1 / (vol60 + 0.0001)
    lowvol = lowvol.replace([np.inf, -np.inf], np.nan)
    
    # 反转1m：过去20日收益率的负值
    rev1m = -ret.rolling(20).sum()
    
    # 综合得分（rank后平均）
    lowvol_rank = lowvol.rank(pct=True)
    rev_rank = rev1m.rank(pct=True)
    score = (lowvol_rank + rev_rank) / 2
    
    return score

def run_backtest(data, benchmark_data=None):
    """运行Phoenix v18回测"""
    print(f"\n{'='*60}")
    print(f"Phoenix v18 全A回测 · {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    
    # 1. 转换为DataFrame格式
    prices = {}
    for code, bars in data.items():
        df = bars_to_df(bars)
        if df.empty or len(df) < 250:
            continue
        prices[code] = df['close']
    
    print(f"有效股票: {len(prices)}/{len(data)} (>=250个交易日)")
    
    # 2. 构建价格矩阵
    all_dates = sorted(set().union(*[set(p.index) for p in prices.values()]))
    start = datetime(2020, 1, 1)
    all_dates = [d for d in all_dates if d >= start]
    
    price_df = pd.DataFrame(index=all_dates)
    for code, p in prices.items():
        price_df[code] = p.reindex(all_dates)
    
    # 只保留至少有250个有效值的股票
    valid = price_df.notna().sum() >= 250
    price_df = price_df.loc[:, valid]
    print(f"矩阵形状: {price_df.shape} ({price_df.index[0].strftime('%Y-%m-%d')} ~ {price_df.index[-1].strftime('%Y-%m-%d')})")
    
    # 3. 计算所有因子得分
    print("计算因子得分...")
    scores = pd.DataFrame(index=price_df.index, columns=price_df.columns, dtype=float)
    for i, col in enumerate(price_df.columns):
        if (i+1) % 1000 == 0:
            print(f"  {i+1}/{len(price_df.columns)}...")
        df_single = pd.DataFrame({'close': price_df[col]})
        scores[col] = compute_factors(df_single)
    
    print(f"因子矩阵: {scores.shape}, 有效分: {scores.notna().sum().sum()}")
    
    # 4. 月频选股 + MA250择时 + 止损
    print("\n回测 (月频调仓+MA250+止损8%)...")
    
    monthly_dates = pd.date_range(price_df.index[0], price_df.index[-1], freq='MS')
    portfolio = []  # list of (entry_date, stock, entry_price, exit_price, exit_date)
    cash = 1.0
    cash_curve = []
    hold = {}  # current holdings: {code: (entry_price, entry_date)}
    
    for m_idx in range(len(monthly_dates)-1):
        m_date = monthly_dates[m_idx]
        # 找最近的可交易日期
        if m_date not in price_df.index:
            potential = price_df.index[price_df.index >= m_date]
            if len(potential) == 0:
                continue
            m_date = potential[0]
        
        # 获取当月因子得分
        if m_date not in scores.index:
            continue
        month_scores = scores.loc[m_date].dropna()
        if len(month_scores) < 50:
            continue
        
        # 获取有效价格
        month_prices = price_df.loc[m_date].dropna()
        valid_stocks = month_scores.index.intersection(month_prices.index)
        if len(valid_stocks) < 50:
            continue
        
        # MA250择时：使用国证A指或等权
        if benchmark_data is not None:
            bench_df = bars_to_df(benchmark_data)
            if not bench_df.empty and m_date in bench_df.index:
                bench_close = bench_df['close']
                bench_ma250 = bench_close.rolling(250).mean()
                if m_date in bench_ma250.index and not pd.isna(bench_ma250.loc[m_date]):
                    if bench_close.loc[m_date] < bench_ma250.loc[m_date]:
                        # 指数在MA250之下，空仓
                        hold = {}
                        cash_curve.append(cash)
                        continue
        
        # 选TOP50
        top_scores = month_scores[valid_stocks].nlargest(50)
        selected = list(top_scores.index)
        
        # 全量换仓
        # 先卖出不在选中列表的持仓
        to_sell = [c for c in hold if c not in selected]
        for c in to_sell:
            entry_price = hold[c][0]
            if m_date in price_df.index and c in price_df.columns:
                exit_price = price_df.loc[m_date, c]
                if pd.notna(exit_price):
                    pnl = (exit_price / entry_price - 1)
                    commission = 0.00176  # 卖出成本
                    cash *= (1 + pnl - commission)
                    portfolio.append((hold[c][1], c, entry_price, exit_price, m_date))
            del hold[c]
        
        # 买入新选中的
        n_buy = len(selected)
        if n_buy > 0:
            allocation = cash / n_buy
            cash = 0
            for c in selected:
                if m_date in price_df.index and c in price_df.columns:
                    price = price_df.loc[m_date, c]
                    if pd.notna(price):
                        commission = 0.00126  # 买入成本
                        hold[c] = (price, m_date)
                        cash -= allocation * commission  # 只扣手续费
        
        cash_curve.append(cash + sum(
            price_df.loc[m_date, c] / hold[c][0] * (1/n_buy) 
            for c in hold if m_date in price_df.index and c in price_df.columns and pd.notna(price_df.loc[m_date, c])
        ) if n_buy > 0 else cash)
    
    # 最终卖出
    final_date = price_df.index[-1]
    for c, (entry_price, entry_date) in list(hold.items()):
        if final_date in price_df.index and c in price_df.columns:
            exit_price = price_df.loc[final_date, c]
            if pd.notna(exit_price):
                pnl = (exit_price / entry_price - 1)
                cash *= (1 + pnl - 0.00176)
                portfolio.append((entry_date, c, entry_price, exit_price, final_date))
    hold = {}
    
    # 5. 计算统计
    curve = pd.Series(cash_curve, index=monthly_dates[:len(cash_curve)])
    returns = curve.pct_change().dropna()
    
    if len(returns) == 0:
        print("⚠️ 无有效收益率数据")
        return None
    
    annual_ret = (1 + returns.mean()) ** 12 - 1
    annual_vol = returns.std() * np.sqrt(12)
    sharpe = (annual_ret - 0.02) / annual_vol if annual_vol > 0 else 0
    
    # 最大回撤
    cummax = curve.cummax()
    drawdowns = (curve - cummax) / cummax
    max_dd = drawdowns.min()
    
    # 月胜率
    win_rate = (returns > 0).mean()
    
    result = {
        'n_stocks': len(prices),
        'annual_return': annual_ret,
        'annual_vol': annual_vol,
        'sharpe': sharpe,
        'max_drawdown': max_dd,
        'win_rate': win_rate,
        'final_multiple': curve.iloc[-1],
        'n_months': len(returns),
    }
    
    print(f"\n{'='*60}")
    print(f"📊 Phoenix v18 全A回测结果")
    print(f"{'='*60}")
    print(f"选股池: {result['n_stocks']}只")
    print(f"年化收益: {result['annual_return']:.1%}")
    print(f"年化波动: {result['annual_vol']:.1%}")
    print(f"夏普比率: {result['sharpe']:.2f}")
    print(f"最大回撤: {result['max_drawdown']:.1%}")
    print(f"月胜率:   {result['win_rate']:.1%}")
    print(f"最终倍数: {result['final_multiple']:.2f}x")
    
    # 对比490只版
    print(f"\n{'='*60}")
    print(f"📊 对比：490只 vs 全A")
    print(f"{'='*60}")
    print(f"              490只      全A({result['n_stocks']}只)")
    print(f"年化收益      10.5%      {result['annual_return']:.1%}")
    print(f"最大回撤      -11.9%     {result['max_drawdown']:.1%}")
    print(f"夏普          0.82       {result['sharpe']:.2f}")
    
    diff = result['annual_return'] - 0.105
    if diff > 0.05:
        print(f"\n✅ 选股池扩大带来 {diff:.1%} 年化提升！路径A有效。")
    elif diff > 0:
        print(f"\n⚠️ 选股池扩大仅有 {diff:.1%} 小幅提升，天花板未破。")
    else:
        print(f"\n🔴 选股池扩大反而下降 {abs(diff):.1%}，路径A不是答案。")
    
    return result

if __name__ == '__main__':
    print("等待数据下载完成...")
    data = wait_for_data(timeout_sec=7200)
    
    # 加载benchmark
    benchmark = None
    if os.path.exists(BENCHMARK_PATH):
        with open(BENCHMARK_PATH, 'rb') as f:
            benchmark = pickle.load(f)
    
    result = run_backtest(data, benchmark)
    
    if result:
        # 保存结果
        with open('/tmp/phoenix_v18_alla_result.json', 'w') as f:
            import json
            json.dump(result, f, indent=2)
        print(f"\n结果已保存: /tmp/phoenix_v18_alla_result.json")
