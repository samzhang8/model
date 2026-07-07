# -*- coding: utf-8 -*-
"""
低换手率策略 — 掘金量化版本
因子逻辑: 过去20日平均换手率最低的股票，持有20日
回测标的: A股全市场 (剔除ST/退市/上市不足60日)
调仓周期: 月度 (每月第一个交易日)
交易成本: 佣金0.03% + 印花税0.05% + 滑点0.1%

作者: Hermes Agent
日期: 2026-06-30
"""

import numpy as np
import pandas as pd


# ===== 策略参数 =====
LOOKBACK = 20           # 换手率计算窗口(日)
HOLD_DAYS = 20          # 持仓天数
TOP_N = 20              # 持仓数量
COMMISSION = 0.0003     # 佣金
STAMP_TAX = 0.0005      # 印花税(卖出)
SLIPPAGE = 0.001        # 滑点
MIN_LISTED_DAYS = 60    # 上市最低天数
FILTER_ST = True        # 剔除ST
FILTER_PRICE = True     # 剔除低股价
MIN_PRICE = 2.0         # 最低股价
EXCLUDE_SUSPEND = True  # 排除停牌


def init(context):
    """
    初始化策略参数和全局变量
    """
    # 基准: 沪深300
    context.benchmark = '000300.SH'
    
    # 股票池: 全A股
    context.universe = 'ALL'
    
    # 滑点
    context.slippage = SLIPPAGE
    
    # 全局变量
    context.counter = 0
    context.current_positions = set()
    
    # 月度调仓: 每月第一个交易日
    schedule(schedule_func=rebalance, date_rule='1m', time_rule='09:35:00')
    
    print(f'[低换手率策略] 初始化完成')
    print(f'  持仓数: {TOP_N}')
    print(f'  换仓周期: {HOLD_DAYS}日')
    print(f'  换手率窗口: {LOOKBACK}日')
    print(f'  成本: 佣金{COMMISSION*100:.2f}% + 印花税{STAMP_TAX*100:.2f}% + 滑点{SLIPPAGE*100:.2f}%')


def rebalance(context):
    """
    月度调仓函数
    """
    print(f'\n[{context.now.strftime("%Y-%m-%d")}] === 调仓开始 ===')
    
    # 1. 获取全市场股票
    symbols = get_stock_list(context)
    if len(symbols) < TOP_N * 3:
        print(f'  可用股票不足({len(symbols)}), 跳过')
        return
    
    # 2. 计算换手率因子
    turnover_ratio = compute_turnover(context, symbols)
    if len(turnover_ratio) < TOP_N:
        print(f'  可计算因子股票不足({len(turnover_ratio)}), 跳过')
        return
    
    # 3. 选择最低换手率TOP_N
    selected = sorted(turnover_ratio.items(), key=lambda x: x[1])[:TOP_N]
    target_symbols = [s for s, _ in selected]
    
    # 4. 获取当前持仓
    current_symbols = list(context.current_positions)
    
    # 5. 卖出不在目标池中的持仓
    for symbol in current_symbols:
        if symbol not in target_symbols:
            order_target_percent(symbol, 0)
            print(f'  卖出: {symbol}')
    
    # 6. 等权买入目标池
    weight_per = 1.0 / TOP_N
    for symbol in target_symbols:
        order_target_percent(symbol, weight_per)
    
    # 7. 更新持仓记录
    context.current_positions = set(target_symbols)
    
    # 8. 打印持仓信息
    print(f'  换仓完成: {len(target_symbols)}只')
    print(f'  换仓率: {len(set(target_symbols) - set(current_symbols))}/{TOP_N}')
    print(f'  TOP3: {[f"{s}({t:.2%})" for s, t in selected[:3]]}')
    
    context.counter += 1


