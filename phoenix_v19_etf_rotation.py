"""
Phoenix v19 — ETF板块轮动 + 混合策略
解决ZZB核心痛点：踏空科技行情

策略架构：
1. 纯ETF轮动：月度调仓，动量/反转/趋势信号选板块ETF
2. 混合策略：70%低波选股(v18) + 30%ETF轮动
3. 对照：100%低波选股(v18基线)

关键优势：
- ETF无印花税（仅佣金~0.01%），交易成本远低于个股
- 板块轮动是月频，T+1只损失1天，不影响alpha
- 直接解决"系统性排除科技"的问题
"""

import pickle, pandas as pd, numpy as np, json
from datetime import datetime, timedelta

# ========== 加载数据 ==========
# ETF/指数数据
with open('/tmp/phoenix_sector_data.pkl', 'rb') as f:
    sector_data = pickle.load(f)

# 个股数据
with open('/tmp/phoenix_alla_data.pkl', 'rb') as f:
    stock_data = pickle.load(f)

stocks = stock_data['stocks']
index_df = stock_data['index'].copy()
index_df['date'] = pd.to_datetime(index_df['date'])
index_df = index_df.sort_values('date').reset_index(drop=True)

for code in stocks:
    df = stocks[code].copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    stocks[code] = df

trading_dates = index_df['date'].tolist()
print(f"数据: {len(stocks)}只股票, {len(trading_dates)}天, {len(sector_data)}个ETF/指数")

# ========== ETF数据对齐 ==========
# 只用2020年后有数据的ETF，对齐到trading_dates
etf_codes = [c for c in sector_data if 'ETF' in sector_data[c]['name'] or '指数' in sector_data[c]['name']]
print(f"ETF/指数: {[sector_data[c]['name'] for c in etf_codes]}")

# 构建ETF价格面板
etf_prices = {}  # {code: pd.Series indexed by date}
for code in etf_codes:
    df = sector_data[code]['df'].copy()
    df['date'] = pd.to_datetime(df['date'])
    s = df.set_index('date')['close']
    # 对齐到trading_dates
    s = s.reindex(trading_dates)
    etf_prices[code] = s

# ========== ETF轮动回测 ==========
ETF_BUY_COST = 0.0005   # ETF佣金约万五
ETF_SELL_COST = 0.0005  # ETF无印花税，仅佣金

def run_etf_rotation(
    signal='dual_momentum',  # 'momentum', 'reversal', 'dual_momentum', 'trend'
    lookback=60,             # 动量回看天数
    top_n=3,                 # 持有ETF数量
    use_ma_filter=True,      # MA趋势过滤
    ma_period=120,           # MA周期
    label='etf_rotation'
):
    """
    ETF板块轮动策略
    月度调仓，T+1执行
    """
    # 调仓日（每月第一个交易日）
    rebalance_days = []
    prev_month = None
    for i, d in enumerate(trading_dates):
        m = (d.year, d.month)
        if m != prev_month:
            rebalance_days.append(i)
            prev_month = m
    
    rebalance_set = set(rebalance_days)
    
    # 计算每个ETF的信号
    etf_signals = {}
    for code in etf_codes:
        s = etf_prices[code]
        # 动量
        mom = s / s.shift(lookback) - 1
        # MA
        ma = s.rolling(ma_period).mean()
        above_ma = (s > ma).astype(float)
        
        etf_signals[code] = {
            'mom': mom,
            'above_ma': above_ma,
            'price': s
        }
    
    # 回测
    nav = 1.0
    holdings = {}  # {code: shares}
    cash = 1.0
    portfolio = [(trading_dates[120], 1.0)]
    
    for i in range(121, len(trading_dates)):
        current_date = trading_dates[i]
        
        # 每日NAV
        daily_nav = cash
        for code, shares in holdings.items():
            cp = etf_prices[code].iloc[i] if i < len(etf_prices[code]) else None
            if cp is not None and not np.isnan(cp):
                daily_nav += shares * cp
        nav = daily_nav
        portfolio.append((current_date, nav))
        
        if i not in rebalance_set:
            continue
        
        # 计算信号
        candidates = []
        for code in etf_codes:
            mom_val = etf_signals[code]['mom'].iloc[i] if i < len(etf_signals[code]['mom']) else None
            above_ma = etf_signals[code]['above_ma'].iloc[i] if i < len(etf_signals[code]['above_ma']) else 0
            
            if mom_val is None or np.isnan(mom_val):
                continue
            
            if signal == 'momentum':
                score = mom_val
                if use_ma_filter and above_ma == 0:
                    continue  # 只买趋势向上的
            elif signal == 'reversal':
                score = -mom_val  # 反转：买跌最多的
                if use_ma_filter and above_ma == 0:
                    continue
            elif signal == 'dual_momentum':
                # 双动量：绝对动量(趋势) + 相对动量(排名)
                if use_ma_filter and above_ma == 0:
                    continue
                score = mom_val
            elif signal == 'trend':
                score = above_ma  # 只看趋势
                if use_ma_filter and above_ma == 0:
                    continue
            
            candidates.append((code, score))
        
        if len(candidates) == 0:
            # 全部不达标，空仓
            for code in list(holdings.keys()):
                exec_idx = i + 1
                if exec_idx < len(trading_dates):
                    sp = etf_prices[code].iloc[exec_idx]
                    if sp is not None and not np.isnan(sp):
                        cash += holdings[code] * sp * (1 - ETF_SELL_COST)
                del holdings[code]
            continue
        
        # 排序选TOP N
        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = [c for c, _ in candidates[:top_n]]
        
        # T+1执行
        exec_idx = i + 1
        if exec_idx >= len(trading_dates):
            continue
        
        # 全卖
        for code in list(holdings.keys()):
            sp = etf_prices[code].iloc[exec_idx]
            if sp is not None and not np.isnan(sp):
                cash += holdings[code] * sp * (1 - ETF_SELL_COST)
            del holdings[code]
        
        # 全买（等权）
        per_etf = cash / len(selected)
        for code in selected:
            bp = etf_prices[code].iloc[exec_idx]
            if bp is not None and not np.isnan(bp) and bp > 0:
                shares = per_etf / bp / (1 + ETF_BUY_COST)
                cost = shares * bp * (1 + ETF_BUY_COST)
                cash -= cost
                holdings[code] = shares
        
        # 更新NAV
        exec_nav = cash
        for code, shares in holdings.items():
            cp = etf_prices[code].iloc[exec_idx]
            if cp is not None and not np.isnan(cp):
                exec_nav += shares * cp
        nav = exec_nav
    
    # 指标
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
    
    pf['year'] = pf['date'].dt.year
    yearly_ret = pf.groupby('year')['nav'].agg(lambda x: x.iloc[-1]/x.iloc[0] - 1)
    
    return {
        'label': label,
        'ann_return': round(ann_return * 100, 1),
        'max_dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'win_rate': round(win_rate * 100, 1),
        'final_nav': round(pf['nav'].iloc[-1], 3),
        'yearly': {str(y): round(r*100, 1) for y, r in yearly_ret.items()},
    }, pf

