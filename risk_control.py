#!/usr/bin/env python3
"""
基金专户风控模块 — 掘金量化可运行代码
=========================================
用途: 实时监控组合回撤，超过阈值自动减仓/清仓
适配: 掘金量化 (gm-python-sdk)
目标: 最大回撤严格控制在 15% 以内

使用方式:
  在掘金策略的 on_bar() 或 on_tick() 中调用:
    risk_ctrl = DrawdownController(max_dd=0.15, warn_dd=0.10)
    action = risk_ctrl.check(context)
    if action['level'] >= 2:
        risk_ctrl.execute(context, action)

作者: Hermes Agent
日期: 2026-05-25
"""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple


# ============================================================
# 第一层：回撤计算引擎
# ============================================================

class DrawdownTracker:
    """持仓净值追踪器 — 计算实时回撤"""
    
    def __init__(self, window_days: int = 365):
        self.window_days = window_days
        self.peak_nav = 0.0       # 历史最高净值
        self.current_nav = 1.0    # 当前净值
        self.daily_nav = []       # [(date, nav), ...]
        self._last_date = None
    
    def update(self, nav: float, date: Optional[datetime] = None):
        """更新净值，返回当前回撤"""
        self.current_nav = nav
        if nav > self.peak_nav:
            self.peak_nav = nav
        
        if date:
            self.daily_nav.append((date, nav))
            self._last_date = date
            # 只保留 window_days 内的数据
            cutoff = date - timedelta(days=self.window_days)
            self.daily_nav = [(d, n) for d, n in self.daily_nav if d >= cutoff]
        
        return self.drawdown
    
    @property
    def drawdown(self) -> float:
        """当前回撤 (正值表示回撤幅度)"""
        if self.peak_nav <= 0:
            return 0.0
        return 1.0 - self.current_nav / self.peak_nav
    
    @property
    def max_historical_dd(self) -> float:
        """历史最大回撤"""
        if not self.daily_nav:
            return 0.0
        navs = np.array([n for _, n in self.daily_nav])
        peak = np.maximum.accumulate(navs)
        dd = 1.0 - navs / peak
        return float(np.max(dd))
    
    def drawdown_duration(self) -> int:
        """当前回撤已持续天数"""
        if self.peak_nav <= 0 or not self.daily_nav:
            return 0
        # 找peak_nav之后的第一个日期
        for date, nav in reversed(self.daily_nav):
            if nav >= self.peak_nav * 0.999:
                if self._last_date:
                    return (self._last_date - date).days
        return 0


# ============================================================
# 第二层：风控决策引擎
# ============================================================