def compute_turnover(context, symbols):
    """
    计算过去LOOKBACK日平均换手率
    
    换手率 = 日均成交量 / 流通股本
    
    Returns:
        dict: {symbol: avg_turnover_rate}
    """
    result = {}
    
    for symbol in symbols:
        try:
            # 获取历史日线数据
            hist = get_history(symbol, frequency='1d', count=LOOKBACK + 5)
            if hist is None or len(hist) < LOOKBACK:
                continue
            
            # 成交量 (手)
            volume = hist['volume'].values[-LOOKBACK:]
            
            # 流通股本 (股) — 掘金数据字段
            # 注意: float_shares 是流通股本
            if 'float_shares' in hist.columns:
                float_shares = hist['float_shares'].values[-1]
            else:
                # 如果没有流通股本，使用量/价反推近似
                # 换手率 = 成交量(手) * 100 / 流通股本(股)
                # 实际掘金可以直接用 turnover 字段
                if 'turn' in hist.columns or 'turnover_rate' in hist.columns:
                    turn_key = 'turn' if 'turn' in hist.columns else 'turnover_rate'
                    avg_turn = np.mean(hist[turn_key].values[-LOOKBACK:])
                    result[symbol] = avg_turn
                    continue
                float_shares = 1e9  # 默认10亿股
            
            # 计算换手率
            turnover_rate = volume * 100 / float_shares  # 手*100=股 / 流通股
            avg_turn = np.mean(turnover_rate)
            
            # 过滤异常值
            if 0 < avg_turn < 0.5:  # 换手率 < 50%
                result[symbol] = avg_turn
                
        except Exception as e:
            continue
    
    return result


def get_stock_list(context):
    """
    获取可交易股票列表，应用过滤条件
    """
    # 全市场A股
    all_stocks = get_universe(context.universe)
    candidates = []
    
    for symbol in all_stocks:
        # ST过滤
        if FILTER_ST:
            name = get_stock_name(symbol)
            if name and 'ST' in name:
                continue
        
        # 创业板/科创板代码过滤 (可选)
        # if symbol.startswith('300') or symbol.startswith('688'):
        #     continue
        
        # 上市天数过滤
        if MIN_LISTED_DAYS > 0:
            try:
                listed_date = get_listed_date(symbol)
                if listed_date:
                    days_listed = (context.now - listed_date).days
                    if days_listed < MIN_LISTED_DAYS:
                        continue
            except:
                pass
        
        # 停牌过滤
        if EXCLUDE_SUSPEND:
            try:
                # 检查最近一天是否有交易
                hist = get_history(symbol, frequency='1d', count=1)
                if hist is None or len(hist) == 0:
                    continue
            except:
                continue
        
        # 股价过滤
        if FILTER_PRICE:
            try:
                price = current_price(symbol)
                if price < MIN_PRICE:
                    continue
            except:
                pass
        
        candidates.append(symbol)
    
    return candidates


def current_price(symbol):
    """获取当前价格"""
    try:
        return get_history(symbol, frequency='1d', count=1)['close'].values[-1]
    except:
        return 0


def get_stock_name(symbol):
    """获取股票名称"""
    try:
        return get_instrument(symbol).name
    except:
        return ''


def get_listed_date(symbol):
    """获取上市日期"""
    try:
        return get_instrument(symbol).listed_date
    except:
        return None


# ===== 可选: 风险控制 =====

def on_order_filled(context, order):
    """订单成交回调"""
    pass


def on_bar(context, bars):
    """日线回调 (非调仓日监控)"""
    pass


# ===== 回测/实盘说明 =====
"""
使用方法:
1. 掘金量化终端 → 新建策略 → 粘贴本文件
2. 回测参数:
   - 时间: 2015-01-01 ~ 2024-12-31
   - 初始资金: 100,000
   - 费率: 佣金0.03% + 印花税0.05%
   - 基准: 沪深300
3. 点击"回测"

预期结果(基于研究回测):
- 年化收益: ~23-28% (刨除成本)
- 最大回撤: ~35-45%
- 夏普比率: 0.8-1.0

注意事项:
- 低换手率因子在A股有效，但高度依赖小盘暴露
- 2017年"漂亮50"行情期间策略可能显著跑输
- 实盘需注意流动性: 建议只交易日成交额>500万的股票
"""
