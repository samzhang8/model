#!/usr/bin/env python3
"""
资产排名看板 - 数据生成器
拉取2020-01-01至今的历史日线数据，计算指标，输出JSON供前端加载

指标：
1. 年化收益率
2. 最大回撤
3. 夏普比率 (统一0%无风险利率)
4. 卡玛比率 (年化收益/最大回撤)
5. 创新高最长天数 + 起止日期区间
"""

import urllib.request
import json
import math
import sys
from datetime import datetime

ASSETS = [
    {"code": "510300", "prefix": "sh", "name": "沪深300ETF", "trade_type": "T+1"},
    {"code": "512090", "prefix": "sh", "name": "MSCI A股ETF", "trade_type": "T+1"},
    {"code": "510500", "prefix": "sh", "name": "中证500ETF", "trade_type": "T+1"},
    {"code": "510050", "prefix": "sh", "name": "上证50ETF", "trade_type": "T+1"},
    {"code": "159915", "prefix": "sz", "name": "创业板ETF", "trade_type": "T+1"},
    {"code": "159949", "prefix": "sz", "name": "创业板50", "trade_type": "T+1"},
    {"code": "588000", "prefix": "sh", "name": "科创50ETF", "trade_type": "T+1"},
    {"code": "588020", "prefix": "sh", "name": "科创成长ETF", "trade_type": "T+1"},
    {"code": "159780", "prefix": "sz", "name": "科创创业ETF", "trade_type": "T+1"},
    {"code": "159967", "prefix": "sz", "name": "创业板成长ETF", "trade_type": "T+1"},
    {"code": "159552", "prefix": "sz", "name": "中证2000增强ETF", "trade_type": "T+1"},
    {"code": "512100", "prefix": "sh", "name": "中证1000ETF", "trade_type": "T+1"},
    {"code": "159901", "prefix": "sz", "name": "深证100ETF", "trade_type": "T+1"},
    {"code": "510880", "prefix": "sh", "name": "红利ETF(上证)", "trade_type": "T+1"},
    {"code": "515080", "prefix": "sh", "name": "中证红利ETF", "trade_type": "T+1"},
    {"code": "512890", "prefix": "sh", "name": "红利低波ETF", "trade_type": "T+1"},
    {"code": "159207", "prefix": "sz", "name": "高股息ETF", "trade_type": "T+1"},
    {"code": "511260", "prefix": "sh", "name": "十年国债ETF", "trade_type": "T+0"},
    {"code": "511360", "prefix": "sh", "name": "短融ETF", "trade_type": "T+0"},
    {"code": "511880", "prefix": "sh", "name": "银华日利ETF", "trade_type": "T+0"},
    {"code": "511380", "prefix": "sh", "name": "可转债ETF", "trade_type": "T+0"},
    {"code": "511520", "prefix": "sh", "name": "政金债ETF", "trade_type": "T+0"},
    {"code": "508056", "prefix": "sh", "name": "普洛斯REIT", "trade_type": "T+1"},
    {"code": "600519", "prefix": "sh", "name": "贵州茅台", "trade_type": "T+1"},
    {"code": "BTC", "prefix": "crypto", "name": "比特币", "trade_type": "7×24"},
    {"code": "ETH", "prefix": "crypto", "name": "以太坊", "trade_type": "7×24"},
    {"code": "TRX", "prefix": "crypto", "name": "波场", "trade_type": "7×24"},
    {"code": "513310", "prefix": "sh", "name": "中韩半导体ETF", "trade_type": "T+0"},
    {"code": "513120", "prefix": "sh", "name": "港股创新药ETF", "trade_type": "T+0"},
    {"code": "513090", "prefix": "sh", "name": "香港证券ETF", "trade_type": "T+0"},
    {"code": "513180", "prefix": "sh", "name": "恒生科技ETF", "trade_type": "T+0"},
    {"code": "159570", "prefix": "sz", "name": "港股通创新药ETF", "trade_type": "T+0"},
    {"code": "513050", "prefix": "sh", "name": "中概互联网ETF", "trade_type": "T+0"},
    {"code": "517520", "prefix": "sh", "name": "黄金股ETF", "trade_type": "T+0"},
    {"code": "513330", "prefix": "sh", "name": "恒生互联网ETF", "trade_type": "T+0"},
    {"code": "520500", "prefix": "sh", "name": "恒生创新药ETF", "trade_type": "T+0"},
    {"code": "159792", "prefix": "sz", "name": "港股通互联网ETF", "trade_type": "T+0"},
    {"code": "159892", "prefix": "sz", "name": "恒生医药ETF", "trade_type": "T+0"},
    {"code": "159131", "prefix": "sz", "name": "港股通信息技术ETF", "trade_type": "T+0"},
    {"code": "159506", "prefix": "sz", "name": "港股通创新药医疗ETF", "trade_type": "T+0"},
    {"code": "513060", "prefix": "sh", "name": "恒生医疗ETF", "trade_type": "T+0"},
    {"code": "513350", "prefix": "sh", "name": "标普油气ETF", "trade_type": "T+0"},
    {"code": "159366", "prefix": "sz", "name": "港股医疗ETF", "trade_type": "T+0"},
    {"code": "513190", "prefix": "sh", "name": "港股通金融ETF", "trade_type": "T+0"},
    {"code": "513750", "prefix": "sh", "name": "港股通非银ETF", "trade_type": "T+0"},
    {"code": "159866", "prefix": "sz", "name": "日经ETF", "trade_type": "T+0"},
    {"code": "513400", "prefix": "sh", "name": "道琼斯ETF", "trade_type": "T+0"},
    {"code": "159920", "prefix": "sz", "name": "恒生ETF", "trade_type": "T+0"},
    {"code": "513500", "prefix": "sh", "name": "标普500ETF", "trade_type": "T+0"},
    {"code": "513200", "prefix": "sh", "name": "港股通医药ETF", "trade_type": "T+0"},
    {"code": "159660", "prefix": "sz", "name": "纳指ETF", "trade_type": "T+0"},
    {"code": "513290", "prefix": "sh", "name": "纳指生物科技ETF", "trade_type": "T+0"},
    {"code": "159502", "prefix": "sz", "name": "标普生物科技ETF", "trade_type": "T+0"},
    {"code": "159262", "prefix": "sz", "name": "港股通科技ETF", "trade_type": "T+0"},
    {"code": "159605", "prefix": "sz", "name": "中概互联ETF", "trade_type": "T+0"},
    {"code": "513980", "prefix": "sh", "name": "港股科技ETF", "trade_type": "T+0"},
    {"code": "159636", "prefix": "sz", "name": "港股通科技30ETF", "trade_type": "T+0"},
    {"code": "520600", "prefix": "sh", "name": "港股通汽车ETF", "trade_type": "T+0"},
    {"code": "159687", "prefix": "sz", "name": "亚太精选ETF", "trade_type": "T+0"},
    {"code": "159561", "prefix": "sz", "name": "德国ETF", "trade_type": "T+0"},
    {"code": "520870", "prefix": "sh", "name": "巴西ETF", "trade_type": "T+0"},
    {"code": "513850", "prefix": "sh", "name": "美国50ETF", "trade_type": "T+0"},
    {"code": "513360", "prefix": "sh", "name": "教育ETF", "trade_type": "T+0"},
    {"code": "513070", "prefix": "sh", "name": "港股通消费ETF", "trade_type": "T+0"},
    {"code": "513800", "prefix": "sh", "name": "日本东证指数ETF", "trade_type": "T+0"},
    {"code": "159750", "prefix": "sz", "name": "港股科技50ETF", "trade_type": "T+0"},
    {"code": "159329", "prefix": "sz", "name": "沙特ETF", "trade_type": "T+0"},
    {"code": "513970", "prefix": "sh", "name": "恒生消费ETF", "trade_type": "T+0"},
    {"code": "513550", "prefix": "sh", "name": "港股通50ETF", "trade_type": "T+0"},
    {"code": "159976", "prefix": "sz", "name": "湾创ETF", "trade_type": "T+0"},
    {"code": "513730", "prefix": "sh", "name": "东南亚科技ETF", "trade_type": "T+0"},
    {"code": "513080", "prefix": "sh", "name": "法国ETF", "trade_type": "T+0"},
    {"code": "513900", "prefix": "sh", "name": "港股通100ETF", "trade_type": "T+0"},
    {"code": "518880", "prefix": "sh", "name": "黄金ETF", "trade_type": "T+0"},
    {"code": "159985", "prefix": "sz", "name": "豆粕ETF", "trade_type": "T+0"},
    {"code": "159981", "prefix": "sz", "name": "能源化工ETF", "trade_type": "T+0"},
    {"code": "159980", "prefix": "sz", "name": "有色ETF", "trade_type": "T+0"},
    {"code": "518890", "prefix": "sh", "name": "上海金ETF", "trade_type": "T+0"},
    {"code": "515880", "prefix": "sh", "name": "通信ETF", "trade_type": "T+1"},
    {"code": "159516", "prefix": "sz", "name": "半导体设备ETF", "trade_type": "T+1"},
    {"code": "512480", "prefix": "sh", "name": "半导体ETF", "trade_type": "T+1"},
    {"code": "512880", "prefix": "sh", "name": "证券ETF", "trade_type": "T+1"},
    {"code": "512400", "prefix": "sh", "name": "有色金属ETF", "trade_type": "T+1"},
    {"code": "159995", "prefix": "sz", "name": "芯片ETF", "trade_type": "T+1"},
    {"code": "512800", "prefix": "sh", "name": "银行ETF", "trade_type": "T+1"},
    {"code": "159992", "prefix": "sz", "name": "创新药ETF", "trade_type": "T+1"},
    {"code": "512170", "prefix": "sh", "name": "医疗ETF", "trade_type": "T+1"},
    {"code": "515220", "prefix": "sh", "name": "煤炭ETF", "trade_type": "T+1"},
    {"code": "159326", "prefix": "sz", "name": "电网设备ETF", "trade_type": "T+1"},
    {"code": "159530", "prefix": "sz", "name": "机器人ETF", "trade_type": "T+1"},
    {"code": "159732", "prefix": "sz", "name": "消费电子ETF", "trade_type": "T+1"},
    {"code": "159611", "prefix": "sz", "name": "电力ETF", "trade_type": "T+1"},
    {"code": "159819", "prefix": "sz", "name": "人工智能ETF", "trade_type": "T+1"},
    {"code": "159206", "prefix": "sz", "name": "卫星ETF", "trade_type": "T+1"},
    {"code": "512660", "prefix": "sh", "name": "军工ETF", "trade_type": "T+1"},
    {"code": "560860", "prefix": "sh", "name": "工业有色ETF", "trade_type": "T+1"},
    {"code": "159852", "prefix": "sz", "name": "软件ETF", "trade_type": "T+1"},
    {"code": "159865", "prefix": "sz", "name": "养殖ETF", "trade_type": "T+1"},
    {"code": "159869", "prefix": "sz", "name": "游戏ETF", "trade_type": "T+1"},
    {"code": "516150", "prefix": "sh", "name": "稀土ETF", "trade_type": "T+1"},
    {"code": "512980", "prefix": "sh", "name": "传媒ETF", "trade_type": "T+1"},
    {"code": "512710", "prefix": "sh", "name": "军工龙头ETF", "trade_type": "T+1"},
    {"code": "159566", "prefix": "sz", "name": "储能电池ETF", "trade_type": "T+1"},
    {"code": "159928", "prefix": "sz", "name": "消费ETF", "trade_type": "T+1"},
    {"code": "159755", "prefix": "sz", "name": "电池ETF", "trade_type": "T+1"},
    {"code": "159870", "prefix": "sz", "name": "化工ETF", "trade_type": "T+1"},
    {"code": "159859", "prefix": "sz", "name": "生物医药ETF", "trade_type": "T+1"},
    {"code": "159698", "prefix": "sz", "name": "粮食ETF", "trade_type": "T+1"},
    {"code": "159851", "prefix": "sz", "name": "金融科技ETF", "trade_type": "T+1"},
    {"code": "562800", "prefix": "sh", "name": "稀有金属ETF", "trade_type": "T+1"},
    {"code": "159766", "prefix": "sz", "name": "旅游ETF", "trade_type": "T+1"},
    {"code": "159825", "prefix": "sz", "name": "农业ETF", "trade_type": "T+1"},
    {"code": "159227", "prefix": "sz", "name": "航空航天ETF", "trade_type": "T+1"},
    {"code": "515000", "prefix": "sh", "name": "科技ETF", "trade_type": "T+1"},
    {"code": "159997", "prefix": "sz", "name": "电子ETF", "trade_type": "T+1"},
    {"code": "560280", "prefix": "sh", "name": "工程机械ETF", "trade_type": "T+1"},
    {"code": "562550", "prefix": "sh", "name": "绿电ETF", "trade_type": "T+1"},
    {"code": "159930", "prefix": "sz", "name": "能源ETF", "trade_type": "T+1"},
    {"code": "515030", "prefix": "sh", "name": "新能源车ETF", "trade_type": "T+1"},
    {"code": "159697", "prefix": "sz", "name": "石油ETF", "trade_type": "T+1"},
    {"code": "159667", "prefix": "sz", "name": "工业母机ETF", "trade_type": "T+1"},
    {"code": "516510", "prefix": "sh", "name": "云计算ETF", "trade_type": "T+1"},
    {"code": "159309", "prefix": "sz", "name": "油气ETF", "trade_type": "T+1"},
    {"code": "515790", "prefix": "sh", "name": "光伏ETF", "trade_type": "T+1"},
    {"code": "516160", "prefix": "sh", "name": "新能源ETF", "trade_type": "T+1"},
    {"code": "159883", "prefix": "sz", "name": "医疗器械ETF", "trade_type": "T+1"},
    {"code": "512200", "prefix": "sh", "name": "房地产ETF", "trade_type": "T+1"},
    {"code": "510230", "prefix": "sh", "name": "金融ETF", "trade_type": "T+1"},
    {"code": "517900", "prefix": "sh", "name": "银行AH优选ETF", "trade_type": "T+0"},
    {"code": "159546", "prefix": "sz", "name": "集成电路ETF", "trade_type": "T+1"},
    {"code": "512670", "prefix": "sh", "name": "国防ETF", "trade_type": "T+1"},
    {"code": "515400", "prefix": "sh", "name": "大数据ETF", "trade_type": "T+1"},
    {"code": "560710", "prefix": "sh", "name": "船舶ETF", "trade_type": "T+1"},
    {"code": "159625", "prefix": "sz", "name": "绿色电力ETF", "trade_type": "T+1"},
    {"code": "159998", "prefix": "sz", "name": "计算机ETF", "trade_type": "T+1"},
    {"code": "561330", "prefix": "sh", "name": "矿业ETF", "trade_type": "T+1"},
    {"code": "515210", "prefix": "sh", "name": "钢铁ETF", "trade_type": "T+1"},
    {"code": "516620", "prefix": "sh", "name": "影视ETF", "trade_type": "T+1"},
    {"code": "159731", "prefix": "sz", "name": "石化ETF", "trade_type": "T+1"},
    {"code": "560080", "prefix": "sh", "name": "中药ETF", "trade_type": "T+1"},
    {"code": "516910", "prefix": "sh", "name": "物流ETF", "trade_type": "T+1"},
    {"code": "159996", "prefix": "sz", "name": "家电ETF", "trade_type": "T+1"},
    {"code": "563010", "prefix": "sh", "name": "电信ETF", "trade_type": "T+1"},
    {"code": "159939", "prefix": "sz", "name": "信息技术ETF", "trade_type": "T+1"},
    {"code": "159745", "prefix": "sz", "name": "建材ETF", "trade_type": "T+1"},
    {"code": "515170", "prefix": "sh", "name": "食品饮料ETF", "trade_type": "T+1"},
    {"code": "159616", "prefix": "sz", "name": "农牧ETF", "trade_type": "T+1"},
    {"code": "562700", "prefix": "sh", "name": "汽车零部件ETF", "trade_type": "T+1"},
    {"code": "515650", "prefix": "sh", "name": "消费50ETF", "trade_type": "T+1"},
    {"code": "516820", "prefix": "sh", "name": "医疗创新ETF", "trade_type": "T+1"},
    {"code": "159666", "prefix": "sz", "name": "交通运输ETF", "trade_type": "T+1"},
    {"code": "159837", "prefix": "sz", "name": "生物科技ETF", "trade_type": "T+1"},
    {"code": "159230", "prefix": "sz", "name": "通用航空ETF", "trade_type": "T+1"},
    {"code": "159811", "prefix": "sz", "name": "5GETF", "trade_type": "T+1"},
    {"code": "516520", "prefix": "sh", "name": "智能驾驶ETF", "trade_type": "T+1"},
    {"code": "512220", "prefix": "sh", "name": "TMTETF", "trade_type": "T+1"},
    {"code": "516970", "prefix": "sh", "name": "基建ETF", "trade_type": "T+1"},
]

