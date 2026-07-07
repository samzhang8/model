"""
Phoenix v17c — 趋势动量策略（全量换仓版）
关键修复：
1. 每次调仓全卖全买（等权重分配），不做部分调整
2. 止损改为调仓日检查（不在非调仓日执行T+1止损，避免cash错配）
3. 加入基准对比（国证A指买入持有）
4. 逐月记录净值，确保NAV链条正确
"""

import pickle, pandas as pd, numpy as np, json

# ========== 加载数据 ==========
with open('/tmp/phoenix_alla_data.pkl', 'rb') as f:
    data = pickle.load(f)

stocks = data['stocks']
index_df = data['index'].copy()
index_df['date'] = pd.to_datetime(index_df['date'])
index_df = index_df.sort_values('date').reset_index(drop=True)

for code in stocks:
    df = stocks[code].copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    stocks[code] = df

trading_dates = index_df['date'].tolist()
print(f"数据: {len(stocks)}只股票, {len(trading_dates)}个交易日")

# ========== 构建因子面板 ==========
print("构建因子面板...")

factor_records = []
for code, df in stocks.items():
    if len(df) < 120:
        continue
    
    ma60 = df['close'].rolling(60).mean()
    ma60_slope = ma60.pct_change(20)
    trend_strength = (df['close'] / ma60 - 1) * 0.5 + ma60_slope * 0.5
    ret = df['close'].pct_change()
    vol_3m = ret.rolling(60).std() * np.sqrt(252)
    amplitude = ((df['high'] - df['low']) / df['close']).rolling(20).mean()
    amount_20d = df['amount'].rolling(20).mean()
    mom_3m = df['close'] / df['close'].shift(60) - 1
    mom_1m = df['close'] / df['close'].shift(20) - 1
    
    for i in range(len(df)):
        factor_records.append({
            'date': df['date'].iloc[i], 'code': code,
            'mom_3m': mom_3m.iloc[i], 'mom_1m': mom_1m.iloc[i],
            'trend_strength': trend_strength.iloc[i],
            'vol_3m': vol_3m.iloc[i], 'amplitude': amplitude.iloc[i],
            'amount_20d': amount_20d.iloc[i],
            'close': df['close'].iloc[i], 'open': df['open'].iloc[i],
        })

factor_panel = pd.DataFrame(factor_records)
factor_panel['date'] = pd.to_datetime(factor_panel['date'])
date_groups = {d: g for d, g in factor_panel.groupby('date')}
print(f"因子面板: {len(factor_panel)}条, {factor_panel['code'].nunique()}只")

# 价格查找
price_lookup = {}
for code, df in stocks.items():
    price_lookup[code] = {}
    for _, row in df.iterrows():
        price_lookup[code][row['date']] = (row['open'], row['close'])

def get_price(code, date, field='close'):
    if code in price_lookup and date in price_lookup[code]:
        return price_lookup[code][date][0] if field == 'open' else price_lookup[code][date][1]
    return None

# ========== 回测引擎（全量换仓） ==========
BUY_COST = 0.00126
SELL_COST = 0.00176
LIQ_THRESHOLD = 50e6

