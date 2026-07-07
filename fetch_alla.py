#!/usr/bin/python3
"""
全A数据拉取 — 分层抽样500只 + 国证A指
覆盖: 创业板/主板/科创板/中小板 按比例取样
"""
import akshare as ak
import pandas as pd
import time, os

START_DATE = "20200101"
END_DATE = "20260618"
CACHE_PATH = "/tmp/phoenix_alla_data.pkl"
TARGET_COUNT = 500

# 1. 获取全A列表
df = ak.stock_info_a_code_name()
print(f"全A股票: {len(df)}只")

# 排除北交所(920) — 流动性差
df = df[~df['code'].str.startswith('920')]
print(f"排除北交所后: {len(df)}只")

# 按前缀分组，按比例抽样
df['prefix'] = df['code'].str[:3]
prefix_counts = df['prefix'].value_counts()
print(f"\n各板块分布:")
print(prefix_counts)

# 按比例分配500个名额
sample_codes = []
for prefix, count in prefix_counts.items():
    n = max(10, int(TARGET_COUNT * count / len(df)))
    subset = df[df['prefix'] == prefix]['code'].tolist()
    # 取前n只(代码排序=上市早=流动性好)
    sample_codes.extend(subset[:n])

sample_codes = sorted(sample_codes)
print(f"\n抽样: {len(sample_codes)}只")

# 2. 拉取国证A指
print("\n拉取国证A指...")
index_df = ak.stock_zh_index_daily(symbol='sz399107')
index_df['date'] = pd.to_datetime(index_df['date'])
index_df = index_df[(index_df['date'] >= START_DATE) & (index_df['date'] <= END_DATE)]
index_df = index_df.sort_values('date').reset_index(drop=True)
print(f"  国证A指: {len(index_df)}天")

# 3. 代码→symbol映射
def code_to_symbol(code):
    if code.startswith(('600', '601', '603', '605', '688')):
        return f"sh{code}"
    elif code.startswith(('300', '301', '000', '001', '002')):
        return f"sz{code}"
    else:
        return None

# 4. 批量拉取
print(f"\n开始拉取{len(sample_codes)}只股票数据...")
stock_data = {}
success = 0
failed = 0
t0 = time.time()

for i, code in enumerate(sample_codes):
    symbol = code_to_symbol(code)
    if not symbol:
        failed += 1
        continue
    try:
        sdf = ak.stock_zh_a_daily(symbol=symbol, start_date=START_DATE, end_date=END_DATE, adjust="qfq")
        if sdf is None or len(sdf) == 0:
            failed += 1
            continue
        sdf['date'] = pd.to_datetime(sdf['date'])
        sdf = sdf[['date','open','close','high','low','volume','amount']].copy()
        sdf = sdf.sort_values('date').reset_index(drop=True)
        sdf['code'] = code
        stock_data[code] = sdf
        success += 1
        if (i+1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i+1) / elapsed
            eta = (len(sample_codes) - i - 1) / rate
            print(f"  {i+1}/{len(sample_codes)}: 成功{success} 失败{failed} | {elapsed:.0f}s elapsed, ETA {eta:.0f}s")
        time.sleep(0.05)
    except Exception as e:
        failed += 1

elapsed = time.time() - t0
print(f"\n完成: {success}成功, {failed}失败, 耗时{elapsed:.0f}s")

# 5. 保存
pd.to_pickle({'index': index_df, 'stocks': stock_data, 'index_name': '国证A指'}, CACHE_PATH)
print(f"缓存: {CACHE_PATH}")
print(f"  指数: {len(index_df)}天")
print(f"  个股: {len(stock_data)}只")

# 按板块统计
prefix_stats = {}
for code in stock_data:
    p = code[:3]
    prefix_stats[p] = prefix_stats.get(p, 0) + 1
print(f"\n成功按板块:")
for p, c in sorted(prefix_stats.items(), key=lambda x: -x[1]):
    print(f"  {p}: {c}")