RISK_FREE_RATE = 0.0
TRADING_DAYS_PER_YEAR = 252

# ETF代码 -> 蛋卷(雪球)指数代码：仅股票类指数有"估值分位"(PE/PB百分位)
# 覆盖宽基/红利/主要行业/主要跨境，其余(加密/商品/债券/REITs/未收录指数)显示"—"
VALUATION_INDEX_MAP = {
    # 宽基
    "510300": "SH000300",   # 沪深300ETF
    "510500": "SH000905",   # 中证500ETF
    "510050": "SH000016",   # 上证50ETF
    "159915": "SZ399006",   # 创业板ETF
    "588000": "SH000688",   # 科创50ETF
    "512100": "SH000852",   # 中证1000ETF
    "159901": "SZ399330",   # 深证100ETF
    # 红利/价值
    "510880": "SH000015",   # 红利ETF(上证)
    "515080": "SH000922",   # 中证红利ETF
    "512890": "CSIH30269",  # 红利低波ETF
    # 行业/主题
    "512880": "SZ399975",   # 证券ETF(证券公司)
    "512800": "SZ399986",   # 银行ETF(中证银行)
    "512170": "SZ399989",   # 医疗ETF(中证医疗)
    "515220": "SZ399998",   # 煤炭ETF(中证煤炭)
    "512660": "SZ399967",   # 军工ETF(中证军工)
    "512980": "SZ399971",   # 传媒ETF(中证传媒)
    "159928": "SH000932",   # 消费ETF(主要消费)
    "515000": "CSI931087",  # 科技ETF(科技龙头)
    # 跨境
    "513180": "HKHSTECH",   # 恒生科技ETF
    "513050": "CSIH30533",  # 中概互联网ETF(中概互联50)
    "159920": "HKHSI",      # 恒生ETF(恒生指数)
    "513500": "SP500",      # 标普500ETF
    "159660": "NDX",        # 纳指ETF(纳指100)
    "159561": "GDAXI",      # 德国ETF(德国DAX)
}


