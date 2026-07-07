"""
Phoenix v17 — 趋势动量策略
目标：在T+1约束下吃到趋势行情（如科技板块），不依赖日频均线择时

核心创新：
1. 不用均线择时（v12已证明T+1下结构性失效）
2. 纯选股：动量+趋势因子，选上涨趋势强的股票
3. 仓位管理替代择时：月频市场状态判断（月线趋势），决定仓位比例
4. 月度调仓，T+1执行（信号日次日出）
5. 个股止损8%安全网

因子设计：
- mom_3m: 3个月动量（追中期趋势）
- mom_1m: 1个月动量（追短期爆发）
- trend_strength: 价格相对MA60的偏离度+MA60斜率（衡量趋势强度）
- rev_1w: 1周反转（短期超买卖出信号，负向因子）
- low_vol: 3个月波动率（控制风险，但不作为主因子）

策略变体扫描：
- 纯动量 vs 动量+防御 vs 动量+趋势强度
- TOP 10/20/30/50
- 有无止损 / 有无市场状态仓位管理
- 调仓频率：月度 vs 双周
"""

import pickle, pandas as pd, numpy as np, json
from datetime import datetime, timedelta

# ========== 加载数据 ==========
with open('/tmp/phoenix_alla_data.pkl', 'rb') as f:
    data = pickle.load(f)

stocks = data['stocks']
index_df = data['index'].copy()
index_df['date'] = pd.to_datetime(index_df['date'])
index_df = index_df.sort_values('date').reset_index(drop=True)

# 预处理股票数据
for code in stocks:
    df = stocks[code].copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    stocks[code] = df

# 交易日列表
trading_dates = index_df['date'].tolist()
print(f"数据: {len(stocks)}只股票, {len(trading_dates)}个交易日")
print(f"时间: {trading_dates[0].date()} ~ {trading_dates[-1].date()}")

# ========== 因子计算 ==========
def calc_factors(df):
    """计算单只股票的因子"""
    df = df.copy()
    close = df['close'].values
    
    if len(df) < 120:
        return None
    
    factors = pd.DataFrame(index=df['date'])
    factors['code'] = df['code'].iloc[0]
    
    # 3个月动量 (约60个交易日)
    factors['mom_3m'] = df['close'] / df['close'].shift(60) - 1
    
    # 1个月动量 (约20个交易日)
    factors['mom_1m'] = df['close'] / df['close'].shift(20) - 1
    
    # 1周动量 (5个交易日)
    factors['mom_1w'] = df['close'] / df['close'].shift(5) - 1
    
    # MA60
    ma60 = df['close'].rolling(60).mean()
    # 趋势强度: 价格在MA60之上 + MA60在上行
    ma60_slope = ma60.pct_change(20)  # MA60的20日变化率
    factors['trend_strength'] = (df['close'] / ma60 - 1) * 0.5 + ma60_slope * 0.5
    factors['above_ma60'] = (df['close'] > ma60).astype(float)
    
    # 波动率 (3个月)
    ret = df['close'].pct_change()
    factors['vol_3m'] = ret.rolling(60).std() * np.sqrt(252)
    
    # 振幅
    factors['amplitude'] = (df['high'] - df['low']) / df['close'].rolling(20).mean()
    factors['amplitude'] = factors['amplitude'].rolling(20).mean()
    
    # 流动性: 20日平均成交额
    factors['amount_20d'] = df['amount'].rolling(20).mean()
    
    # 日均成交额(用于流动性过滤)
    
    return factors

# 计算所有股票因子
print("\n计算因子...")
all_factors = {}
for code, df in stocks.items():
    f = calc_factors(df)
    if f is not None:
        all_factors[code] = f

print(f"因子计算完成: {len(all_factors)}只股票")

# ========== 回测引擎 ==========
BUY_COST = 0.00126   # 买入0.126%
SELL_COST = 0.00176  # 卖出0.176%
LIQ_THRESHOLD = 50e6  # 流动性过滤: 日均成交额>5000万

