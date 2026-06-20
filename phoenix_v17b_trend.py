"""
Phoenix v17b — 趋势动量策略（修复对齐bug）
关键修复：用日期对齐替代iloc整数索引
"""

import pickle, pandas as pd, numpy as np, json
from datetime import datetime

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
date_to_idx = {d: i for i, d in enumerate(trading_dates)}

print(f"数据: {len(stocks)}只股票, {len(trading_dates)}个交易日")

# ========== 构建横cs因子面板 ==========
print("\n构建因子面板...")

# 为每只股票计算因子，然后按日期对齐
factor_records = []

for code, df in stocks.items():
    if len(df) < 120:
        continue
    
    close = df['close'].values
    dates = df['date'].values
    
    # 因子计算
    mom_3m = df['close'] / df['close'].shift(60) - 1
    mom_1m = df['close'] / df['close'].shift(20) - 1
    mom_1w = df['close'] / df['close'].shift(5) - 1
    
    ma60 = df['close'].rolling(60).mean()
    ma60_slope = ma60.pct_change(20)
    trend_strength = (df['close'] / ma60 - 1) * 0.5 + ma60_slope * 0.5
    
    ret = df['close'].pct_change()
    vol_3m = ret.rolling(60).std() * np.sqrt(252)
    
    amplitude = ((df['high'] - df['low']) / df['close']).rolling(20).mean()
    amount_20d = df['amount'].rolling(20).mean()
    
    for i in range(len(df)):
        factor_records.append({
            'date': dates[i],
            'code': code,
            'mom_3m': mom_3m.iloc[i],
            'mom_1m': mom_1m.iloc[i],
            'mom_1w': mom_1w.iloc[i],
            'trend_strength': trend_strength.iloc[i],
            'vol_3m': vol_3m.iloc[i],
            'amplitude': amplitude.iloc[i],
            'amount_20d': amount_20d.iloc[i],
            'close': close[i],
            'open': df['open'].iloc[i],
        })

factor_panel = pd.DataFrame(factor_records)
factor_panel['date'] = pd.to_datetime(factor_panel['date'])
print(f"因子面板: {len(factor_panel)}条记录, {factor_panel['code'].nunique()}只股票")

# ========== 回测引擎 ==========
BUY_COST = 0.00126
SELL_COST = 0.00176
LIQ_THRESHOLD = 50e6

# 按日期分组，方便快速取cs
date_groups = {d: g for d, g in factor_panel.groupby('date')}

# 股票价格查找表: {code: {date: (open, close)}}
price_lookup = {}
for code, df in stocks.items():
    price_lookup[code] = {}
    for _, row in df.iterrows():
        price_lookup[code][row['date']] = (row['open'], row['close'])

def get_price(code, date, field='close'):
    """安全获取价格"""
    if code in price_lookup and date in price_lookup[code]:
        idx = 0 if field == 'open' else 1
        return price_lookup[code][date][idx]
    return None

def run_backtest(
    factor_names, weights, top_n,
    use_stop_loss=True, stop_loss_pct=0.08,
    use_market_filter=True,
    rebalance_freq='monthly',
    label='strategy'
):
    # 调仓日
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
    
    # 市场状态: MA120
    index_close = index_df['close'].values
    ma120 = pd.Series(index_close).rolling(120).mean().values
    
    portfolio = []
    nav = 1.0
    holdings = {}  # {code: (buy_price, shares)}
    cash = 1.0
    
    rebalance_set = set(rebalance_days)
    
    for i in range(120, len(trading_dates)):
        current_date = trading_dates[i]
        
        # === 每日止损检查 ===
        if use_stop_loss and holdings:
            to_sell = []
            for code in list(holdings.keys()):
                buy_price, shares = holdings[code]
                cp = get_price(code, current_date, 'close')
                if cp is None:
                    to_sell.append(code)
                    continue
                if cp / buy_price - 1 < -stop_loss_pct:
                    to_sell.append(code)
            
            for code in to_sell:
                buy_price, shares = holdings[code]
                # T+1执行: 用次日开盘价
                if i + 1 < len(trading_dates):
                    sp = get_price(code, trading_dates[i+1], 'open')
                else:
                    sp = get_price(code, current_date, 'close')
                if sp is None:
                    sp = buy_price * (1 - stop_loss_pct)
                cash += shares * sp * (1 - SELL_COST)
                del holdings[code]
        
        # === 调仓 ===
        if i in rebalance_set:
            # 取cs
            if current_date not in date_groups:
                portfolio.append((current_date, nav))
                continue
            
            cs = date_groups[current_date].copy()
            
            # 流动性过滤
            cs = cs[(cs['amount_20d'].notna()) & (cs['amount_20d'] >= LIQ_THRESHOLD)]
            
            if len(cs) == 0:
                portfolio.append((current_date, nav))
                continue
            
            # 计算综合得分
            score = np.zeros(len(cs))
            for fname, fw in zip(factor_names, weights):
                vals = cs[fname].fillna(0).values
                # 横csz-score标准化
                mu = np.nanmean(vals)
                sigma = np.nanstd(vals)
                if sigma > 0:
                    z = (vals - mu) / sigma
                else:
                    z = np.zeros_like(vals)
                score += z * fw
            
            cs['score'] = score
            
            # 市场状态仓位
            position_ratio = 1.0
            if use_market_filter and i < len(ma120) and not np.isnan(ma120[i]):
                if index_close[i] > ma120[i]:
                    position_ratio = 1.0
                else:
                    position_ratio = 0.3  # 熊市轻仓
            
            # 选TOP N
            cs = cs.sort_values('score', ascending=False)
            selected = cs.head(top_n)['code'].tolist()
            
            # === T+1执行 ===
            if i + 1 >= len(trading_dates):
                portfolio.append((current_date, nav))
                continue
            
            exec_date = trading_dates[i + 1]
            
            # 卖出不在选中的
            to_sell = [c for c in holdings if c not in selected]
            for code in to_sell:
                buy_price, shares = holdings[code]
                sp = get_price(code, exec_date, 'open')
                if sp is None:
                    sp = buy_price
                cash += shares * sp * (1 - SELL_COST)
                del holdings[code]
            
            # 计算总资产
            total_asset = cash
            for code, (bp, sh) in holdings.items():
                cp = get_price(code, exec_date, 'open')
                if cp is None:
                    cp = bp
                total_asset += sh * cp
            
            # 等权买入
            target_per_stock = (total_asset * position_ratio) / top_n
            for code in selected:
                if code not in holdings:
                    bp = get_price(code, exec_date, 'open')
                    if bp is not None and bp > 0 and target_per_stock <= cash:
                        shares = target_per_stock / bp / (1 + BUY_COST)
                        cost = shares * bp * (1 + BUY_COST)
                        cash -= cost
                        holdings[code] = (bp, shares)
            
            # 更新NAV
            nav = cash
            for code, (bp, sh) in holdings.items():
                cp = get_price(code, exec_date, 'close')
                if cp is None:
                    cp = bp
                nav += sh * cp
        
        # 每日NAV
        if i not in rebalance_set:
            daily_nav = cash
            for code, (bp, sh) in holdings.items():
                cp = get_price(code, current_date, 'close')
                if cp is None:
                    cp = bp
                daily_nav += sh * cp
            nav = daily_nav
        
        portfolio.append((current_date, nav))
    
    # 指标计算
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
    }, pf