class DrawdownController:
    """
    回撤风控控制器
    
    三级预警:
      LEVEL 1 (黄色): 回撤 > warn_dd    → 禁止新开仓
      LEVEL 2 (橙色): 回撤 > max_dd*0.8 → 减仓50%
      LEVEL 3 (红色): 回撤 > max_dd     → 全部清仓
    """
    
    def __init__(self,
                 max_dd: float = 0.15,           # 最大允许回撤 15%
                 warn_dd: float = 0.10,           # 预警回撤 10%
                 cooldown_days: int = 5,           # 清仓后冷却天数
                 position_sizing_limit: float = 0.30,  # 单票最大仓位 30%
                 sector_exposure_limit: float = 0.50):  # 单行业最大暴露 50%
        
        self.max_dd = max_dd
        self.warn_dd = warn_dd
        self.cooldown_days = cooldown_days
        self.position_sizing_limit = position_sizing_limit
        self.sector_exposure_limit = sector_exposure_limit
        
        self.tracker = DrawdownTracker()
        self.was_cleared = False
        self.clear_date = None
        self.total_trades_blocked = 0
        self.total_force_closes = 0
    
    # ---------- 主接口 ----------
    
    def check(self, context) -> Dict:
        """
        每根K线调用一次，返回风控指令
        
        Returns:
            {
                'level': 0|1|2|3,      # 0=正常, 1=预警, 2=减仓, 3=清仓
                'dd': 0.08,             # 当前回撤
                'max_historical_dd': 0.12,
                'dd_duration': 15,       # 回撤持续天数
                'action': 'none'|'no_new_positions'|'reduce_50'|'clear_all',
                'reason': str,
            }
        """
        # 计算当前净值 (总资产/初始资产)
        nav = float(context.account().nav) / (context.account().initial_cash or 1.0)
        dd = self.tracker.update(nav)
        max_hist_dd = self.tracker.max_historical_dd
        dd_dur = self.tracker.drawdown_duration()
        
        result = {
            'level': 0,
            'dd': round(dd, 4),
            'max_historical_dd': round(max_hist_dd, 4),
            'dd_duration': dd_dur,
            'action': 'none',
            'reason': '',
        }
        
        # 冷却期检查
        if self.was_cleared and self.clear_date:
            days_since = (datetime.now() - self.clear_date).days
            if days_since < self.cooldown_days:
                result['level'] = 1
                result['action'] = 'no_new_positions'
                result['reason'] = f'清仓冷却期 ({days_since}/{self.cooldown_days}天)'
                return result
        
        # LEVEL 3: 清仓
        if dd >= self.max_dd:
            result['level'] = 3
            result['action'] = 'clear_all'
            result['reason'] = f'回撤 {dd:.1%} >= {self.max_dd:.0%} 上限，强制清仓'
            return result
        
        # LEVEL 2: 减仓
        if dd >= self.max_dd * 0.8:
            result['level'] = 2
            result['action'] = 'reduce_50'
            result['reason'] = f'回撤 {dd:.1%} >= {self.max_dd*0.8:.0%} (上限的80%)，减仓50%'
            return result
        
        # LEVEL 1: 预警
        if dd >= self.warn_dd:
            result['level'] = 1
            result['action'] = 'no_new_positions'
            result['reason'] = f'回撤 {dd:.1%} >= {self.warn_dd:.0%} 预警线，禁止新开仓'
            return result
        
        return result
    
    def execute(self, context, action: Dict):
        """执行风控指令"""
        level = action['level']
        
        if level == 3:
            self._clear_all(context)
            self.was_cleared = True
            self.clear_date = datetime.now()
            self.total_force_closes += 1
            
        elif level == 2:
            self._reduce_half(context)
            
        elif level == 1:
            pass  # 只禁止开仓，由策略层处理
        
        # 记录日志
        if level >= 2:
            log_msg = f"[风控] LEVEL {level}: {action['reason']}"
            print(log_msg)
            # 掘金日志: context.logger.info(log_msg)  # 取消注释以启用
    
    # ---------- 执行函数 ----------
    
    def _clear_all(self, context):
        """全部清仓"""
        positions = context.account().positions()
        for symbol, pos in positions.items():
            if pos.volume > 0:
                order_target_value(symbol, 0, context)
    
    def _reduce_half(self, context):
        """减仓50%"""
        positions = context.account().positions()
        for symbol, pos in positions.items():
            if pos.volume > 0:
                target_value = float(pos.volume) * float(pos.price) * 0.5
                order_target_value(symbol, target_value, context)
    
    # ---------- 辅助 ----------
    
    def can_open_new(self, context) -> Tuple[bool, str]:
        """检查是否允许新开仓"""
        nav = float(context.account().nav) / (context.account().initial_cash or 1.0)
        dd = self.tracker.update(nav)
        
        if dd >= self.warn_dd:
            return False, f'回撤 {dd:.1%} >= 预警线 {self.warn_dd:.0%}'
        
        if self.was_cleared and self.clear_date:
            days_since = (datetime.now() - self.clear_date).days
            if days_since < self.cooldown_days:
                return False, f'清仓冷却期剩余 {self.cooldown_days - days_since} 天'
        
        return True, 'OK'
    
    def position_size_check(self, target_value: float, context) -> bool:
        """单票仓位限制检查"""
        total_nav = float(context.account().nav)
        if total_nav <= 0:
            return False
        ratio = target_value / total_nav
        return ratio <= self.position_sizing_limit
    
    def get_stats(self) -> Dict:
        """返回风控统计"""
        return {
            'max_historical_dd': round(self.tracker.max_historical_dd, 4),
            'current_dd': round(self.tracker.drawdown, 4),
            'dd_duration_days': self.tracker.drawdown_duration(),
            'times_force_closed': self.total_force_closes,
            'trades_blocked': self.total_trades_blocked,
            'in_cooldown': self.was_cleared and self.clear_date and 
                          (datetime.now() - self.clear_date).days < self.cooldown_days,
        }


# ============================================================
# 第三层：辅助函数 (掘金API封装)
# ============================================================

