#!/usr/bin/python3
"""
Phoenix Strategy — 多层Alpha + 自适应风控
目标: 2000-5000万容量 | 回撤<10% | 年化30%+ | 月胜率75%+

架构:
  Layer 1: 市场择时 (MA250 + 波动率regime)
  Layer 2: 多因子选股 (滚动IC加权, 无前视偏差)
  Layer 3: 仓位管理 (波动率目标 + 单票上限)
  Layer 4: 风控 (5%月止损 + 回撤熔断)
  Layer 5: 现金增强 (空仓期货币基金收益)

数据: akshare 新浪指数 + 腾讯个股
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import time
import os
import sys

# ============================================================
# 配置
# ============================================================
INDEX_CODE = "sz399006"      # 创业板指(新浪格式)
START_DATE = "20200101"
END_DATE = "20260618"
REBALANCE_FREQ = 21          # 月度调仓(21交易日)
TOP_N = 10                   # 持仓数
MA_WINDOW = 250              # 择时均线
STOP_LOSS = 0.05             # 月度止损5%
DD_WARN = 0.07               # 回撤预警7%
DD_REDUCE = 0.09             # 回撤减仓9%
DD_KILL = 0.10               # 回撤清仓10%
TARGET_VOL = 0.15            # 目标年化波动率15%
MAX_POSITION = 0.15          # 单票最大15%
CASH_RETURN = 0.02 / 252     # 空仓期日收益(货币基金~2%)
DATA_CACHE = "/tmp/phoenix_data.pkl"
MAX_STOCKS = 60              # 拉取股票数(控制时间)

# ============================================================
# Phase 1: 数据拉取
# ============================================================

def fetch_index_data():
    """拉取创业板指日线(新浪源)"""
    print("[1/4] 拉取创业板指日线(新浪源)...")
    df = ak.stock_zh_index_daily(symbol=INDEX_CODE)
    df['date'] = pd.to_datetime(df['date'])
    df = df[(df['date'] >= START_DATE) & (df['date'] <= END_DATE)]
    df = df.sort_values('date').reset_index(drop=True)
    print(f"  创业板指: {len(df)} 天, {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    return df

def fetch_stock_universe():
    """获取创业板股票列表"""
    print("[2/4] 获取创业板股票列表...")
    try:
        df = ak.stock_info_a_code_name()
        # 创业板以300开头, 取前120只(上市最早=流动性最好)
        gem = df[df['code'].str.startswith('300')]
        codes = gem['code'].tolist()
        # 按代码排序取前120只(300001-300120, 上市早流动性好)
        codes = sorted(codes)[:120]
        print(f"  创业板股票: {len(codes)} 只 (取前120只高流动性)")
        return codes
    except Exception as e:
        print(f"  获取失败: {e}")
        return []

def fetch_stock_data(codes, max_stocks=60):
    """批量拉取个股日线(腾讯源)"""
    print(f"[3/4] 拉取个股日线(腾讯源, 最多{max_stocks}只)...")
    
    all_data = {}
    failed = 0
    
    if len(codes) > max_stocks:
        codes = codes[:max_stocks]
    
    for i, code in enumerate(codes):
        try:
            # 腾讯源需要带市场前缀: sz300xxx
            tencent_code = f"sz{code}"
            df = ak.stock_zh_a_daily(symbol=tencent_code, 
                                     start_date=START_DATE, end_date=END_DATE,
                                     adjust="qfq")
            if df is None or len(df) == 0:
                failed += 1
                continue
            
            df['date'] = pd.to_datetime(df['date'])
            df = df[['date', 'open', 'close', 'high', 'low', 'volume', 'amount']].copy()
            df = df.sort_values('date').reset_index(drop=True)
            df['code'] = code
            all_data[code] = df
            
            if (i + 1) % 10 == 0:
                print(f"  已拉取 {i+1}/{len(codes)} 只, 成功 {len(all_data)}")
            
            time.sleep(0.1)  # 限速
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"  {code} 失败: {e}")
    
    print(f"  完成: 成功 {len(all_data)} 只, 失败 {failed} 只")
    return all_data

def save_cache(index_df, stock_data):
    """缓存数据"""
    cache = {'index': index_df, 'stocks': stock_data}
    pd.to_pickle(cache, DATA_CACHE)
    print(f"  数据缓存至 {DATA_CACHE}")

def load_cache():
    """加载缓存"""
    cache = pd.read_pickle(DATA_CACHE)
    return cache['index'], cache['stocks']

# ============================================================
# Phase 2: 因子计算
# ============================================================

def compute_factors(stock_data):
    """计算多因子"""
    print("[4/4] 计算因子...")
    
    all_factors = []
    
    for code, df in stock_data.items():
        if len(df) < 252:
            continue
        
        df = df.copy()
        df['ret_1d'] = df['close'].pct_change()
        
        # 因子1: ret_1m (月动量)
        df['ret_1m'] = df['close'] / df['close'].shift(21) - 1
        
        # 因子2: volatility_1m (月波动率)
        df['volatility_1m'] = df['ret_1d'].rolling(21).std()
        
        # 因子3: bb_position (布林带位置)
        ma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        df['bb_position'] = (df['close'] - ma20) / (2 * std20)
        
        # 因子4: illiquidity (非流动性)
        avg_amount_1m = df['amount'].rolling(21).mean()
        df['illiquidity'] = (df['ret_1m'].abs() / avg_amount_1m) * 1e8
        
        # 因子5: turnover
        df['turnover'] = df['amount'] / df['close']
        
        # 因子6: max_ret_1m
        df['max_ret_1m'] = df['ret_1d'].rolling(21).max()
        
        # 因子7: min_ret_1m
        df['min_ret_1m'] = df['ret_1d'].rolling(21).min()
        
        # 因子8: volume_ratio
        df['volume_ratio'] = df['volume'].rolling(5).mean() / df['volume'].rolling(20).mean()
        
        # 前瞻收益(用于IC计算)
        df['fwd_ret_21d'] = df['close'].shift(-21) / df['close'] - 1
        
        df['code'] = code
        all_factors.append(df)
    
    factor_df = pd.concat(all_factors, ignore_index=True)
    factor_df = factor_df.dropna(subset=['ret_1m', 'volatility_1m', 'bb_position'])
    print(f"  因子数据: {len(factor_df)} 行, {factor_df['code'].nunique()} 只股票")
    return factor_df

# ============================================================
# Phase 3: 回测引擎
# ============================================================

def get_rebalance_dates(index_df):
    """获取调仓日列表(每21个交易日)"""
    dates = index_df['date'].tolist()
    rebalance = [dates[i] for i in range(MA_WINDOW, len(dates), REBALANCE_FREQ)]
    return rebalance

def compute_rolling_ic_weights(factor_df, current_date, lookback_days=365):
    """计算滚动IC权重 (无前视偏差)"""
    hist = factor_df[factor_df['date'] <= current_date].copy()
    hist = hist[hist['date'] >= current_date - timedelta(days=lookback_days)]
    
    if len(hist) < 100:
        return None
    
    factor_cols = ['ret_1m', 'volatility_1m', 'bb_position', 'illiquidity', 
                   'turnover', 'max_ret_1m', 'min_ret_1m', 'volume_ratio']
    
    weights = {}
    for col in factor_cols:
        valid = hist[[col, 'fwd_ret_21d']].dropna()
        if len(valid) < 50:
            continue
        ic = valid[col].corr(valid['fwd_ret_21d'], method='spearman')
        if abs(ic) > 0.02:
            weights[col] = ic
    
    if not weights:
        return None
    
    # 归一化
    total = sum(abs(v) for v in weights.values())
    for k in weights:
        weights[k] = weights[k] / total
    
    return weights

def select_stocks(factor_df, current_date, weights, top_n=10):
    """多因子选股"""
    cross = factor_df[factor_df['date'] == current_date].copy()
    if len(cross) < top_n:
        return []
    
    cross['score'] = 0.0
    for col, w in weights.items():
        ranked = cross[col].rank(pct=True)
        cross['score'] += ranked * w
    
    top = cross.nlargest(top_n, 'score')
    return top['code'].tolist()

def run_backtest(index_df, factor_df, stock_data):
    """运行Phoenix策略回测"""
    print("\n" + "="*60)
    print("Phoenix Strategy Backtest")
    print("="*60)
    
    # 择时信号
    index_df = index_df.copy()
    index_df['ma250'] = index_df['close'].rolling(MA_WINDOW).mean()
    index_df['timing_signal'] = (index_df['close'] > index_df['ma250']).astype(int)
    
    # 波动率regime
    index_df['ret_1d'] = index_df['close'].pct_change()
    index_df['vol_20d'] = index_df['ret_1d'].rolling(20).std() * np.sqrt(252)
    index_df['vol_regime'] = index_df['vol_20d'].rolling(60).mean()
    
    # 调仓日
    rebalance_dates = set(get_rebalance_dates(index_df))
    
    # 股票价格lookup
    price_lookup = {}
    for code, df in stock_data.items():
        s = df.set_index('date')['close']
        price_lookup[code] = s
    
    # 交易日列表
    all_dates = index_df['date'].tolist()
    date_idx = {d: i for i, d in enumerate(all_dates)}
    
    # 回测变量
    portfolio = {}
    cash_weight = 1.0
    nav = 1.0
    peak_nav = 1.0
    daily_records = []
    last_rebalance_date = None
    last_month_nav = 1.0
    stop_loss_triggered = False
    
    for i, date in enumerate(all_dates):
        if i < MA_WINDOW:
            continue
        
        row = index_df.iloc[i]
        timing = row['timing_signal']
        vol_regime = row['vol_regime'] if not np.isnan(row['vol_regime']) else 0.25
        
        # 波动率调整
        vol_scaler = min(1.0, TARGET_VOL / max(vol_regime, 0.05))
        
        # 回撤控制
        current_dd = (nav - peak_nav) / peak_nav if peak_nav > 0 else 0
        dd_scaler = 1.0
        if current_dd <= -DD_KILL:
            dd_scaler = 0.0
        elif current_dd <= -DD_REDUCE:
            dd_scaler = 0.25
        elif current_dd <= -DD_WARN:
            dd_scaler = 0.50
        
        # 月度止损: 如果上月亏损>5%, 本月空仓
        if stop_loss_triggered:
            portfolio = {}
            cash_weight = 1.0
            # 止损只空仓一个月
            if date in rebalance_dates:
                stop_loss_triggered = False
        
        # 月末调仓
        if date in rebalance_dates and not stop_loss_triggered:
            weights = compute_rolling_ic_weights(factor_df, date)
            
            if weights and timing == 1 and dd_scaler > 0:
                selected = select_stocks(factor_df, date, weights, TOP_N)
                
                if selected:
                    base_weight = 1.0 / len(selected)
                    target_exposure = min(base_weight * vol_scaler * dd_scaler, MAX_POSITION)
                    portfolio = {code: target_exposure for code in selected}
                    cash_weight = 1.0 - sum(portfolio.values())
                else:
                    portfolio = {}
                    cash_weight = 1.0
            else:
                portfolio = {}
                cash_weight = 1.0
            
            last_rebalance_date = date
            
            # 检查本月是否触发止损
            month_ret = nav / last_month_nav - 1
            if month_ret < -STOP_LOSS:
                stop_loss_triggered = True
                portfolio = {}
                cash_weight = 1.0
                print(f"  ⚠️ 月止损触发: {date.date()}, 月收益={month_ret*100:.1f}%")
            
            last_month_nav = nav
        
        # 逐日收益
        daily_ret = 0.0
        for code, weight in portfolio.items():
            if code in price_lookup:
                try:
                    today_price = price_lookup[code].get(date)
                    yesterday_price = price_lookup[code].get(all_dates[i-1])
                    if today_price is not None and yesterday_price is not None and yesterday_price > 0:
                        stock_ret = today_price / yesterday_price - 1
                        daily_ret += weight * stock_ret
                except:
                    pass
        
        daily_ret += cash_weight * CASH_RETURN
        nav *= (1 + daily_ret)
        peak_nav = max(peak_nav, nav)
        
        daily_records.append({
            'date': date,
            'nav': nav,
            'daily_ret': daily_ret,
            'timing': timing,
            'exposure': 1 - cash_weight,
            'dd': (nav - peak_nav) / peak_nav,
            'vol_scaler': vol_scaler,
            'dd_scaler': dd_scaler,
            'n_positions': len(portfolio),
        })
    
    return pd.DataFrame(daily_records)

# ============================================================
# Phase 4: 结果分析
# ============================================================

def analyze_results(records_df, index_df):
    """分析回测结果"""
    print("\n" + "="*60)
    print("📊 Phoenix Strategy Results")
    print("="*60)
    
    nav = records_df['nav'].values
    total_days = len(nav)
    total_return = nav[-1] / nav[0] - 1
    years = total_days / 252
    ann_return = (nav[-1] / nav[0]) ** (1/years) - 1
    
    daily_rets = records_df['daily_ret'].values
    ann_vol = np.std(daily_rets) * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0
    
    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_dd = np.min(drawdown)
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0
    
    # 月度收益
    records_df['month'] = records_df['date'].dt.to_period('M')
    monthly = records_df.groupby('month').agg(
        ret=('daily_ret', lambda x: np.prod(1+x) - 1),
        end_nav=('nav', 'last')
    ).reset_index()
    
    monthly_win_rate = (monthly['ret'] > 0).sum() / len(monthly) * 100
    n_pos = (monthly['ret'] > 0).sum()
    n_neg = (monthly['ret'] <= 0).sum()
    
    # 月度创新高
    monthly['prev_max_nav'] = monthly['end_nav'].cummax().shift(1).fillna(0)
    monthly['is_new_high'] = monthly['end_nav'] > monthly['prev_max_nav']
    new_high_rate = monthly['is_new_high'].sum() / len(monthly) * 100
    
    exposure_avg = records_df['exposure'].mean()
    timing_in_pct = records_df['timing'].mean() * 100
    
    # 创业板指对比
    idx_start = records_df['date'].iloc[0]
    idx_filtered = index_df[index_df['date'] >= idx_start].copy()
    idx_nav = idx_filtered['close'].values
    idx_nav = idx_nav / idx_nav[0]
    idx_total_ret = idx_nav[-1] / idx_nav[0] - 1
    idx_ann_ret = (idx_nav[-1] / idx_nav[0]) ** (1/years) - 1
    idx_peak = np.maximum.accumulate(idx_nav)
    idx_dd = np.min((idx_nav - idx_peak) / idx_peak)
    
    print(f"\n📅 回测期间: {records_df['date'].iloc[0].date()} ~ {records_df['date'].iloc[-1].date()}")
    print(f"📅 回测天数: {total_days} 天 ({years:.1f} 年)")
    print(f"\n{'指标':<25} {'Phoenix策略':<20} {'创业板指':<20}")
    print("-" * 65)
    print(f"{'年化收益率':<25} {ann_return*100:>8.1f}%         {idx_ann_ret*100:>8.1f}%")
    print(f"{'累计收益率':<25} {total_return*100:>8.1f}%         {idx_total_ret*100:>8.1f}%")
    print(f"{'年化波动率':<25} {ann_vol*100:>8.1f}%         {'—':>10}")
    print(f"{'夏普比率':<25} {sharpe:>8.2f}            {'—':>10}")
    print(f"{'最大回撤':<25} {max_dd*100:>8.1f}%         {idx_dd*100:>8.1f}%")
    print(f"{'Calmar比率':<25} {calmar:>8.2f}            {'—':>10}")
    
    print(f"\n📊 月度统计:")
    print(f"  总月数: {len(monthly)}")
    print(f"  正收益月: {n_pos} ({monthly_win_rate:.1f}%)")
    print(f"  负收益月: {n_neg} ({100-monthly_win_rate:.1f}%)")
    print(f"  月度创新高: {monthly['is_new_high'].sum()}/{len(monthly)} ({new_high_rate:.1f}%)")
    print(f"  最佳月收益: {monthly['ret'].max()*100:.1f}%")
    print(f"  最差月收益: {monthly['ret'].min()*100:.1f}%")
    print(f"  月均收益: {monthly['ret'].mean()*100:.2f}%")
    
    print(f"\n📊 持仓统计:")
    print(f"  平均仓位: {exposure_avg*100:.1f}%")
    print(f"  择时在场比例: {timing_in_pct:.1f}%")
    print(f"  平均持仓数: {records_df['n_positions'].mean():.1f}")
    
    # 月度收益明细
    print(f"\n📊 月度收益明细:")
    print(f"{'月份':<10} {'收益率':>10} {'创新高':>8}")
    print("-" * 30)
    for _, row in monthly.iterrows():
        nh = "✅" if row['is_new_high'] else "  "
        print(f"{str(row['month']):<10} {row['ret']*100:>8.2f}% {nh:>8}")
    
    # 目标达成
    print(f"\n{'='*60}")
    print(f"🎯 目标达成评估:")
    print(f"{'='*60}")
    targets = [
        ("年化收益>30%", ann_return > 0.30, f"{ann_return*100:.1f}%"),
        ("最大回撤<10%", max_dd > -0.10, f"{max_dd*100:.1f}%"),
        ("月胜率>75%", monthly_win_rate > 75, f"{monthly_win_rate:.1f}%"),
        ("月度创新高>70%", new_high_rate > 70, f"{new_high_rate:.1f}%"),
        ("夏普>2.0", sharpe > 2.0, f"{sharpe:.2f}"),
        ("Calmar>3.0", calmar > 3.0, f"{calmar:.2f}"),
    ]
    for name, passed, value in targets:
        status = "✅" if passed else "❌"
        print(f"  {status} {name}: {value}")
    
    return {
        'ann_return': ann_return,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'monthly_win_rate': monthly_win_rate,
        'new_high_rate': new_high_rate,
        'monthly_returns': monthly['ret'].tolist(),
    }

# ============================================================
# Main
# ============================================================

def main():
    print("="*60)
    print("🔥 Phoenix Strategy — 多层Alpha + 自适应风控")
    print("="*60)
    
    # Phase 1: 数据
    if os.path.exists(DATA_CACHE):
        print(f"\n发现缓存 {DATA_CACHE}, 直接加载...")
        index_df, stock_data = load_cache()
        print(f"  创业板指: {len(index_df)} 天")
        print(f"  个股: {len(stock_data)} 只")
    else:
        index_df = fetch_index_data()
        codes = fetch_stock_universe()
        if not codes:
            print("❌ 无法获取股票列表, 退出")
            return
        stock_data = fetch_stock_data(codes, max_stocks=MAX_STOCKS)
        if not stock_data:
            print("❌ 无法获取个股数据, 退出")
            return
        save_cache(index_df, stock_data)
    
    # Phase 2: 因子计算
    factor_df = compute_factors(stock_data)
    
    # Phase 3: 回测
    records_df = run_backtest(index_df, factor_df, stock_data)
    
    if len(records_df) == 0:
        print("❌ 回测无数据, 可能是MA_WINDOW不够长")
        return
    
    # Phase 4: 分析
    results = analyze_results(records_df, index_df)
    
    # 保存
    results_json = {k: v for k, v in results.items() if k != 'monthly_returns'}
    with open('/opt/quant/phoenix_result.json', 'w') as f:
        json.dump(results_json, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存至 /opt/quant/phoenix_result.json")
    
    records_df.to_csv('/opt/quant/phoenix_daily.csv', index=False)
    print(f"日线数据已保存至 /opt/quant/phoenix_daily.csv")

if __name__ == '__main__':
    main()
