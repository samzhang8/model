"""
Phoenix v18 — 终极整合版
整合v12-v17所有发现：
1. 低波因子选股（v16/v17c确认最稳）
2. 反转因子增强（v17c确认A股动量反向，反转正向）
3. 月频MA250择时（v16确认有效，不涉及日频翻转，T+1只损失1天）
4. 个股止损8%（v16确认有效）
5. 部分换仓（只换出排名跌出TOP 2N的，降低交易成本）
6. T+1执行 + 交易成本 + 流动性过滤
7. 对照基准

目标：在真实条件下逼近年化15-20%，回撤<15%，月胜率>70%
"""

import pickle, pandas as pd, numpy as np, json

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
print(f"数据: {len(stocks)}只, {len(trading_dates)}天")

# ========== 因子面板 ==========
print("构建因子面板...")
factor_records = []
for code, df in stocks.items():
    if len(df) < 250:
        continue
    
    ret = df['close'].pct_change()
    vol_3m = ret.rolling(60).std() * np.sqrt(252)
    vol_1m = ret.rolling(20).std() * np.sqrt(252)
    mom_3m = df['close'] / df['close'].shift(60) - 1
    mom_1m = df['close'] / df['close'].shift(20) - 1
    rev_1w = -(df['close'] / df['close'].shift(5) - 1)  # 反转：1周跌的买
    rev_1m = -(df['close'] / df['close'].shift(20) - 1)  # 反转：1月跌的买
    amplitude = ((df['high'] - df['low']) / df['close']).rolling(20).mean()
    amount_20d = df['amount'].rolling(20).mean()
    
    # MA250
    ma250 = df['close'].rolling(250).mean()
    above_ma250 = (df['close'] > ma250).astype(float)
    
    # beta vs 指数
    # 简单beta: 个股60日收益 / 指数60日收益
    
    for i in range(len(df)):
        factor_records.append({
            'date': df['date'].iloc[i], 'code': code,
            'vol_3m': vol_3m.iloc[i], 'vol_1m': vol_1m.iloc[i],
            'mom_3m': mom_3m.iloc[i], 'mom_1m': mom_1m.iloc[i],
            'rev_1w': rev_1w.iloc[i], 'rev_1m': rev_1m.iloc[i],
            'amplitude': amplitude.iloc[i], 'amount_20d': amount_20d.iloc[i],
            'above_ma250': above_ma250.iloc[i],
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

# ========== 回测引擎 ==========
BUY_COST = 0.00126
SELL_COST = 0.00176
LIQ_THRESHOLD = 50e6

def run_backtest(factor_names, weights, top_n,
                 use_ma250_filter=True,    # 月频MA250择时
                 use_stop_loss=True, stop_loss_pct=0.08,
                 partial_rebalance=True,   # 部分换仓（只换出TOP 2N的）
                 rebalance_freq='monthly',
                 label='strategy'):
    
    # 调仓日
    if rebalance_freq == 'monthly':
        rebalance_days = []
        prev_month = None
        for i, d in enumerate(trading_dates):
            m = (d.year, d.month)
            if m != prev_month:
                rebalance_days.append(i)
                prev_month = m
    elif rebalance_freq == 'quarterly':
        rebalance_days = []
        prev_q = None
        for i, d in enumerate(trading_dates):
            q = (d.year, (d.month-1)//3)
            if q != prev_q:
                rebalance_days.append(i)
                prev_q = q
    else:
        rebalance_days = list(range(250, len(trading_dates), 10))
    
    rebalance_set = set(rebalance_days)
    
    # 指数MA250
    index_close = index_df['close'].values
    index_ma250 = pd.Series(index_close).rolling(250).mean().values
    
    nav = 1.0
    holdings = {}  # {code: (shares, buy_price)}
    cash = 1.0
    portfolio = [(trading_dates[250], 1.0)]
    
    for i in range(251, len(trading_dates)):
        current_date = trading_dates[i]
        
        # 每日NAV
        daily_nav = cash
        for code, (shares, bp) in holdings.items():
            cp = get_price(code, current_date, 'close')
            if cp is not None:
                daily_nav += shares * cp
        nav = daily_nav
        portfolio.append((current_date, nav))
        
        # === 调仓 ===
        if i not in rebalance_set:
            continue
        
        if current_date not in date_groups:
            continue
        cs = date_groups[current_date].copy()
        cs = cs[(cs['amount_20d'].notna()) & (cs['amount_20d'] >= LIQ_THRESHOLD)]
        
        # MA250个股过滤：只选价格在MA250之上的
        if use_ma250_filter:
            cs = cs[cs['above_ma250'] == 1.0]
        
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
        cs = cs.sort_values('score', ascending=False)
        
        # 市场状态仓位（月频MA250）
        position_ratio = 1.0
        if use_ma250_filter and i < len(index_ma250) and not np.isnan(index_ma250[i]):
            position_ratio = 1.0 if index_close[i] > index_ma250[i] else 0.3
        
        # 选股
        selected = cs.head(top_n)['code'].tolist()
        if len(selected) == 0:
            continue
        
        # 部分换仓：保留在TOP 2N内的已有持仓
        if partial_rebalance:
            keep_threshold = top_n * 2
            keep_set = set(cs.head(keep_threshold)['code'].tolist())
            # 保留在keep_set内的持仓，卖出不在的
            to_sell = [c for c in holdings if c not in keep_set]
        else:
            to_sell = [c for c in holdings if c not in selected]
        
        # 止损：检查已有持仓
        if use_stop_loss:
            for code in list(holdings.keys()):
                if code in to_sell:
                    continue
                shares, bp = holdings[code]
                cp = get_price(code, current_date, 'close')
                if cp is not None and cp / bp - 1 < -stop_loss_pct:
                    to_sell.append(code)
        
        # === T+1执行 ===
        exec_idx = i + 1
        if exec_idx >= len(trading_dates):
            continue
        exec_date = trading_dates[exec_idx]
        
        # 卖出
        for code in to_sell:
            if code not in holdings:
                continue
            shares, bp = holdings[code]
            sp = get_price(code, exec_date, 'open')
            if sp is None:
                sp = get_price(code, current_date, 'close')
            if sp is None:
                continue
            cash += shares * sp * (1 - SELL_COST)
            del holdings[code]
        
        # 买入新选中的
        total_asset = cash
        for code, (shares, bp) in holdings.items():
            cp = get_price(code, exec_date, 'open')
            if cp is not None:
                total_asset += shares * cp
        
        target_invest = total_asset * position_ratio
        n_new = len([c for c in selected if c not in holdings])
        if n_new > 0:
            per_stock = target_invest / top_n  # 等权到TOP N
            for code in selected:
                if code not in holdings:
                    bp = get_price(code, exec_date, 'open')
                    if bp is not None and bp > 0 and per_stock <= cash:
                        shares = per_stock / bp / (1 + BUY_COST)
                        cost = shares * bp * (1 + BUY_COST)
                        cash -= cost
                        holdings[code] = (shares, bp)
        
        # 更新NAV
        exec_nav = cash
        for code, (shares, bp) in holdings.items():
            cp = get_price(code, exec_date, 'close')
            if cp is not None:
                exec_nav += shares * cp
        nav = exec_nav
    
    # ========== 指标 ==========
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
    
    # 年度收益
    pf['year'] = pf['date'].dt.year
    yearly_ret = pf.groupby('year')['nav'].agg(lambda x: x.iloc[-1]/x.iloc[0] - 1)
    
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
        'yearly': {str(y): round(r*100, 1) for y, r in yearly_ret.items()},
    }, pf

# ========== 基准 ==========
bench_ret = (index_df['close'].iloc[-1] / index_df['close'].iloc[250] - 1)
bench_years = (len(index_df) - 250) / 252
bench_ann = (1 + bench_ret) ** (1/bench_years) - 1
print(f"基准(国证A指买入持有): 年化={bench_ann*100:.1f}%")

# ========== 配置 ==========
configs = [
    # 核心策略：低波+反转+MA250+止损
    {'factor_names': ['vol_3m'], 'weights': [-1.0], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波TOP50+MA250+止损8%(=v16基线)'},
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP50+MA250+止损8%'},
    {'factor_names': ['vol_3m', 'rev_1w'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1w TOP50+MA250+止损8%'},
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.5, 0.5], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m(等权) TOP50+MA250+止损8%'},
    {'factor_names': ['vol_3m', 'rev_1m', 'amplitude'], 'weights': [-0.4, 0.4, -0.2], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转+低振幅 TOP50+MA250+止损8%'},
    # TOP N变化
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 30, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP30+MA250+止损8%'},
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 20, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP20+MA250+止损8%'},
    # 无止损对照
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': False, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP50+MA250无止损'},
    # 无MA250对照
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': False, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP50无MA250+止损8%'},
    # 季度调仓
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'quarterly', 'label': '低波+反转1m TOP50+MA250+止损8% 季度调仓'},
    # 全量换仓对照
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': False, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP50+MA250+止损8% 全量换仓'},
    # 纯反转
    {'factor_names': ['rev_1m'], 'weights': [1.0], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '纯反转1m TOP50+MA250+止损8%'},
    # 低波+反转1m+反转1w
    {'factor_names': ['vol_3m', 'rev_1m', 'rev_1w'], 'weights': [-0.4, 0.3, 0.3], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m+反转1w TOP50+MA250+止损8%'},
    # 止损5% vs 10%
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'stop_loss_pct': 0.05, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP50+MA250+止损5%'},
    {'factor_names': ['vol_3m', 'rev_1m'], 'weights': [-0.6, 0.4], 'top_n': 50, 'use_ma250_filter': True, 'use_stop_loss': True, 'stop_loss_pct': 0.10, 'partial_rebalance': True, 'rebalance_freq': 'monthly', 'label': '低波+反转1m TOP50+MA250+止损10%'},
]

print(f"\n{'='*95}")
print(f"Phoenix v18 终极整合版 ({len(configs)}个配置) | 基准: 国证A指 年化{bench_ann*100:.1f}%")
print(f"{'='*95}")
print(f"{'配置':<46} {'年化':>6} {'回撤':>6} {'夏普':>5} {'Calmar':>6} {'月胜率':>5} {'终值':>6}")
print(f"{'-'*95}")

results = []
nav_curves = {}
for cfg in configs:
    try:
        result, pf = run_backtest(**cfg)
        results.append(result)
        nav_curves[cfg['label']] = pf
        flag = '✅' if result['ann_return'] > 15 and result['max_dd'] > -15 else ('🟡' if result['ann_return'] > 5 else '❌')
        print(f"{flag} {result['label']:<44} {result['ann_return']:>5.1f}% {result['max_dd']:>5.1f}% {result['sharpe']:>5.2f} {result['calmar']:>6.2f} {result['win_rate']:>4.1f}% {result['final_nav']:>6.3f}")
    except Exception as e:
        print(f"❌ {cfg['label']:<44} ERROR: {str(e)[:50]}")

with open('/opt/quant/phoenix_v18_result.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*95}")
print(f"基准: 国证A指 年化{bench_ann*100:.1f}% | 目标: 年化>15% 回撤<15% 月胜率>70%")
print(f"{'='*95}")

if results:
    valid = [r for r in results if r['ann_return'] > -50]
    if valid:
        best = max(valid, key=lambda x: x['sharpe'])
        print(f"\n🥇 最优: {best['label']}")
        print(f"   年化={best['ann_return']}% 回撤={best['max_dd']}% 夏普={best['sharpe']} 月胜率={best['win_rate']}%")
        print(f"   年度: {best.get('yearly', {})}")
        
        best_ret = max(valid, key=lambda x: x['ann_return'])
        print(f"\n🚀 最高收益: {best_ret['label']}")
        print(f"   年化={best_ret['ann_return']}% 回撤={best_ret['max_dd']}% 夏普={best_ret['sharpe']}")
        print(f"   年度: {best_ret.get('yearly', {})}")