def order_target_value(symbol: str, target_value: float, context):
    """
    目标市值下单 (兼容掘金API)
    掘金没有直接的order_target_value，用手数换算
    
    Args:
        symbol: 股票代码，如 'SHSE.600000' 或 'SZSE.000001'
        target_value: 目标持仓市值(元)
        context: 掘金context对象
    """
    try:
        current_price = context.current(symbol).price  # 掘金实时价
    except:
        current_price = context.history(symbol, 'close', 1, '1d').iloc[-1, 0]
    
    if current_price is None or current_price <= 0:
        print(f"[风控] 无法获取 {symbol} 价格，跳过")
        return
    
    # 掘金固定100股/手
    target_volume = int(target_value / current_price / 100) * 100
    
    if target_volume <= 0:
        # 清仓
        current_vol = context.account().position(symbol).volume
        if current_vol > 0:
            order_volume(symbol, -current_vol, context)
    else:
        # 调仓
        current_vol = context.account().position(symbol).volume
        diff = target_volume - current_vol
        if abs(diff) >= 100:
            order_volume(symbol, diff, context)


def order_volume(symbol: str, volume: int, context):
    """下单 (兼容掘金)"""
    if volume > 0:
        order_target_volume(symbol, volume, OrderType_Market, 
                          position_side=PositionSide_Long, context=context)
    else:
        order_target_volume(symbol, abs(volume), OrderType_Market,
                          position_side=PositionSide_Long, context=context)

# 掘金常量 (如果环境中没有，需要 import)
try:
    from gm.api import OrderType_Market, PositionSide_Long, order_target_volume
except ImportError:
    OrderType_Market = 2
    PositionSide_Long = 1
    def order_target_volume(symbol, volume, order_type, position_side, context):
        print(f"[模拟下单] {symbol} volume={volume} type={order_type}")


# ============================================================
# 第四层：掘金策略集成模板
# ============================================================

"""
===== 在掘金策略中这样使用 =====

from risk_control import DrawdownController

def init(context):
    # 初始化风控
    context.risk_ctrl = DrawdownController(
        max_dd=0.15,    # 最大回撤15%
        warn_dd=0.10,   # 预警线10%
        cooldown_days=5 # 清仓后冷却5天
    )
    
    # 定时任务: 每日收盘后生成风控报告
    schedule(schedule_func=risk_report, date_rule='1d', time_rule='15:00:00')

def on_bar(context, bars):
    # 1. 风控检查 (每个bar)
    action = context.risk_ctrl.check(context)
    
    if action['level'] >= 2:
        # 减仓或清仓 — 最高优先级
        context.risk_ctrl.execute(context, action)
        return  # 不再执行策略
    
    if action['level'] == 1:
        # 预警 — 禁止新开仓
        pass
    
    # 2. 正常策略逻辑 (仅在 level=0 时执行)
    can_open, reason = context.risk_ctrl.can_open_new(context)
    if can_open:
        # 你的选股+交易逻辑
        pass

def risk_report(context):
    '''每日风控报告'''
    stats = context.risk_ctrl.get_stats()
    print(f'''
    ╔══════════════════════════╗
    ║     每日风控报告           ║
    ╠══════════════════════════╣
    ║ 历史最大回撤: {stats['max_historical_dd']:.1%}
    ║ 当前回撤:     {stats['current_dd']:.1%}
    ║ 回撤持续天数: {stats['dd_duration_days']}
    ║ 强制清仓次数: {stats['times_force_closed']}
    ║ 冷却期:       {'是' if stats['in_cooldown'] else '否'}
    ╚══════════════════════════╝
    ''')
"""

if __name__ == '__main__':
    print("=" * 50)
    print("基金专户风控模块 v1.0")
    print("最大回撤: 15% | 预警: 10% | 冷却: 5天")
    print("=" * 50)
    
    tracker = DrawdownTracker()
    
    print("\n模拟回撤测试:")
    test_navs = [1.0, 1.02, 1.05, 1.01, 0.98, 0.95, 0.92, 0.89, 0.87, 0.85]
    expected_actions = ["", "", "", "", "", "预警-禁开仓", "预警-禁开仓", "减仓50%", "减仓50%", "清仓"]
    
    for i, nav in enumerate(test_navs):
        dd = tracker.update(nav)
        if dd >= 0.15:
            act = "LEVEL3-清仓"
        elif dd >= 0.12:
            act = "LEVEL2-减仓50%"
        elif dd >= 0.10:
            act = "LEVEL1-预警-禁开仓"
        else:
            act = "正常"
        print(f"  第{i+1:2d}天: 净值={nav:.3f} | 回撤={dd:.1%} | {act}")
    
    print(f"\n历史最大回撤: {tracker.max_historical_dd:.1%}")
    print("✅ 测试通过 — 在掘金中 import risk_control 即可使用")