def fetch_valuation() -> dict:
    """从蛋卷(雪球)API拉取指数估值分位，返回 {指数代码: percentile(0~1)}
    优先用PE(TTM)分位，PE无效(≤0)时退回PB分位"""
    url = "https://danjuanapp.com/djapi/index_eva/dj"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  估值分位拉取失败: {e}", file=sys.stderr)
        return {}

    result = {}
    for it in data.get("data", {}).get("items", []):
        code = it.get("index_code")
        pe = it.get("pe") or 0
        pe_pct = it.get("pe_percentile")
        pb_pct = it.get("pb_percentile")
        if pe and pe > 0 and pe_pct is not None:
            pct = pe_pct
        elif pb_pct is not None:
            pct = pb_pct
        else:
            pct = None
        result[code] = pct
    return result


def fetch_history(symbol: str, prefix: str) -> list:
    """分3段拉取2020-01-01至今的前复权日线数据"""
    if prefix == "crypto":
        return fetch_crypto_history(symbol)

    chunks = [
        ("2020-01-01", "2021-12-31"),
        ("2022-01-01", "2023-12-31"),
        ("2024-01-01", "2026-12-31"),
    ]
    import time
    all_bars = []
    for start, end in chunks:
        url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get?param={prefix}{symbol},day,{start},{end},640,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                stock_data = data.get("data", {}).get(f"{prefix}{symbol}", {})
                bars = stock_data.get("qfqday") or stock_data.get("day") or []
                all_bars.extend(bars)
        except Exception as e:
            print(f"  Error fetching {symbol} {start}-{end}: {e}", file=sys.stderr)
        time.sleep(0.15)

    seen = set()
    unique = []
    for bar in all_bars:
        date = bar[0]
        if date not in seen:
            seen.add(date)
            unique.append(bar)
    return unique