# ========== 个股低波选股回测（v18最优配置） ==========
BUY_COST = 0.00126
SELL_COST = 0.00176
LIQ_THRESHOLD = 50e6

# 构建因子面板
print("构建个股因子面板...")
factor_records = []
for code, df in stocks.items():
    if len(df) < 250:
        continue
    ret = df['close'].pct_change()
    vol_3m = ret.rolling(60).std() * np.sqrt(252)
    mom_3m = df['close'] / df['close'].shift(60) - 1
    rev_1m = -(df['close'] / df['close'].shift(20) - 1)
    amplitude = ((df['high'] - df['low']) / df['close']).rolling(20).mean()
    amount_20d = df['amount'].rolling(20).mean()
    ma250 = df['close'].rolling(250).mean()
    above_ma250 = (df['close'] > ma250).astype(float)
    
    for i in range(len(df)):
        factor_records.append({
            'date': df['date'].iloc[i], 'code': code,
            'vol_3m': vol_3m.iloc[i], 'rev_1m': rev_1m.iloc[i],
            'amplitude': amplitude.iloc[i], 'amount_20d': amount_20d.iloc[i],
            'above_ma250': above_ma250.iloc[i],
            'close': df['close'].iloc[i], 'open': df['open'].iloc[i],
        })

factor_panel = pd.DataFrame(factor_records)
factor_panel['date'] = pd.to_datetime(factor_panel['date'])
date_groups = {d: g for d, g in factor_panel.groupby('date')}

price_lookup = {}
for code, df in stocks.items():
    price_lookup[code] = {}
    for _, row in df.iterrows():
        price_lookup[code][row['date']] = (row['open'], row['close'])

def get_price(code, date, field='close'):
    if code in price_lookup and date in price_lookup[code]:
        return price_lookup[code][date][0] if field == 'open' else price_lookup[code][date][1]
    return None

