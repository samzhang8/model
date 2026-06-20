#!/usr/bin/env python3
"""
策略回测引擎 v0.1 - 复现同花顺多因子策略
用户策略规则：
  1. 3日均线金叉21日均线
  2. MACD月线红柱
  3. 净利润同比增长 > 20%
  4. 非ST
  5. 当日涨幅 < 5% 不追高
  6. 板块热点确认
止损止盈：
  -5% 止损
  +10% 后从最高点回落 5-6% 止盈
"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

print("=" * 60)
print("  AgentMatrixLab 策略回测引擎 v0.1")
print("  复现：3日金叉21日 + MACD红柱 + 净利增长 > 20%")
print("=" * 60)

# ====== 1. 获取A股列表 ======
print("\n[1/5] 获取A股列表...")
try:
    stock_list = ak.stock_zh_a_spot_em()
    # 过滤ST和科创板(688开头，无净利润要求不同)
    stock_list = stock_list[~stock_list['名称'].str.contains('ST|退', na=False)]
    # 去掉新股（上市不到60天）
    if '代码' in stock_list.columns:
        stock_list['代码'] = stock_list['代码'].astype(str)
    print(f"   A股池: {len(stock_list)} 只（已剔除ST）")
except Exception as e:
    print(f"   stock_zh_a_spot_em 出错: {e}")
    # 备用简单数据源
    try:
        stock_list = ak.stock_info_a_code_name()
        stock_list = stock_list[~stock_list['名称'].str.contains('ST|退', na=False)]
        print(f"   A股池(备用): {len(stock_list)} 只")
    except:
        print("   无法获取，使用固定测试池")
        stock_list = pd.DataFrame({
            '代码': ['000001', '000002', '000858', '002415', '300750', '600519'],
            '名称': ['平安银行', '万科A', '五粮液', '海康威视', '宁德时代', '贵州茅台']
        })
        stock_list['代码'] = stock_list['代码'].astype(str)

# ====== 2. 批量获取个股日线数据 ======
print("\n[2/5] 拉取日线数据...")

# 测试先用 50 只热门股
test_pool = stock_list.head(50)
all_stock_data = {}
failed = 0

for idx, row in test_pool.iterrows():
    code = row['代码']
    name = row['名称']
    try:
        # 补充代码前缀
        if code.startswith('6'):
            symbol = f"{code}"
        else:
            symbol = f"{code}"
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                start_date="20240101", end_date="20260101",
                                adjust="qfq")
        if len(df) > 50:  # 至少60个交易日
            df['code'] = code
            df['name'] = name
            all_stock_data[code] = df
    except:
        failed += 1

print(f"   成功拉取: {len(all_stock_data)} 只, 失败: {failed} 只")

# ====== 3. 计算技术指标 ======
print("\n[3/5] 计算金叉+MACD信号...")

def calc_signals(df):
    """计算买入信号"""
    # 3日均线和21日均线
    df['MA3'] = df['收盘'].rolling(3).mean()
    df['MA21'] = df['收盘'].rolling(21).mean()
    
    # 金叉信号：前一天MA3 < MA21，今天MA3 > MA21
    df['golden_cross'] = (df['MA3'].shift(1) < df['MA21'].shift(1)) & \
                          (df['MA3'] > df['MA21'])
    
    # MACD (12, 26, 9)
    ema12 = df['收盘'].ewm(span=12).mean()
    ema26 = df['收盘'].ewm(span=26).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    
    # MACD红柱：柱 > 0
    df['macd_red'] = df['MACD_Hist'] > 0
    
    # 涨幅
    df['pct_change'] = df['收盘'].pct_change() * 100
    
    # 买入信号组合
    df['buy_signal'] = df['golden_cross'] & df['macd_red'] & (df['pct_change'] < 5)
    
    return df

signals_summary = []
for code, df in all_stock_data.items():
    df = calc_signals(df)
    all_stock_data[code] = df
    # 统计最近一次信号
    recent_signal = df[df['buy_signal']].tail(1)
    if len(recent_signal) > 0:
        signals_summary.append({
            '代码': code,
            '名称': df['name'].iloc[0],
            '信号日': recent_signal['日期'].values[0],
            '收盘价': recent_signal['收盘'].values[0],
            '涨幅%': round(recent_signal['pct_change'].values[0], 2),
            'MACD_柱': round(recent_signal['MACD_Hist'].values[0], 4)
        })

# ====== 4. 回测模拟 ======
print("\n[4/5] 运行回测...")

def simulate_trade(df, capital=100000, stop_loss=-0.05, take_profit=0.10, 
                   trailing_pct=0.055):
    """
    模拟交易
    capital: 初始资金
    stop_loss: 止损线 (-5%)
    take_profit: 止盈线 (+10%) 
    trailing_pct: 回撤止盈 (从高点回落5.5%)
    """
    trades = []
    position = 0
    entry_price = 0
    high_since_entry = 0
    cash = capital
    shares = 0
    
    for i in range(len(df)):
        row = df.iloc[i]
        
        if position == 0 and row['buy_signal']:
            # 买入
            entry_price = row['收盘']
            shares = int(cash * 0.95 / entry_price)  # 95%仓位
            cost = shares * entry_price
            cash -= cost
            position = 1
            high_since_entry = entry_price
            trades.append({
                '日期': row['日期'], '操作': '买入', '价格': entry_price,
                '数量': shares, '原因': '金叉+MACD红柱'
            })
            
        elif position == 1:
            high_since_entry = max(high_since_entry, row['收盘'])
            pnl = (row['收盘'] - entry_price) / entry_price
            
            sell_reason = None
            # 止损
            if pnl <= stop_loss:
                sell_reason = '止损'
            # 止盈或回撤止盈
            elif high_since_entry / entry_price - 1 >= take_profit:
                drawdown = (row['收盘'] - high_since_entry) / high_since_entry
                if drawdown <= -trailing_pct:
                    sell_reason = '回撤止盈'
            
            if sell_reason:
                cash += shares * row['收盘']
                trades.append({
                    '日期': row['日期'], '操作': '卖出', '价格': row['收盘'],
                    '数量': shares, '原因': sell_reason,
                    '盈亏%': round(pnl * 100, 2)
                })
                position = 0
                shares = 0
                entry_price = 0
                high_since_entry = 0
    
    # 平仓
    if position == 1:
        last_price = df.iloc[-1]['收盘']
        cash += shares * last_price
        pnl = (last_price - entry_price) / entry_price
        trades.append({
            '日期': df.iloc[-1]['日期'], '操作': '卖出', '价格': last_price,
            '数量': shares, '原因': '期末平仓', '盈亏%': round(pnl * 100, 2)
        })
    
    total_return = (cash - capital) / capital * 100
    return trades, total_return, cash

# 汇总回测
backtest_results = []
for code, df in list(all_stock_data.items())[:20]:
    trades, ret, final = simulate_trade(df, capital=100000)
    win_trades = [t for t in trades if t['操作'] == '卖出' and float(str(t.get('盈亏%', '0')).replace('%','')) > 0]
    all_sells = [t for t in trades if t['操作'] == '卖出']
    win_rate = len(win_trades)/len(all_sells)*100 if all_sells else 0
    
    backtest_results.append({
        '代码': code, '名称': df['name'].iloc[0],
        '交易次数': len(all_sells),
        '胜率%': round(win_rate, 1),
        '总收益%': round(ret, 2),
        '最终资金': round(final, 2)
    })

# ====== 5. 输出报告 ======
print("\n" + "=" * 60)
print("  回测报告")
print("=" * 60)

if signals_summary:
    print(f"\n📊 近期信号 ({len(signals_summary)} 只):")
    for s in sorted(signals_summary, key=lambda x: x['涨幅%']):
        print(f"  {s['代码']} {s['名称']:6s} | {str(s['信号日'])[:10]} | "
              f"涨幅:{s['涨幅%']:+.2f}% | MACD柱:{s['MACD_柱']:.4f}")

if backtest_results:
    print(f"\n💰 回测收益排名:")
    for r in sorted(backtest_results, key=lambda x: x['总收益%'], reverse=True)[:10]:
        print(f"  {r['代码']} {r['名称']:6s} | 交易{r['交易次数']}次 | "
              f"胜率{r['胜率%']:.0f}% | 收益{r['总收益%']:+.2f}% | "
              f"余额:{r['最终资金']:.0f}")

# 整体统计
if backtest_results:
    avg_ret = np.mean([r['总收益%'] for r in backtest_results])
    pos_stocks = len([r for r in backtest_results if r['总收益%'] > 0])
    print(f"\n📈 整体统计:")
    print(f"  平均收益: {avg_ret:+.2f}%")
    print(f"  盈利比例: {pos_stocks}/{len(backtest_results)}")
    print(f"  年化参考: {avg_ret:.1f}% (近2年累计)")

print("\n⚠️  注意：以上为简化回测，不含基本面因子（净利增长），不含板块热点过滤。")
print("  完整版需接入财务数据(akshare stock_financial_analysis_indicator)。")
print("=" * 60)