def run_backtest(
    factor_names,      # 因子列表
    weights,           # 因子权重
    top_n,             # 选股数量
    use_stop_loss=True,
    stop_loss_pct=0.08,
    use_market_filter=True,  # 市场状态仓位管理
    rebalance_freq='monthly',  # 'monthly' or 'biweekly'
    label='strategy'
):
    """
    月度/双周调仓回测，T+1执行
    信号日 = 调仓日
    执行日 = 调仓日 + 1个交易日 (T+1)
    """
    
    # 确定调仓日
    if rebalance_freq == 'monthly':
        # 每月第一个交易日调仓
        rebalance_days = []
        prev_month = None
        for i, d in enumerate(trading_dates):
            m = (d.year, d.month)
            if m != prev_month:
                rebalance_days.append(i)
                prev_month = m
    else:  # biweekly
        rebalance_days = list(range(0, len(trading_dates), 10))
    
    # 市场状态: 月线趋势（月度收益>0 且 价格在MA120之上）
    index_close = index_df['close'].values
    ma120 = pd.Series(index_close).rolling(120).mean().values
    
    portfolio = []  # list of (date, nav)
    nav = 1.0
    holdings = {}  # {code: (buy_price, shares, buy_date_idx)}
    cash = 1.0
    
    rebalance_set = set(rebalance_days)
    
    for i in range(120, len(trading_dates)):
        current_date = trading_dates[i]
        current_price_idx = i
        
        # === 止损检查 (每日) ===
        if use_stop_loss and holdings:
            to_sell = []
            for code, (buy_price, shares, buy_idx) in list(holdings.items()):
                if code not in stocks:
                    to_sell.append(code)
                    continue
                current_price = stocks[code].iloc[i]['close']
                if current_price / buy_price - 1 < -stop_loss_pct:
                    to_sell.append(code)
            
            for code in to_sell:
                buy_price, shares, buy_idx = holdings[code]
                if code in stocks:
                    sell_price = stocks[code].iloc[i+1]['close'] if i+1 < len(trading_dates) else stocks[code].iloc[i]['close']
                else:
                    sell_price = buy_price * (1 - stop_loss_pct)
                proceeds = shares * sell_price * (1 - SELL_COST)
                cash += proceeds
                del holdings[code]
        
        # === 调仓 ===
        if i in rebalance_set:
            # 计算信号 (用第i天的数据)
            signal_data = []
            for code, fdf in all_factors.items():
                if i >= len(fdf):
                    continue
                row = fdf.iloc[i]
                
                # 流动性过滤
                if pd.isna(row.get('amount_20d')) or row['amount_20d'] < LIQ_THRESHOLD:
                    continue
                
                # 计算综合得分
                score = 0
                valid = True
                for fname, fw in zip(factor_names, weights):
                    val = row.get(fname)
                    if pd.isna(val):
                        valid = False
                        break
                    score += val * fw
                
                if valid:
                    signal_data.append((code, score, row))
            
            if not signal_data:
                portfolio.append((current_date, nav))
                continue
            
            # 横截面排序
            signal_df = pd.DataFrame(signal_data, columns=['code', 'score', 'factors'])
            
            # 市场状态仓位管理
            position_ratio = 1.0
            if use_market_filter:
                if i < len(ma120) and not np.isnan(ma120[i]):
                    if index_close[i] > ma120[i]:
                        position_ratio = 1.0  # 满仓
                    else:
                        position_ratio = 0.5  # 半仓
                else:
                    position_ratio = 0.5
            
            # 选TOP N
            signal_df = signal_df.sort_values('score', ascending=False)
            selected = signal_df.head(top_n)['code'].tolist()
            
            # === 执行 (T+1: 用第i+1天的开盘价) ===
            if i + 1 >= len(trading_dates):
                portfolio.append((current_date, nav))
                continue
            
            exec_idx = i + 1
            
            # 先卖出不在选中的
            to_sell = [c for c in holdings if c not in selected]
            for code in to_sell:
                buy_price, shares, buy_idx = holdings[code]
                if code in stocks and exec_idx < len(stocks[code]):
                    sell_price = stocks[code].iloc[exec_idx]['open']
                else:
                    sell_price = buy_price
                proceeds = shares * sell_price * (1 - SELL_COST)
                cash += proceeds
                del holdings[code]
            
            # 计算当前总资产
            total_asset = cash
            for code, (buy_price, shares, buy_idx) in holdings.items():
                if code in stocks and exec_idx < len(stocks[code]):
                    total_asset += shares * stocks[code].iloc[exec_idx]['open']
            
            # 买入新选中的
            target_invest = total_asset * position_ratio
            n_new = len([c for c in selected if c not in holdings])
            if n_new > 0:
                per_stock = target_invest / top_n  # 等权分配到TOP N
                
                for code in selected:
                    if code not in holdings and code in stocks and exec_idx < len(stocks[code]):
                        buy_price = stocks[code].iloc[exec_idx]['open']
                        if buy_price > 0 and not np.isnan(buy_price):
                            shares = per_stock / buy_price / (1 + BUY_COST)
                            cost = shares * buy_price * (1 + BUY_COST)
                            if cost <= cash:
                                cash -= cost
                                holdings[code] = (buy_price, shares, exec_idx)
            
            # 重新计算NAV
            nav = cash
            for code, (buy_price, shares, buy_idx) in holdings.items():
                if code in stocks and exec_idx < len(stocks[code]):
                    nav += shares * stocks[code].iloc[exec_idx]['close']
        
        # 每日记录NAV
        if i not in rebalance_set and holdings:
            daily_nav = cash
            for code, (buy_price, shares, buy_idx) in holdings.items():
                if code in stocks and i < len(stocks[code]):
                    daily_nav += shares * stocks[code].iloc[i]['close']
            nav = daily_nav
        
        portfolio.append((current_date, nav))
    
    # 转换结果
    pf = pd.DataFrame(portfolio, columns=['date', 'nav'])
    pf = pf.drop_duplicates(subset='date', keep='last').reset_index(drop=True)
    
    # 计算指标
    pf['ret'] = pf['nav'].pct_change()
    
    total_days = len(pf)
    years = total_days / 252
    
    total_return = pf['nav'].iloc[-1] / pf['nav'].iloc[0] - 1
    ann_return = (1 + total_return) ** (1/years) - 1 if years > 0 else 0
    
    # 最大回撤
    pf['peak'] = pf['nav'].cummax()
    pf['dd'] = pf['nav'] / pf['peak'] - 1
    max_dd = pf['dd'].min()
    
    # 月胜率
    pf['month'] = pf['date'].dt.to_period('M')
    monthly_ret = pf.groupby('month')['nav'].agg(lambda x: x.iloc[-1]/x.iloc[0] - 1)
    monthly_ret = monthly_ret[monthly_ret != 0]
    win_rate = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0
    
    # 夏普
    daily_ret = pf['ret'].dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    
    # Calmar
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0
    
    # 创新高比例
    new_high = (pf['nav'] == pf['peak']).sum() / len(pf)
    
    result = {
        'label': label,
        'ann_return': round(ann_return * 100, 1),
        'max_dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'win_rate': round(win_rate * 100, 1),
        'new_high': round(new_high * 100, 1),
        'total_return': round(total_return * 100, 1),
        'years': round(years, 1),
    }
    
    return result, pf