def fetch_crypto_history(symbol: str) -> list:
    """从Binance公共API拉取加密货币历史日线 (USD计价)"""
    binance_symbol = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "TRX": "TRXUSDT"}[symbol]
    all_bars = []
    import time

    end_time = int(time.time() * 1000)

    while len(all_bars) < 2500 and end_time > 0:
        url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval=1d&limit=1000&endTime={end_time}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                klines = json.loads(resp.read())
                if not klines:
                    break
                for k in klines:
                    ts = k[0] / 1000
                    date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                    price = str(float(k[4]))
                    all_bars.append([date_str, str(float(k[1])), price, str(float(k[2])), str(float(k[3])), str(float(k[5]))])
                end_time = klines[0][0] - 86400000
        except Exception as e:
            print(f"  Binance error for {symbol}: {e}", file=sys.stderr)
            break
        time.sleep(0.5)

    seen = set()
    unique = []
    for bar in all_bars:
        date = bar[0]
        if date not in seen:
            seen.add(date)
            unique.append(bar)
    unique.sort(key=lambda x: x[0])
    return unique


def compute_metrics(bars: list, code: str, name: str) -> dict:
    """计算各项指标"""
    if len(bars) < 2:
        return {"code": code, "name": name, "error": "数据不足"}

    dates = [bar[0] for bar in bars]
    closes = [float(bar[2]) for bar in bars]
    n_days = len(closes)
    n_years = n_days / TRADING_DAYS_PER_YEAR

    # ---- 1. 年化收益率 ----
    total_return = (closes[-1] / closes[0]) - 1
    annual_return = (1 + total_return) ** (1 / n_years) - 1

    # ---- 2. 最大回撤 ----
    peak = closes[0]
    max_drawdown = 0
    max_dd_peak_date = dates[0]
    max_dd_trough_date = dates[0]
    current_peak_date = dates[0]

    for i, price in enumerate(closes):
        if price > peak:
            peak = price
            current_peak_date = dates[i]
        dd = (peak - price) / peak
        if dd > max_drawdown:
            max_drawdown = dd
            max_dd_peak_date = current_peak_date
            max_dd_trough_date = dates[i]

    # ---- 3. 夏普比率 ----
    daily_returns = []
    for i in range(1, n_days):
        daily_returns.append((closes[i] - closes[i-1]) / closes[i-1])

    mean_daily = sum(daily_returns) / len(daily_returns)
    var_daily = sum((r - mean_daily) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_daily = math.sqrt(var_daily)
    annual_vol = std_daily * math.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (annual_return - RISK_FREE_RATE) / annual_vol if annual_vol > 0 else 0

    # ---- 4. 卡玛比率 ----
    calmar = annual_return / max_drawdown if max_drawdown > 0 else 0

    # ---- 6. 历年最大回撤中位数 ----
    # 回撤相对"运行历史最高点"(跨年延续)，取每年内出现的最大回撤值，再取各年中位数
    # 与"当前回撤"口径一致，均相对历史最高点，可直接对照
    from collections import defaultdict
    year_dd = defaultdict(float)
    running_peak = closes[0]
    for d, p in zip(dates, closes):
        if p > running_peak:
            running_peak = p
        dd = (running_peak - p) / running_peak
        year = d[:4]
        if dd > year_dd[year]:
            year_dd[year] = dd

    annual_dds = sorted(year_dd[year] for year in sorted(year_dd))
    m = len(annual_dds)
    if m == 0:
        median_annual_dd = 0.0
    elif m % 2 == 1:
        median_annual_dd = annual_dds[m // 2]
    else:
        median_annual_dd = (annual_dds[m // 2 - 1] + annual_dds[m // 2]) / 2

    # ---- 7. 当前回撤（现价相对历史最高点） ----
    all_time_high = peak  # 循环结束后 peak 即历史最高价
    current_drawdown = (all_time_high - closes[-1]) / all_time_high if all_time_high > 0 else 0.0

    # ---- 5. 创新高最长天数 + 起止日期 ----
    running_peak = closes[0]
    high_indices = [0]

    for i in range(1, n_days):
        if closes[i] > running_peak:
            high_indices.append(i)
            running_peak = closes[i]

    high_indices.append(n_days - 1)

    # 最后一个真正创新高的位置（不含最后追加的终点索引）
    last_peak_idx = high_indices[-2] if len(high_indices) >= 2 else 0
    last_peak_date = dates[last_peak_idx]
    days_since_last_peak = n_days - 1 - last_peak_idx

    max_high_gap = 0
    max_high_gap_start = ""
    max_high_gap_end = ""
    for i in range(1, len(high_indices)):
        gap = high_indices[i] - high_indices[i-1]
        if gap > max_high_gap:
            max_high_gap = gap
            max_high_gap_start = dates[high_indices[i-1]]
            max_high_gap_end = dates[high_indices[i]]

    return {
        "code": code,
        "name": name,
        "start_date": dates[0],
        "end_date": dates[-1],
        "n_days": n_days,
        "start_price": round(closes[0], 3),
        "end_price": round(closes[-1], 3),
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "max_drawdown": round(max_drawdown * 100, 2),
        "max_dd_peak_date": max_dd_peak_date,
        "max_dd_trough_date": max_dd_trough_date,
        "median_annual_dd": round(median_annual_dd * 100, 2),
        "current_drawdown": round(current_drawdown * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "annual_vol": round(annual_vol * 100, 2),
        "max_high_gap_days": max_high_gap,
        "max_high_gap_start": max_high_gap_start,
        "max_high_gap_end": max_high_gap_end,
        "last_peak_date": last_peak_date,
        "days_since_last_peak": days_since_last_peak,
        "total_value_10k": round(10000 * (1 + total_return), 0),
        "closes": [{"d": dates[i], "p": round(closes[i], 3)} for i in range(n_days)],
    }


def main():
    print("📊 资产排名看板 - 数据生成中...")
    print("  拉取指数估值分位(蛋卷)...", end=" ")
    valuation = fetch_valuation()
    print(f"{len(valuation)}个指数")
    results = []

    for asset in ASSETS:
        print(f"  拉取 {asset['name']} ({asset['code']})...", end=" ")
        bars = fetch_history(asset["code"], asset["prefix"])
        if not bars:
            print("❌ 失败")
            continue
        print(f"{len(bars)}个交易日")
        metrics = compute_metrics(bars, asset["code"], asset["name"])
        metrics["trade_type"] = asset.get("trade_type", "")
        idx_code = VALUATION_INDEX_MAP.get(asset["code"])
        if idx_code and idx_code in valuation and valuation[idx_code] is not None:
            metrics["valuation_percentile"] = round(valuation[idx_code] * 100, 1)
        else:
            metrics["valuation_percentile"] = None
        if "error" in metrics:
            print(f"⚠️ {metrics['error']}")
            continue
        results.append(metrics)
        gap_info = ""
        if metrics.get("max_high_gap_start"):
            gap_info = f"  无新高: {metrics['max_high_gap_start']} ~ {metrics['max_high_gap_end']} ({metrics['max_high_gap_days']}天)"
        print(f"    年化: {metrics['annual_return']}%  回撤: {metrics['max_drawdown']}%  历年回撤中位: {metrics['median_annual_dd']}%  当前回撤: {metrics['current_drawdown']}%  夏普: {metrics['sharpe']}  卡玛: {metrics['calmar']}{gap_info}  1万→{metrics['total_value_10k']:.0f}元")

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "risk_free_rate": RISK_FREE_RATE,
        "start_date": "2020-01-01",
        "assets": results,
    }

    output_path = "/opt/quant/docs/metrics.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)

    print(f"\n✅ 数据已写入 {output_path}")
    print(f"   {len(results)} 个资产, 生成时间: {output['generated_at']}")

    print("\n📈 年化收益率排名:")
    for i, a in enumerate(sorted(results, key=lambda x: x["annual_return"], reverse=True)):
        gap_str = ""
        if a.get("max_high_gap_start"):
            gap_str = f"  {a['max_high_gap_start']}~{a['max_high_gap_end']}"
        print(f"  {i+1}. {a['name']:<15} 年化{a['annual_return']:>6.1f}%  回撤{a['max_drawdown']:>6.1f}%  夏普{a['sharpe']:>5.2f}  卡玛{a['calmar']:>5.2f}  无新高{a['max_high_gap_days']:>4.0f}天{gap_str}")


if __name__ == "__main__":
    main()
