#!/usr/bin/env python3
"""
掘金QMT持仓上报脚本
用法：在QMT所在Windows机器上运行一次即可
依赖：xtquant（已安装于QMT目录下）

输出：
  1. 控制台打印持仓表格
  2. 保存JSON到 ./positions_YYYYMMDD_HHMMSS.json
  3. 可选：推送到Supabase（如果配置了凭据）
"""

import json, os, sys
from datetime import datetime

# ════════════════════════════════════════════
# 第一步：找xtquant的位置
# ════════════════════════════════════════════
# 常见路径，按优先级尝试
QMT_PATHS = [
    r"D:\迅投极速交易终端 睿智融科版\userdata_mini",
    r"D:\QMT\userdata_mini",
    r"C:\QMT\userdata_mini",
    r"D:\国金QMT\userdata_mini",
    r"C:\Program Files\QMT\userdata_mini",
]

xtquant_path = None
for p in QMT_PATHS:
    xt_path = os.path.join(p, "..", "xtquant")
    if os.path.exists(xt_path):
        xtquant_path = p
        sys.path.insert(0, os.path.join(p, ".."))
        break

if xtquant_path is None:
    print("=" * 60)
    print("⚠️  找不到QMT安装目录")
    print("请手动输入QMT的 userdata_mini 路径：")
    print("示例: D:\\迅投极速交易终端 睿智融科版\\userdata_mini")
    print("=" * 60)
    xtquant_path = input("路径: ").strip()
    parent = os.path.dirname(xtquant_path)
    sys.path.insert(0, parent)

try:
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAccount
except ImportError:
    print("❌ 无法导入 xtquant。请确认：")
    print("   1. QMT已安装且正在运行")
    print("   2. xtquant 文件夹在 QMT 安装目录下")
    print(f"   当前搜索路径: {sys.path}")
    sys.exit(1)

# ════════════════════════════════════════════
# 第二步：连接QMT并查询持仓
# ════════════════════════════════════════════
def connect_and_query(userdata_path, account_id=None, session_id=9999):
    """连接QMT并查询持仓"""
    
    xt_trader = XtQuantTrader(userdata_path, session_id)
    xt_trader.start()
    
    connect_result = xt_trader.connect()
    if connect_result != 0:
        print(f"❌ 连接QMT失败 (返回码: {connect_result})")
        print("   请确认QMT客户端已启动并登录")
        return None
    
    print("✅ QMT连接成功")
    
    # 查询资产
    if account_id:
        acc = StockAccount(account_id)
        try:
            asset = xt_trader.query_stock_asset(acc)
            if asset:
                print(f"\n💰 账户资产:")
                print(f"   总资产: ¥{asset.total_asset:,.2f}")
                print(f"   可用资金: ¥{asset.cash:,.2f}")
                print(f"   持仓市值: ¥{asset.market_value:,.2f}")
        except Exception as e:
            print(f"   ⚠️ 查询资产失败: {e}")
        
        # 查询持仓
        try:
            positions = xt_trader.query_stock_positions(acc)
        except Exception as e:
            print(f"❌ 查询持仓失败: {e}")
            return None
    else:
        # 如果没有account_id，尝试列出所有账号
        print("⚠️ 未指定资金账号，请提供")
        account_id = input("资金账号: ").strip()
        acc = StockAccount(account_id)
        try:
            positions = xt_trader.query_stock_positions(acc)
        except Exception as e:
            print(f"❌ 查询持仓失败: {e}")
            return None
    
    return xt_trader, acc, positions