# ========== 策略变体扫描 ==========

configs = [
    # 1. 纯动量 (3个月动量)
    {
        'factor_names': ['mom_3m'],
        'weights': [1.0],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '纯动量3m TOP20 +止损 +市场过滤'
    },
    # 2. 纯动量 TOP10
    {
        'factor_names': ['mom_3m'],
        'weights': [1.0],
        'top_n': 10,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '纯动量3m TOP10 +止损 +市场过滤'
    },
    # 3. 短期动量 (1个月)
    {
        'factor_names': ['mom_1m'],
        'weights': [1.0],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '动量1m TOP20 +止损 +市场过滤'
    },
    # 4. 趋势强度
    {
        'factor_names': ['trend_strength'],
        'weights': [1.0],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '趋势强度 TOP20 +止损 +市场过滤'
    },
    # 5. 动量+趋势强度 (进攻型)
    {
        'factor_names': ['mom_3m', 'trend_strength'],
        'weights': [0.6, 0.4],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '动量3m+趋势强度 TOP20 +止损 +市场过滤'
    },
    # 6. 动量+趋势+低波 (攻守兼备)
    {
        'factor_names': ['mom_3m', 'trend_strength', 'vol_3m'],
        'weights': [0.5, 0.3, -0.2],  # 波动率负权重(低波优先)
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '动量+趋势+低波 TOP20 +止损 +市场过滤'
    },
    # 7. 动量+趋势 TOP30
    {
        'factor_names': ['mom_3m', 'trend_strength'],
        'weights': [0.6, 0.4],
        'top_n': 30,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '动量+趋势 TOP30 +止损 +市场过滤'
    },
    # 8. 动量+趋势 无止损 (看止损影响)
    {
        'factor_names': ['mom_3m', 'trend_strength'],
        'weights': [0.6, 0.4],
        'top_n': 20,
        'use_stop_loss': False,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '动量+趋势 TOP20 无止损 +市场过滤'
    },
    # 9. 动量+趋势 无市场过滤
    {
        'factor_names': ['mom_3m', 'trend_strength'],
        'weights': [0.6, 0.4],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': False,
        'rebalance_freq': 'monthly',
        'label': '动量+趋势 TOP20 +止损 无市场过滤'
    },
    # 10. 双周调仓
    {
        'factor_names': ['mom_3m', 'trend_strength'],
        'weights': [0.6, 0.4],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'biweekly',
        'label': '动量+趋势 TOP20 +止损 双周调仓'
    },
    # 11. 短+中期动量组合
    {
        'factor_names': ['mom_1m', 'mom_3m'],
        'weights': [0.4, 0.6],
        'top_n': 20,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '短+中动量 TOP20 +止损 +市场过滤'
    },
    # 12. 纯趋势强度 TOP10 (集中进攻)
    {
        'factor_names': ['trend_strength'],
        'weights': [1.0],
        'top_n': 10,
        'use_stop_loss': True,
        'stop_loss_pct': 0.08,
        'use_market_filter': True,
        'rebalance_freq': 'monthly',
        'label': '趋势强度 TOP10 +止损 +市场过滤'
    },
]

