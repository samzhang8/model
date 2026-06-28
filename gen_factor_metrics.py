#!/usr/bin/env python3
"""
因子面板数据生成器 v3
数据源: 本地 factor_metrics.json（322因子基准） + ClickHouse（更新原始33因子的最新IC）
输出: 合并后的 factor_metrics.json
"""
import subprocess, json, math
from datetime import datetime
from collections import defaultdict

def ch(sql, timeout=30):
    cmd = f'docker exec clickhouse clickhouse-client --query "{sql}" --format CSVWithNames 2>/dev/null'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout

# 1. 从ClickHouse更新33原始因子的最新IC
ORIGINAL_33 = {
    "avg_amount_1m","log_amount_1m","log_price","illiquidity","turnover_proxy",
    "volatility_1m","volatility_3m","volatility_6m","amplitude_1m","high_low_1m",
    "ret_1m","ret_3m","ret_6m","ret_12m","momentum_12_1","ret_3m_vol_adj",
    "min_ret_1m","max_ret_1m","reversal","up_ratio_1m",
    "bb_position","rsi_14","ma_signal","vol_convergence","volume_ratio",
    "roe_ttm","roa_ttm","net_margin","debt_to_asset",
    "revenue_yoy","profit_yoy","eps_yoy","asset_turnover"
}

print("Updating original 33 factors from ClickHouse...")
sql = """
SELECT factor, avg(IC) as ic_mean, avg(IC)/stddevSamp(IC) as ic_ir,
       countIf(IC>0)*100.0/count() as win_rate, count() as n
FROM amazingdata.factor_ic GROUP BY factor
"""
rows = ch(sql).strip().split("\n")[1:]

ch_data = {}
for row in rows:
    p = row.split(",")
    if len(p) < 5: continue
    f = p[0].strip('"')
    ch_data[f] = {
        "ic_mean": round(float(p[1]), 4),
        "ic_ir": round(float(p[2]), 3),
        "win_rate": round(float(p[3]), 1),
        "n_months": int(p[4]),
    }

# 2. 加载现有322因子的完整数据
with open('/opt/quant/docs/factor_metrics.json') as f:
    data = json.load(f)

# 3. 更新原始33因子的IC数据
updated = 0
for f in data['factors']:
    if f['name'] in ORIGINAL_33 and f['name'] in ch_data:
        cd = ch_data[f['name']]
        f['ic_mean'] = cd['ic_mean']
        f['ic_ir'] = cd['ic_ir']
        f['win_rate'] = cd['win_rate']
        f['n_months'] = cd['n_months']
        ir = abs(f['ic_ir'])
        f['status'] = 'green' if ir>=0.3 else ('yellow' if ir>=0.1 else 'red')
        updated += 1

# 4. 更新衍生因子的IC（也从ClickHouse读取）
DERIVED = {"illiq_div_vol","illiq_x_vol","vol_term_struct","bb_rsi","amp_vol_ratio","quality","liq_quality"}
for f in data['factors']:
    if f['name'] in DERIVED and f['name'] in ch_data:
        cd = ch_data[f['name']]
        f['ic_mean'] = cd['ic_mean']
        f['ic_ir'] = cd['ic_ir']
        f['win_rate'] = cd['win_rate']
        f['n_months'] = cd['n_months']
        ir = abs(f['ic_ir'])
        f['status'] = 'green' if ir>=0.3 else ('yellow' if ir>=0.1 else 'red')
        updated += 1

# 5. 更新时间戳
data['generated_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
data['total_factors'] = len(data['factors'])

# 6. 去重检测：IC_IR完全相同(差距<0.001)的因子标记为疑似重复
from collections import defaultdict
ic_ir_groups = defaultdict(list)
for f in data['factors']:
    ir_key = round(f.get('ic_ir', 0), 3)
    ic_ir_groups[ir_key].append(f['name'])

duplicates = []
for ir_key, names in ic_ir_groups.items():
    if len(names) >= 2:
        duplicates.append({'ir_value': ir_key, 'factors': names, 'count': len(names)})
duplicates.sort(key=lambda x: -x['count'])
data['duplicates_detected'] = len(duplicates)
if duplicates:
    data['duplicate_groups'] = duplicates[:30]  # top 30 groups
    print(f"⚠️  Duplicate detection: {len(duplicates)} groups share identical IC_IR")
    for d in duplicates[:5]:
        print(f"  IR={d['ir_value']}: {d['count']} factors — {', '.join(d['factors'][:5])}")

# 7. 高smallcap_corr因子强制降级：|corr|>0.5 无论IC_IR多高都降为REVIEW/yellow
downgraded = 0
for f in data['factors']:
    sc = f.get('smallcap_corr')
    if sc is not None and abs(sc) > 0.5:
        if f.get('validation_verdict') in ('SAFE', None):
            f['validation_verdict'] = 'REVIEW'
            f['validation_score'] = min(f.get('validation_score', 70), 45)
            f['validation_warning'] = f'⚠️ 高市值相关性(|corr|={abs(sc):.2f}), IC主要由规模效应驱动'
        f['status'] = 'yellow'  # force yellow regardless of IC_IR
        downgraded += 1
if downgraded > 0:
    print(f"⚠️  Downgraded {downgraded} high-|smallcap_corr| factors to yellow/REVIEW")

data['factors'].sort(key=lambda x: abs(x['ic_ir']), reverse=True)

with open('/opt/quant/docs/factor_metrics.json', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Updated {updated} factors from ClickHouse")
print(f"Total: {data['total_factors']} factors, generated: {data['generated_at']}")