# ════════════════════════════════════════════
# 第三步：格式化输出
# ════════════════════════════════════════════
def format_positions(positions, acc):
    """格式化持仓数据"""
    if not positions:
        print("\n📭 当前无持仓")
        return [], {}
    
    print(f"\n📊 当前持仓 ({len(positions)} 只):")
    print("-" * 90)
    print(f"{'代码':<12s} {'数量':>8s} {'可平':>8s} {'成本':>10s} {'现价':>10s} {'市值':>14s} {'盈亏':>10s} {'盈亏%':>8s}")
    print("-" * 90)
    
    total_mv = 0
    total_pnl = 0
    position_list = []
    
    for pos in positions:
        code = pos.stock_code
        vol = pos.volume
        can_use = pos.can_use_volume
        cost = pos.avg_price
        # XtPosition has market_value but we don't know current price directly
        # We need to get current price from xtdata
        mv = pos.market_value
        pnl = pos.float_profit if hasattr(pos, 'float_profit') else 0
        pnl_pct = (pnl / (mv - pnl) * 100) if (mv - pnl) != 0 else 0
        
        total_mv += mv
        total_pnl += pnl
        
        print(f"{code:<12s} {vol:>8d} {can_use:>8d} {cost:>10.2f} {'?':>10s} {mv:>14,.2f} {pnl:>+10,.2f} {pnl_pct:>+7.2f}%")
        
        position_list.append({
            "code": code,
            "volume": vol,
            "can_use": can_use,
            "avg_cost": cost,
            "market_value": mv,
            "float_pnl": pnl,
            "pnl_pct": round(pnl_pct, 2)
        })
    
    print("-" * 90)
    print(f"{'合计':<12s} {'':>8s} {'':>8s} {'':>10s} {'':>10s} {total_mv:>14,.2f} {total_pnl:>+10,.2f}")
    
    summary = {
        "total_positions": len(positions),
        "total_market_value": total_mv,
        "total_pnl": total_pnl,
        "positions": position_list,
        "timestamp": datetime.now().isoformat(),
        "account_id": acc.account_id if acc else "unknown"
    }
    
    return position_list, summary

def get_current_prices(xt_trader, position_list):
    """尝试获取实时价格（需要xtdata）"""
    try:
        from xtquant import xtdata
        codes = [p["code"] for p in position_list]
        if codes:
            # 获取最新价
            for p in position_list:
                try:
                    data = xtdata.get_market_data_ex(
                        fields=['close'],
                        stock_list=[p["code"]],
                        period='1d',
                        count=1
                    )
                    if data and 'close' in data:
                        last_price = float(data['close'].iloc[-1, 0])
                        p["last_price"] = last_price
                        # 重新计算盈亏
                        p["float_pnl"] = (last_price - p["avg_cost"]) * p["volume"]
                        if p["avg_cost"] > 0:
                            p["pnl_pct"] = round((last_price / p["avg_cost"] - 1) * 100, 2)
                except:
                    p["last_price"] = None
    except ImportError:
        print("⚠️ xtdata未导入，无法获取实时价格")
    except Exception as e:
        print(f"⚠️ 获取实时价格失败: {e}")
    
    return position_list

# ════════════════════════════════════════════
# 第四步：保存和输出
# ════════════════════════════════════════════
def save_output(summary, position_list):
    """保存JSON文件"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"positions_{timestamp}.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 持仓数据已保存至: {os.path.abspath(filename)}")
    return filename

# ════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  掘金QMT持仓查询工具")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 获取资金账号（可选，从命令行参数或环境变量）
    account_id = None
    if len(sys.argv) > 1:
        account_id = sys.argv[1]
        print(f"使用指定资金账号: {account_id}")
    
    # 连接查询
    result = connect_and_query(xtquant_path, account_id)
    if result is None:
        sys.exit(1)
    
    xt_trader, acc, positions = result
    
    # 格式化
    position_list, summary = format_positions(positions, acc)
    
    if position_list:
        # 尝试获取实时价格
        position_list = get_current_prices(xt_trader, position_list)
        
        # 更新summary
        summary["positions"] = position_list
        total_pnl = sum(p.get("float_pnl", 0) for p in position_list)
        summary["total_pnl"] = total_pnl
        
        # 打印实时价格
        print(f"\n📊 含实时价格:")
        print("-" * 90)
        print(f"{'代码':<12s} {'数量':>8s} {'成本':>10s} {'现价':>10s} {'市值':>14s} {'盈亏':>10s} {'盈亏%':>8s}")
        print("-" * 90)
        for p in position_list:
            lp = p.get("last_price", "?")
            lp_str = f"{lp:>10.2f}" if lp else f"{'?':>10s}"
            mv = p["market_value"]
            pnl = p["float_pnl"]
            pnl_pct = p["pnl_pct"]
            print(f"{p['code']:<12s} {p['volume']:>8d} {p['avg_cost']:>10.2f} {lp_str} {mv:>14,.2f} {pnl:>+10,.2f} {pnl_pct:>+7.2f}%")
        print("-" * 90)
        print(f"{'合计':<12s} {'':>8s} {'':>10s} {'':>10s} {summary['total_market_value']:>14,.2f} {total_pnl:>+10,.2f}")
    
    # 保存
    saved_file = save_output(summary, position_list)
    
    # 输出JSON（方便Hermes直接读取）
    print(f"\n📋 JSON数据 (可直接复制):")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    print("\n" + "=" * 60)
    print("✅ 完成。请将上面JSON数据或生成的JSON文件发给我。")
    print("=" * 60)