print(f"\n{'='*80}")
print(f"Phoenix v17 趋势动量策略扫描 ({len(configs)}个配置)")
print(f"{'='*80}")
print(f"{'配置':<42} {'年化':>7} {'回撤':>7} {'夏普':>5} {'月胜率':>6} {'新高':>6}")
print(f"{'-'*80}")

results = []
for cfg in configs:
    try:
        result, _ = run_backtest(**cfg)
        results.append(result)
        print(f"{result['label']:<42} {result['ann_return']:>6.1f}% {result['max_dd']:>6.1f}% {result['sharpe']:>5.2f} {result['win_rate']:>5.1f}% {result['new_high']:>5.1f}%")
    except Exception as e:
        print(f"{cfg['label']:<42} ERROR: {str(e)[:40]}")

# 保存结果
with open('/opt/quant/phoenix_v17_result.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*80}")
print("目标: 年化>30% | 回撤<10% | 月胜率>75% | 夏普>2.0")
print(f"{'='*80}")

# 找最优
best = max(results, key=lambda x: x['sharpe'])
print(f"\n最优(夏普): {best['label']}")
print(f"  年化={best['ann_return']}% 回撤={best['max_dd']}% 夏普={best['sharpe']} 月胜率={best['win_rate']}%")

best_ret = max(results, key=lambda x: x['ann_return'])
print(f"最高收益: {best_ret['label']}")
print(f"  年化={best_ret['ann_return']}% 回撤={best_ret['max_dd']}% 夏普={best_ret['sharpe']} 月胜率={best_ret['win_rate']}%")

best_dd = min(results, key=lambda x: abs(x['max_dd']))
print(f"最低回撤: {best_dd['label']}")
print(f"  年化={best_dd['ann_return']}% 回撤={best_dd['max_dd']}% 夏普={best_dd['sharpe']} 月胜率={best_dd['win_rate']}%")
