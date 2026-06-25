#!/usr/bin/env python3
"""
因子面板数据生成器
从ClickHouse拉取因子IC数据，生成JSON供前端因子面板加载
"""
import subprocess, json, math, sys
from datetime import datetime
from collections import defaultdict

def ch(sql, timeout=30):
    cmd = f'docker exec clickhouse clickhouse-client --query "{sql}" --format CSVWithNames 2>/dev/null'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout

# 因子分类
CATEGORIES = {
    "avg_amount_1m": "规模", "log_amount_1m": "规模", "log_price": "规模",
    "illiquidity": "流动性", "turnover_proxy": "流动性",
    "volatility_1m": "低波动", "volatility_3m": "低波动", "volatility_6m": "低波动",
    "amplitude_1m": "低波动", "high_low_1m": "低波动",
    "ret_1m": "动量", "ret_3m": "动量", "ret_6m": "动量", "ret_12m": "动量",
    "momentum_12_1": "动量", "ret_3m_vol_adj": "动量",
    "min_ret_1m": "反转", "max_ret_1m": "反转", "reversal": "反转",
    "bb_position": "技术", "rsi_14": "技术", "ma_signal": "技术",
    "vol_convergence": "技术", "volume_ratio": "技术", "up_ratio_1m": "技术",
    "roe_ttm": "基本面", "roa_ttm": "基本面", "net_margin": "基本面",
    "debt_to_asset": "基本面", "revenue_yoy": "成长", "profit_yoy": "成长",
    "eps_yoy": "成长", "asset_turnover": "基本面",
}

DIRECTION = {
    "avg_amount_1m": "负向", "log_amount_1m": "负向", "log_price": "负向",
    "volatility_1m": "负向", "volatility_3m": "负向", "volatility_6m": "负向",
    "amplitude_1m": "负向", "high_low_1m": "负向",
    "turnover_proxy": "负向", "max_ret_1m": "负向",
    "ret_3m": "负向", "ret_6m": "负向", "ret_12m": "负向",
    "momentum_12_1": "负向", "ret_3m_vol_adj": "负向", "reversal": "负向",
    "roa_ttm": "负向", "net_margin": "负向", "roe_ttm": "负向",
}

# 小市值相关性（之前算的）
SMALLCAP_CORR = {
    "illiquidity": -0.047, "volume_ratio": -0.016, "net_margin": 0.004,
    "profit_yoy": -0.004, "eps_yoy": -0.004, "roe_ttm": 0.006,
    "revenue_yoy": 0.008, "debt_to_asset": 0.062, "bb_position": 0.030,
    "rsi_14": 0.046, "vol_convergence": 0.053, "ma_signal": 0.074,
    "up_ratio_1m": 0.078, "roa_ttm": 0.101, "ret_1m": 0.117,
    "reversal": -0.117, "min_ret_1m": -0.120, "volatility_6m": 0.148,
    "max_ret_1m": 0.161, "volatility_3m": 0.176, "high_low_1m": 0.182,
    "amplitude_1m": 0.188, "volatility_1m": 0.196, "momentum_12_1": 0.214,
    "ret_3m_vol_adj": 0.223, "ret_12m": 0.230, "ret_3m": 0.237,
    "ret_6m": 0.265, "turnover_proxy": 0.298,
}

# 1. 全期IC统计
sql = """
SELECT factor, avg(IC) as ic_mean, avg(IC)/stddevPop(IC) as ic_ir,
       countIf(IC>0)*100.0/count() as win_rate, count() as n
FROM amazingdata.factor_ic GROUP BY factor ORDER BY abs(ic_ir) DESC
"""
rows = ch(sql).strip().split("\n")[1:]

factors = {}
for row in rows:
    p = row.split(",")
    if len(p) < 5: continue
    f = p[0].strip('"')
    factors[f] = {
        "name": f, "category": CATEGORIES.get(f, "其他"),
        "direction": DIRECTION.get(f, "正向"),
        "ic_mean": round(float(p[1]), 4),
        "ic_ir": round(float(p[2]), 3),
        "win_rate": round(float(p[3]), 1),
        "n_months": int(p[4]),
        "smallcap_corr": SMALLCAP_CORR.get(f, None),
    }

# 2. 最近3个月IC趋势
sql3 = """
SELECT factor, groupArray(3)(IC) as recent_ic
FROM (SELECT factor, trade_date, IC FROM amazingdata.factor_ic
      WHERE trade_date >= '2026-01-01' ORDER BY trade_date)
GROUP BY factor
"""
rows3 = ch(sql3).strip().split("\n")[1:]
for row in rows3:
    p = row.split(",")
    if len(p) < 2: continue
    f = p[0].strip('"')
    if f in factors:
        try:
            ics = [float(x) for x in p[1].strip('"[]').split(",") if x]
            factors[f]["recent_3m"] = ics[-3:] if len(ics) >= 3 else ics
            # 趋势：最近3个月的IC方向
            if len(ics) >= 3:
                factors[f]["trend"] = "up" if ics[-1] > ics[-3] else "down"
            else:
                factors[f]["trend"] = "flat"
        except:
            factors[f]["recent_3m"] = []
            factors[f]["trend"] = "flat"

# 3. 状态灯：绿=IC_IR>0.3, 黄=0.1-0.3, 红=<0.1
for f, d in factors.items():
    ir = abs(d["ic_ir"])
    if ir >= 0.3:
        d["status"] = "green"
    elif ir >= 0.1:
        d["status"] = "yellow"
    else:
        d["status"] = "red"

# 输出JSON
result = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "data_source": "ClickHouse amazingdata.factor_ic",
    "total_factors": len(factors),
    "factors": sorted(factors.values(), key=lambda x: abs(x["ic_ir"]), reverse=True),
}

print(json.dumps(result, ensure_ascii=False, indent=2))
