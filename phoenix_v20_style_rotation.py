"""
Phoenix v20 — 风格轮动策略
解决ZZB踏空科技问题的正确方案

核心逻辑：
- 不是固定比例配置科技ETF（v19证明会拉低长期收益）
- 而是月频动态切换：科技趋势向上→持有科技ETF，向下→退回低波选股
- 月频判断不涉及日频翻转，T+1只损失1天

策略变体：
1. 纯风格轮动：科技ETF vs 低波选股，月频切换
2. 核心-卫星动态：80%低波底仓 + 20%科技卫星（趋势向上时加满，向下时空仓）
3. 三状态切换：牛市(科技ETF) / 震荡(低波选股) / 熊市(空仓)
"""

import pickle, pandas as pd, numpy as np, json
from datetime import datetime, timedelta

# ========== 加载数据 ==========
with open('/tmp/phoenix_sector_data.pkl', 'rb') as f:
    sector_data = pickle.load(f)
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
print(f"数据: {len(stocks)}只股票, {len(trading_dates)}天")

# ETF价格
etf_codes = {
    'sh588000': '科创50ETF',
    'sz159915': '创业板ETF',
    'sh512760': '半导体ETF',
}
etf_prices = {}
for code, name in etf_codes.items():
    df = sector_data[code]['df'].copy()
    df['date'] = pd.to_datetime(df['date'])
    s = df.set_index('date')['close']
    etf_prices[code] = s.reindex(trading_dates)

# 科技板块综合指数（科创50+创业板等权）
tech_index = (etf_prices['sh588000'] + etf_prices['sz159915']) / 2
tech_index = tech_index.dropna()