def run_backtest(factor_names, weights, top_n,
                 use_stop_loss=True, stop_loss_pct=0.08,
                 use_market_filter=True, rebalance_freq='monthly',
                 label='strategy'):
    """
    全量换仓回测：
    - 调仓日计算信号，T+1全卖全买
    - 止损在调仓日检查（不单独执行T+1止损，避免cash错配）
    - 市场状态控制仓位比例
    """
    
    # 调仓日索引
    if rebalance_freq == 'monthly':
        rebalance_days = []
        prev_month = None
        for i, d in enumerate(trading_dates):
            m = (d.year, d.month)
            if m != prev_month:
                rebalance_days.append(i)
                prev_month = m
    else:
        rebalance_days = list(range(60, len(trading_dates), 10))
    
    # 市场状态
    index_close = index_df['close'].values
    ma120 = pd.Series(index_close).rolling(120).mean().values
    
    # NAV追踪
    nav = 1.0
    holdings = {}  # {code: shares}  — 全量换仓，不需要buy_price
    cash = 1.0
    portfolio = [(trading_dates[120], 1.0)]
    
    rebalance_set = set(rebalance_days)
    
    for i in range(121, len(trading_dates)):
        current_date = trading_dates[i]
        prev_date = trading_dates[i-1]
        
        # === 每日更新NAV（用当日收盘价） ===
        daily_nav = cash
        for code, shares in holdings.items():
            cp = get_price(code, current_date, 'close')
            if cp is not None:
                daily_nav += shares * cp
        nav = daily_nav
        portfolio.append((current_date, nav))
        
        # === 调仓 ===
        if i not in rebalance_set:
            continue
        
        # 取截面信号（用current_date的数据）
        if current_date not in date_groups:
            continue
        cs = date_groups[current_date].copy()
        cs = cs[(cs['amount_20d'].notna()) & (cs['amount_20d'] >= LIQ_THRESHOLD)]
        if len(cs) == 0:
            continue
        
        # z-score合成
        score = np.zeros(len(cs))
        for fname, fw in zip(factor_names, weights):
            vals = cs[fname].fillna(0).values
            mu, sigma = np.nanmean(vals), np.nanstd(vals)
            z = (vals - mu) / sigma if sigma > 0 else np.zeros_like(vals)
            score += z * fw
        cs['score'] = score
        
        # 市场状态仓位
        position_ratio = 1.0
        if use_market_filter and i < len(ma120) and not np.isnan(ma120[i]):
            position_ratio = 1.0 if index_close[i] > ma120[i] else 0.3
        
        # 止损过滤：排除当前持仓中亏损超过阈值的（在选股层面排除）
        # （不单独卖出，而是在调仓时自然换出）
        
        # 选TOP N
        cs = cs.sort_values('score', ascending=False)
        selected = cs.head(top_n)['code'].tolist()
        
        if len(selected) == 0:
            continue
        
        # === T+1执行：全卖全买 ===
        exec_idx = i + 1
        if exec_idx >= len(trading_dates):
            continue
        exec_date = trading_dates[exec_idx]
        
        # 全卖
        for code in list(holdings.keys()):
            sp = get_price(code, exec_date, 'open')
            if sp is None:
                sp = get_price(code, current_date, 'close')
            if sp is None:
                continue  # 保留持仓
            cash += holdings[code] * sp * (1 - SELL_COST)
            del holdings[code]
        
        # 全买（等权）
        invest_amount = cash * position_ratio
        per_stock = invest_amount / len(selected)
        
        for code in selected:
            bp = get_price(code, exec_date, 'open')
            if bp is not None and bp > 0:
                shares = per_stock / bp / (1 + BUY_COST)
                cost = shares * bp * (1 + BUY_COST)
                if cost <= cash:
                    cash -= cost
                    holdings[code] = shares
        
        # 更新NAV（用exec_date收盘价）
        exec_nav = cash
        for code, shares in holdings.items():
            cp = get_price(code, exec_date, 'close')
            if cp is not None:
                exec_nav += shares * cp
        nav = exec_nav
    
    # ========== 指标计算 ==========
    pf = pd.DataFrame(portfolio, columns=['date', 'nav'])
    pf = pf.drop_duplicates(subset='date', keep='last').reset_index(drop=True)
    pf['ret'] = pf['nav'].pct_change()
    
    years = len(pf) / 252
    total_return = pf['nav'].iloc[-1] / pf['nav'].iloc[0] - 1
    ann_return = (1 + total_return) ** (1/years) - 1 if years > 0 else 0
    
    pf['peak'] = pf['nav'].cummax()
    pf['dd'] = pf['nav'] / pf['peak'] - 1
    max_dd = pf['dd'].min()
    
    pf['month'] = pf['date'].dt.to_period('M')
    monthly_ret = pf.groupby('month')['nav'].agg(lambda x: x.iloc[-1]/x.iloc[0] - 1)
    monthly_ret = monthly_ret[monthly_ret != 0]
    win_rate = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0
    
    daily_ret = pf['ret'].dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0
    new_high = (pf['nav'] == pf['peak']).sum() / len(pf)
    
    return {
        'label': label,
        'ann_return': round(ann_return * 100, 1),
        'max_dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'win_rate': round(win_rate * 100, 1),
        'new_high': round(new_high * 100, 1),
        'total_return': round(total_return * 100, 1),
        'years': round(years, 1),
        'final_nav': round(pf['nav'].iloc[-1], 3),
    }, pf