# ========== 扫描 ==========
configs = [
    {'factor_names': ['mom_3m'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '纯动量3m TOP20+止损+过滤'},
    {'factor_names': ['mom_3m'], 'weights': [1.0], 'top_n': 10, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '纯动量3m TOP10+止损+过滤'},
    {'factor_names': ['mom_1m'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量1m TOP20+止损+过滤'},
    {'factor_names': ['trend_strength'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '趋势强度 TOP20+止损+过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.6, 0.4], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP20+止损+过滤'},
    {'factor_names': ['mom_3m', 'trend_strength', 'vol_3m'], 'weights': [0.5, 0.3, -0.2], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势+低波 TOP20+止损+过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.6, 0.4], 'top_n': 30, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP30+止损+过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.6, 0.4], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP20无止损+过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.6, 0.4], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': False, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP20+止损无过滤'},
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.6, 0.4], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'biweekly', 'label': '动量+趋势 TOP20+止损双周调仓'},
    {'factor_names': ['mom_1m', 'mom_3m'], 'weights': [0.4, 0.6], 'top_n': 20, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '短+中动量 TOP20+止损+过滤'},
    {'factor_names': ['trend_strength'], 'weights': [1.0], 'top_n': 10, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'use_market_filter': True, 'rebalance_freq': 'monthly', 'label': '趋势强度 TOP10+止损+过滤'},
    # 无市场过滤的纯动量（看是否市场过滤在伤害收益）
    {'factor_names': ['mom_3m'], 'weights': [1.0], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': False, 'rebalance_freq': 'monthly', 'label': '纯动量3m TOP20无止损无过滤'},
    # 动量+趋势 无市场过滤无止损
    {'factor_names': ['mom_3m', 'trend_strength'], 'weights': [0.6, 0.4], 'top_n': 20, 'use_stop_loss': False, 'use_market_filter': False, 'rebalance_freq': 'monthly', 'label': '动量+趋势 TOP20裸跑'},
]

print(f"\n{'='*85}")
print(f"Phoenix v17b 趋势动量策略扫描 ({len(configs)}个配置)")
print(f"{'='*85}")
print(f"{'配置':<38} {'年化':>7} {'回撤':>7} {'夏普':>5} {'Calmar':>6} {'月胜率':>6} {'新高':>6}")
print(f"{'-'*85}")

results = []
nav_curves = {}
for cfg in configs:
    try:
        result, pf = run_backtest(**cfg)
        results.append(result)
        nav_curves[cfg['label']] = pf
        print(f"{result['label']:<38} {result['ann_return']:>6.1f}% {result['max_dd']:>6.1f}% {result['sharpe']:>5.2f} {result['calmar']:>6.2f} {result['win_rate']:>5.1f}% {result['new_high']:>5.1f}%")
    except Exception as e:
        print(f"{cfg['label']:<38} ERROR: {str(e)[:50]}")

with open('/opt/quant/phoenix_v17b_result.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*85}")
print("目标: 年化>30% | 回撤<10% | 月胜率>75% | 夏普>2.0")
print(f"{'='*85}")

if results:
    best_sharpe = max(results, key=lambda x: x['sharpe'])
    best_ret = max(results, key=lambda x: x['ann_return'])
    best_dd = min(results, key=lambda x: abs(x['max_dd']))
    
    print(f"\n🥇 最优夏普: {best_sharpe['label']}")
    print(f"   年化={best_sharpe['ann_return']}% 回撤={best_sharpe['max_dd']}% 夏普={best_sharpe['sharpe']} 月胜率={best_sharpe['win_rate']}%")
    
    print(f"\n🚀 最高收益: {best_ret['label']}")
    print(f"   年化={best_ret['ann_return']}% 回撤={best_ret['max_dd']}% 夏普={best_ret['sharpe']} 月胜率={best_ret['win_rate']}%")
    
    print(f"\n🛡️ 最低回撤: {best_dd['label']}")
    print(f"   年化={best_dd['ann_return']}% 回撤={best_dd['max_dd']}% 夏普={best_dd['sharpe']} 月胜率={best_dd['win_rate']}%")