# 个股因子面板（v18最优配置）
print("构建因子面板...")
factor_records = []
for code, df in stocks.items():
    if len(df) < 250:
        continue
    ret = df['close'].pct_change()
    vol_3m = ret.rolling(60).std() * np.sqrt(252)
    rev_1m = -(df['close'] / df['close'].shift(20) - 1)
    amount_20d = df['amount'].rolling(20).mean()
    ma250 = df['close'].rolling(250).mean()
    above_ma250 = (df['close'] > ma250).astype(float)
    
    for i in range(len(df)):
        factor_records.append({
            'date': df['date'].iloc[i], 'code': code,
            'vol_3m': vol_3m.iloc[i], 'rev_1m': rev_1m.iloc[i],
            'amount_20d': amount_20d.iloc[i], 'above_ma250': above_ma250.iloc[i],
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

BUY_COST = 0.00126
SELL_COST = 0.00176
ETF_BUY_COST = 0.0005
ETF_SELL_COST = 0.0005
LIQ_THRESHOLD = 50e6

# ========== 策略1：纯风格轮动 ==========
def run_style_rotation(
    tech_ma_period=60,       # 科技趋势判断MA
    stock_top_n=50,          # 低波选股数量
    use_stock_ma250=True,    # 个股MA250过滤
    use_stop_loss=True,
    stop_loss_pct=0.08,
    label='style_rotation'
):
    """
    月频风格轮动：
    - 科技ETF在MA之上 → 全仓科技ETF（科创50+创业板等权）
    - 科技ETF在MA之下 → 全仓低波选股
    - 大盘也在MA250之下 → 低波选股仓位降至30%
    """
    
    rebalance_days = []
    prev_month = None
    for i, d in enumerate(trading_dates):
        m = (d.year, d.month)
        if m != prev_month:
            rebalance_days.append(i)
            prev_month = m
    rebalance_set = set(rebalance_days)
    
    # 科技板块MA
    tech_ma = tech_index.rolling(tech_ma_period).mean()
    # 大盘MA250
    index_close = index_df['close'].values
    index_ma250 = pd.Series(index_close).rolling(250).mean().values
    
    nav = 1.0
    mode = 'stock'  # 'stock' or 'etf'
    stock_holdings = {}  # {code: (shares, buy_price)}
    etf_holdings = {}    # {code: shares}
    cash = 1.0
    portfolio = [(trading_dates[250], 1.0)]
    
    for i in range(251, len(trading_dates)):
        current_date = trading_dates[i]
        
        # 每日NAV
        daily_nav = cash
        for code, (shares, bp) in stock_holdings.items():
            cp = get_price(code, current_date, 'close')
            if cp is not None:
                daily_nav += shares * cp
        for code, shares in etf_holdings.items():
            cp = etf_prices[code].iloc[i] if i < len(etf_prices[code]) else None
            if cp is not None and not np.isnan(cp):
                daily_nav += shares * cp
        nav = daily_nav
        portfolio.append((current_date, nav))
        
        if i not in rebalance_set:
            continue
        
        # 判断科技趋势
        tech_above = False
        if i < len(tech_ma) and not np.isnan(tech_ma.iloc[i]):
            tech_above = tech_index.iloc[i] > tech_ma.iloc[i]
        
        # 判断大盘趋势
        market_bull = True
        if i < len(index_ma250) and not np.isnan(index_ma250[i]):
            market_bull = index_close[i] > index_ma250[i]
        
        # 决定模式
        new_mode = 'etf' if tech_above else 'stock'
        
        # 仓位
        position_ratio = 1.0 if market_bull else 0.3
        
        # T+1执行
        exec_idx = i + 1
        if exec_idx >= len(trading_dates):
            continue
        
        # 如果模式切换，先全卖
        if new_mode != mode:
            # 卖出所有
            for code in list(stock_holdings.keys()):
                shares, bp = stock_holdings[code]
                sp = get_price(code, trading_dates[exec_idx], 'open')
                if sp is None:
                    sp = get_price(code, current_date, 'close')
                if sp is not None:
                    cash += shares * sp * (1 - SELL_COST)
                del stock_holdings[code]
            
            for code in list(etf_holdings.keys()):
                sp = etf_prices[code].iloc[exec_idx]
                if sp is not None and not np.isnan(sp):
                    cash += etf_holdings[code] * sp * (1 - ETF_SELL_COST)
                del etf_holdings[code]
            
            mode = new_mode
        
        # 止损检查（仅stock模式）
        if use_stop_loss and mode == 'stock':
            for code in list(stock_holdings.keys()):
                shares, bp = stock_holdings[code]
                cp = get_price(code, current_date, 'close')
                if cp is not None and cp / bp - 1 < -stop_loss_pct:
                    sp = get_price(code, trading_dates[exec_idx], 'open')
                    if sp is None:
                        sp = cp
                    cash += shares * sp * (1 - SELL_COST)
                    del stock_holdings[code]
        
        # 根据模式建仓
        if mode == 'etf':
            # 买科技ETF（科创50+创业板等权）
            total_asset = cash
            for code, shares in etf_holdings.items():
                cp = etf_prices[code].iloc[exec_idx]
                if cp is not None and not np.isnan(cp):
                    total_asset += shares * cp
            
            invest = total_asset * position_ratio
            per_etf = invest / 2  # 2个ETF等权
            
            for code in ['sh588000', 'sz159915']:
                if code not in etf_holdings:
                    bp = etf_prices[code].iloc[exec_idx]
                    if bp is not None and not np.isnan(bp) and bp > 0:
                        shares = per_etf / bp / (1 + ETF_BUY_COST)
                        cost = shares * bp * (1 + ETF_BUY_COST)
                        if cost <= cash:
                            cash -= cost
                            etf_holdings[code] = shares
        
        elif mode == 'stock':
            # 低波+反转选股
            if current_date not in date_groups:
                continue
            cs = date_groups[current_date].copy()
            cs = cs[(cs['amount_20d'].notna()) & (cs['amount_20d'] >= LIQ_THRESHOLD)]
            if use_stock_ma250:
                cs = cs[cs['above_ma250'] == 1.0]
            if len(cs) == 0:
                continue
            
            vol_z = (cs['vol_3m'].fillna(0) - cs['vol_3m'].fillna(0).mean()) / (cs['vol_3m'].fillna(0).std() + 1e-8)
            rev_z = (cs['rev_1m'].fillna(0) - cs['rev_1m'].fillna(0).mean()) / (cs['rev_1m'].fillna(0).std() + 1e-8)
            cs['score'] = -0.6 * vol_z + 0.4 * rev_z
            cs = cs.sort_values('score', ascending=False)
            
            selected = cs.head(stock_top_n)['code'].tolist()
            if len(selected) == 0:
                continue
            
            # 卖出不在选中的
            to_sell = [c for c in stock_holdings if c not in selected]
            for code in to_sell:
                shares, bp = stock_holdings[code]
                sp = get_price(code, trading_dates[exec_idx], 'open')
                if sp is None:
                    sp = get_price(code, current_date, 'close')
                if sp is not None:
                    cash += shares * sp * (1 - SELL_COST)
                del stock_holdings[code]
            
            # 计算总资产
            total_asset = cash
            for code, (shares, bp) in stock_holdings.items():
                cp = get_price(code, trading_dates[exec_idx], 'open')
                if cp is not None:
                    total_asset += shares * cp
            
            target_invest = total_asset * position_ratio
            per_stock = target_invest / stock_top_n
            
            for code in selected:
                if code not in stock_holdings:
                    bp = get_price(code, trading_dates[exec_idx], 'open')
                    if bp is not None and bp > 0 and per_stock <= cash:
                        shares = per_stock / bp / (1 + BUY_COST)
                        cost = shares * bp * (1 + BUY_COST)
                        cash -= cost
                        stock_holdings[code] = (shares, bp)
        
        # 更新NAV
        exec_nav = cash
        for code, (shares, bp) in stock_holdings.items():
            cp = get_price(code, trading_dates[exec_idx], 'close')
            if cp is not None:
                exec_nav += shares * cp
        for code, shares in etf_holdings.items():
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

# ========== 运行 ==========
print(f"\n{'='*90}")
print("Phoenix v20 — 风格轮动策略")
print(f"{'='*90}")

configs = [
    {'tech_ma_period': 60, 'stock_top_n': 50, 'use_stock_ma250': True, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'label': '风格轮动 MA60科技+低波TOP50'},
    {'tech_ma_period': 120, 'stock_top_n': 50, 'use_stock_ma250': True, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'label': '风格轮动 MA120科技+低波TOP50'},
    {'tech_ma_period': 20, 'stock_top_n': 50, 'use_stock_ma250': True, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'label': '风格轮动 MA20科技+低波TOP50'},
    {'tech_ma_period': 60, 'stock_top_n': 30, 'use_stock_ma250': True, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'label': '风格轮动 MA60科技+低波TOP30'},
    {'tech_ma_period': 60, 'stock_top_n': 50, 'use_stock_ma250': True, 'use_stop_loss': False, 'label': '风格轮动 MA60科技+低波TOP50无止损'},
    {'tech_ma_period': 60, 'stock_top_n': 50, 'use_stock_ma250': False, 'use_stop_loss': True, 'stop_loss_pct': 0.08, 'label': '风格轮动 MA60科技+低波TOP50无MA250'},
]

print(f"\n{'配置':<40} {'年化':>6} {'回撤':>6} {'夏普':>5} {'Calmar':>6} {'月胜率':>5} {'终值':>6}  年度收益")
print("-"*110)

results = []
for cfg in configs:
    try:
        result, pf = run_style_rotation(**cfg)
        results.append(result)
        yearly_str = ' | '.join([f"{y}:{r}%" for y, r in result['yearly'].items()])
        print(f"{result['label']:<40} {result['ann_return']:>5.1f}% {result['max_dd']:>5.1f}% {result['sharpe']:>5.2f} {result['calmar']:>6.2f} {result['win_rate']:>4.1f}% {result['final_nav']:>6.3f}  {yearly_str}")
    except Exception as e:
        print(f"{cfg['label']:<40} ERROR: {str(e)[:60]}")

# 基准
bench_ret = (index_df['close'].iloc[-1] / index_df['close'].iloc[250] - 1)
bench_years = (len(index_df) - 250) / 252
bench_ann = (1 + bench_ret) ** (1/bench_years) - 1

# v18基线对照
print(f"\n{'v18低波+反转TOP50(对照)':<40} {'10.5':>5}% {'-11.9':>5}% {'0.82':>5} {'0.88':>6} {'52.3':>4}% {'1.684':>6}  2021:48.5|2022:-7.3|2023:-1.2|2024:4.5|2025:18.0|2026:-1.4")
print(f"{'基准(国证A指买入持有)':<40} {bench_ann*100:>5.1f}%")
print(f"\n目标: 年化>15% 回撤<15% 月胜率>70%")

if results:
    best = max(results, key=lambda x: x['sharpe'])
    print(f"\n🥇 最优: {best['label']}")
    print(f"   年化={best['ann_return']}% 回撤={best['max_dd']}% 夏普={best['sharpe']} 月胜率={best['win_rate']}%")
    print(f"   年度: {best['yearly']}")

with open('/opt/quant/phoenix_v20_result.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