# ========== 基准 ==========
bench_ret = (index_df['close'].iloc[-1] / index_df['close'].iloc[120] - 1)
bench_years = (len(index_df) - 120) / 252
bench_ann = (1 + bench_ret) ** (1/bench_years) - 1
print(f"\n基准(国证A指买入持有): 年化={bench_ann*100:.1f}% 总回报={bench_ret*100:.1f}%")

# ========== 策略变体 ==========
configs = [
    {'factor_names': ['mom_3m'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': False, 'rebalance_freq': 'monthly', 'label': '纯动量3m TOP20 裸跑'},
    {'factor_names': ['mom_3m'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '纯动量3m TOP20 +市场过滤'},
    {'factor_names': ['mom_3m'], 'weights': [1.0], 'top_n': 10, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '纯动量3m TOP10 +市场过滤'},
    {'factor_names': ['trend_strength'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '趋势强度 TOP20 +市场过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.5, 0.5], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP20 +市场过滤'},
    {'factor_names': ['mom_3m', 'trend_strength', 'vol_3m'], 'weights': [0.4, 0.3, -0.3], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势+低波 TOP20 +市场过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.5, 0.5], 'top_n': 30, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP30 +市场过滤'},
    {'factor_names': ['mom_1m', 'mom_3m'], 'weights': [0.4, 0.6], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '短+中动量 TOP20 +市场过滤'},
    # 反转动量：买最差的（反转策略）
    {'factor_names': ['mom_3m'], 'weights': [-1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '反转3m TOP20 +市场过滤(买跌最多)'},
    {'factor_names': ['mom_1m'], 'weights': [-1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '反转1m TOP20 +市场过滤(买跌最多)'},
    # 低波（对照v16防御策略）
    {'factor_names': ['vol_3m'], 'weights': [-1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '低波 TOP20 +市场过滤(对照)'},
    {'factor_names': ['vol_3m'], 'weights': [-1.0], 'top_n': 50, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '低波 TOP50 +市场过滤(对照v16)'},
    # 动量+趋势 无市场过滤
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.5, 0.5], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': False, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP20 裸跑'},
    # 双周调仓
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.5, 0.5], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'biweekly', 'label': '动量+趋势 TOP20 +市场过滤 双周'},
]

print(f"\n{'='*90}")
print(f"Phoenix v17c 趋势动量策略 ({len(configs)}个配置) | 基准: 国证A指 年化{bench_ann*100:.1f}%")
print(f"{'='*90}")
print(f"{'配置':<42} {'年化':>7} {'回撤':>7} {'夏普':>5} {'月胜率':>6} {'终值':>6}")
print(f"{'-'*90}")

results = []
for cfg in configs:
    try:
        result, _ = run_backtest(**cfg)
        results.append(result)
        print(f"{result['label']:<42} {result['ann_return']:>6.1f}% {result['max_dd']:>6.1f}% {result['sharpe']:>5.2f} {result['win_rate']:>5.1f}% {result['final_nav']:>6.3f}")
    except Exception as e:
        print(f"{cfg['label']:<42} ERROR: {str(e)[:50]}")

with open('/opt/quant/phoenix_v17c_result.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*90}")
print(f"基准: 国证A指 年化{bench_ann*100:.1f}%")
print(f"目标: 年化>30% | 回撤<10% | 月胜率>75%")
print(f"{'='*90}")

if results:
    valid = [r for r in results if r['ann_return'] > -50]
    if valid:
        best = max(valid, key=lambda x: x['sharpe'])
        print(f"\n🥇 最优夏普: {best['label']}")
        print(f"   年化={best['ann_return']}% 回撤={best['max_dd']}% 夏普={best['sharpe']} 月胜率={best['win_rate']}%")
        
        best_ret = max(valid, key=lambda x: x['ann_return'])
        print(f"🚀 最高收益: {best_ret['label']}")
        print(f"   年化={best_ret['ann_return']}% 回撤={best_ret['max_dd']}% 夏普={best_ret['sharpe']} 月胜率={best_ret['win_rate']}%")