def run_stock_lowvol(top_n=50, use_ma250=True, use_stop=True, stop_pct=0.08, label='stock_lowvol'):
    """v18最优配置：低波+反转1m TOP50 全量换仓 +MA250 +止损8%"""
    rebalance_days = []
    prev_month = None
    for i, d in enumerate(trading_dates):
        m = (d.year, d.month)
        if m != prev_month:
            rebalance_days.append(i)
            prev_month = m
    rebalance_set = set(rebalance_days)
    
    index_close = index_df['close'].values
    index_ma250 = pd.Series(index_close).rolling(250).mean().values
    
    nav = 1.0
    holdings = {}
    cash = 1.0
    portfolio = [(trading_dates[250], 1.0)]
    
    for i in range(251, len(trading_dates)):
        current_date = trading_dates[i]
        
        daily_nav = cash
        for code, (shares, bp) in holdings.items():
            cp = get_price(code, current_date, 'close')
            if cp is not None:
                daily_nav += shares * cp
        nav = daily_nav
        portfolio.append((current_date, nav))
        
        if i not in rebalance_set:
            continue
        
        if current_date not in date_groups:
            continue
        cs = date_groups[current_date].copy()
        cs = cs[(cs['amount_20d'].notna()) & (cs['amount_20d'] >= LIQ_THRESHOLD)]
        if use_ma250:
            cs = cs[cs['above_ma250'] == 1.0]
        if len(cs) == 0:
            continue
        
        # 低波+反转 z-score
        vol_z = (cs['vol_3m'].fillna(0) - cs['vol_3m'].fillna(0).mean()) / (cs['vol_3m'].fillna(0).std() + 1e-8)
        rev_z = (cs['rev_1m'].fillna(0) - cs['rev_1m'].fillna(0).mean()) / (cs['rev_1m'].fillna(0).std() + 1e-8)
        cs['score'] = -0.6 * vol_z + 0.4 * rev_z
        cs = cs.sort_values('score', ascending=False)
        
        position_ratio = 1.0
        if use_ma250 and i < len(index_ma250) and not np.isnan(index_ma250[i]):
            position_ratio = 1.0 if index_close[i] > index_ma250[i] else 0.3
        
        selected = cs.head(top_n)['code'].tolist()
        if len(selected) == 0:
            continue
        
        # 止损
        to_sell = [c for c in holdings if c not in selected]
        if use_stop:
            for code in list(holdings.keys()):
                if code in to_sell:
                    continue
                shares, bp = holdings[code]
                cp = get_price(code, current_date, 'close')
                if cp is not None and cp / bp - 1 < -stop_pct:
                    to_sell.append(code)
        
        exec_idx = i + 1
        if exec_idx >= len(trading_dates):
            continue
        exec_date = trading_dates[exec_idx]
        
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
        
        total_asset = cash
        for code, (shares, bp) in holdings.items():
            cp = get_price(code, exec_date, 'open')
            if cp is not None:
                total_asset += shares * cp
        
        target_invest = total_asset * position_ratio
        per_stock = target_invest / top_n
        for code in selected:
            if code not in holdings:
                bp = get_price(code, exec_date, 'open')
                if bp is not None and bp > 0 and per_stock <= cash:
                    shares = per_stock / bp / (1 + BUY_COST)
                    cost = shares * bp * (1 + BUY_COST)
                    cash -= cost
                    holdings[code] = (shares, bp)
        
        exec_nav = cash
        for code, (shares, bp) in holdings.items():
            cp = get_price(code, exec_date, 'close')
            if cp is not None:
                exec_nav += shares * cp
        nav = exec_nav
    
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
    pf['year'] = pf['date'].dt.year
    yearly_ret = pf.groupby('year')['nav'].agg(lambda x: x.iloc[-1]/x.iloc[0] - 1)
    
    return {
        'label': label,
        'ann_return': round(ann_return * 100, 1),
        'max_dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'win_rate': round(win_rate * 100, 1),
        'final_nav': round(pf['nav'].iloc[-1], 3),
        'yearly': {str(y): round(r*100, 1) for y, r in yearly_ret.items()},
    }, pf

def calc_metrics(pf):
    pf = pf.copy()
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
    pf['year'] = pf['date'].dt.year
    yearly_ret = pf.groupby('year')['nav'].agg(lambda x: x.iloc[-1]/x.iloc[0] - 1)
    return {
        'ann_return': round(ann_return * 100, 1),
        'max_dd': round(max_dd * 100, 1),
        'sharpe': round(sharpe, 2),
        'calmar': round(calmar, 2),
        'win_rate': round(win_rate * 100, 1),
        'final_nav': round(pf['nav'].iloc[-1], 3),
        'yearly': {str(y): round(r*100, 1) for y, r in yearly_ret.items()},
    }

# ========== 运行 ==========
print("\n" + "="*90)
print("Phoenix v19 — ETF板块轮动 + 混合策略")
print("="*90)

# 1. 纯ETF轮动
etf_configs = [
    {'signal': 'dual_momentum', 'lookback': 60, 'top_n': 3, 'use_ma_filter': True, 'ma_period': 120, 'label': 'ETF双动量 TOP3 MA120'},
    {'signal': 'dual_momentum', 'lookback': 60, 'top_n': 2, 'use_ma_filter': True, 'ma_period': 120, 'label': 'ETF双动量 TOP2 MA120'},
    {'signal': 'dual_momentum', 'lookback': 20, 'top_n': 3, 'use_ma_filter': True, 'ma_period': 120, 'label': 'ETF双动量(1月) TOP3 MA120'},
    {'signal': 'dual_momentum', 'lookback': 60, 'top_n': 3, 'use_ma_filter': True, 'ma_period': 60, 'label': 'ETF双动量 TOP3 MA60'},
    {'signal': 'reversal', 'lookback': 60, 'top_n': 3, 'use_ma_filter': True, 'ma_period': 120, 'label': 'ETF反转 TOP3 MA120'},
    {'signal': 'momentum', 'lookback': 60, 'top_n': 3, 'use_ma_filter': False, 'ma_period': 120, 'label': 'ETF动量 TOP3 无过滤'},
    {'signal': 'dual_momentum', 'lookback': 60, 'top_n': 5, 'use_ma_filter': True, 'ma_period': 120, 'label': 'ETF双动量 TOP5 MA120'},
    {'signal': 'dual_momentum', 'lookback': 60, 'top_n': 1, 'use_ma_filter': True, 'ma_period': 120, 'label': 'ETF双动量 TOP1(集中) MA120'},
]

print(f"\n--- A. 纯ETF轮动 ({len(etf_configs)}个配置) ---")
print(f"{'配置':<35} {'年化':>6} {'回撤':>6} {'夏普':>5} {'月胜率':>5} {'终值':>6}")
print("-"*70)

etf_results = []
etf_curves = {}
for cfg in etf_configs:
    try:
        result, pf = run_etf_rotation(**cfg)
        etf_results.append(result)
        etf_curves[cfg['label']] = pf
        print(f"{result['label']:<35} {result['ann_return']:>5.1f}% {result['max_dd']:>5.1f}% {result['sharpe']:>5.2f} {result['win_rate']:>4.1f}% {result['final_nav']:>6.3f}  {result.get('yearly',{})}")
    except Exception as e:
        print(f"{cfg['label']:<35} ERROR: {str(e)[:50]}")

# 2. 个股低波选股
print(f"\n--- B. 个股低波选股(v18基线) ---")
stock_result, stock_pf = run_stock_lowvol(top_n=50, use_ma250=True, use_stop=True, stop_pct=0.08, label='低波+反转TOP50(v18)')
print(f"{stock_result['label']:<35} {stock_result['ann_return']:>5.1f}% {stock_result['max_dd']:>5.1f}% {stock_result['sharpe']:>5.2f} {stock_result['win_rate']:>4.1f}% {stock_result['final_nav']:>6.3f}  {stock_result.get('yearly',{})}")

# 3. 混合策略：70%低波 + 30%ETF轮动
print(f"\n--- C. 混合策略 (70%低波+30%ETF) ---")

# 取最优ETF配置
if etf_results:
    best_etf = max(etf_results, key=lambda x: x['sharpe'])
    best_etf_label = best_etf['label']
    best_etf_pf = etf_curves[best_etf_label]
    
    # 对齐两条曲线
    stock_aligned = stock_pf.set_index('date')['nav']
    etf_aligned = best_etf_pf.set_index('date')['nav']
    
    common_dates = stock_aligned.index.intersection(etf_aligned.index)
    stock_aligned = stock_aligned.loc[common_dates]
    etf_aligned = etf_aligned.loc[common_dates]
    
    # 混合NAV
    for weight in [0.2, 0.3, 0.4, 0.5]:
        mixed_nav = 0.7 * stock_aligned + 0.3 * etf_aligned if weight == 0.3 else (1-weight) * stock_aligned + weight * etf_aligned
        mixed_pf = pd.DataFrame({'date': mixed_nav.index, 'nav': mixed_nav.values})
        mixed_metrics = calc_metrics(mixed_pf)
        print(f"混合({int((1-weight)*100)}%低波+{int(weight*100)}%ETF)    {mixed_metrics['ann_return']:>5.1f}% {mixed_metrics['max_dd']:>5.1f}% {mixed_metrics['sharpe']:>5.2f} {mixed_metrics['win_rate']:>4.1f}% {mixed_metrics['final_nav']:>6.3f}  {mixed_metrics.get('yearly',{})}")

# 基准
bench_ret = (index_df['close'].iloc[-1] / index_df['close'].iloc[250] - 1)
bench_years = (len(index_df) - 250) / 252
bench_ann = (1 + bench_ret) ** (1/bench_years) - 1
print(f"\n基准(国证A指): 年化={bench_ann*100:.1f}%")
print(f"目标: 年化>15% 回撤<15% 月胜率>70%")
