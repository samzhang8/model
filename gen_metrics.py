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
import gzip
from datetime import datetime, timedelta

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

# ETF代码 -> (理杏仁地区, 理杏仁指数代码)：补充蛋卷未收录的细分行业/港股/细分宽基
# 2026-09-20 接入理杏仁，估值分位从 24 扩到 ~127 个股票类 ETF
LIXINGER_INDEX_MAP = {
    # 宽基(补蛋卷未覆盖)
    "159949": ("cn", "399673"),   # 创业板50
    "588020": ("cn", "000690"),   # 科创成长
    "159780": ("cn", "931643"),   # 科创创业50
    "159967": ("cn", "399296"),   # 创业板成长(创成长)
    "159552": ("cn", "932000"),   # 中证2000
    # 红利/风格
    "159207": ("cn", "930838"),   # 高股息(CS高股息)
    # 港股/港股通/中概
    "513120": ("cn", "931787"),   # 港股创新药
    "513090": ("cn", "930709"),   # 香港证券
    "159570": ("cn", "987018"),   # 港股通创新药
    "517520": ("cn", "931238"),   # 黄金股(SSH黄金股票)
    "513330": ("hk", "HSIII"),    # 恒生互联网
    "520500": ("hk", "HSIDI"),    # 恒生创新药
    "159792": ("cn", "931637"),   # 港股通互联网
    "159892": ("hk", "HSHCI"),    # 恒生医药(医疗保健)
    "159131": ("cn", "930967"),   # 港股通信息技术
    "159506": ("cn", "931250"),   # 港股通创新药医疗
    "513060": ("hk", "HSHCI"),    # 恒生医疗
    "159366": ("cn", "932069"),   # 港股医疗(港股通医疗主题)
    "513190": ("cn", "H11146"),   # 港股通金融(内地金融)
    "513750": ("cn", "931024"),   # 港股通非银
    "513200": ("cn", "932069"),   # 港股通医药
    "159262": ("cn", "987008"),   # 港股通科技
    "159605": ("cn", "H11136"),   # 中概互联(中国互联网)
    "513980": ("cn", "931574"),   # 港股科技
    "159636": ("cn", "987008"),   # 港股通科技30
    "520600": ("cn", "931239"),   # 港股通汽车
    "513360": ("cn", "931456"),   # 教育(中国教育)
    "513070": ("cn", "931454"),   # 港股通消费
    "159750": ("cn", "931574"),   # 港股科技50
    "513970": ("hk", "HSCGSI"),   # 恒生消费
    "513550": ("cn", "930931"),   # 港股通50
    "159976": ("cn", "931000"),   # 湾创(大湾区)
    "513900": ("cn", "930957"),   # 港股通100(港股通中国100)
    # A股行业/主题
    "515880": ("cn", "931160"),   # 通信(通信设备)
    "159516": ("cn", "931743"),   # 半导体设备(材料设备)
    "512480": ("cn", "H30184"),   # 半导体
    "512400": ("cn", "000819"),   # 有色金属
    "159995": ("cn", "H30007"),   # 芯片(芯片产业)
    "159992": ("cn", "931152"),   # 创新药(CS创新药)
    "159326": ("cn", "931994"),   # 电网设备
    "159530": ("cn", "H30590"),   # 机器人
    "159732": ("cn", "980030"),   # 消费电子
    "159611": ("cn", "H30199"),   # 电力
    "159819": ("cn", "931071"),   # 人工智能
    "159206": ("cn", "931594"),   # 卫星(卫星产业)
    "560860": ("cn", "H11059"),   # 工业有色
    "159852": ("cn", "930601"),   # 软件(中证软件)
    "159865": ("cn", "930707"),   # 养殖(中证畜牧)
    "159869": ("cn", "930901"),   # 游戏(动漫游戏)
    "516150": ("cn", "930598"),   # 稀土
    "512710": ("cn", "931066"),   # 军工龙头
    "159566": ("cn", "932246"),   # 储能电池
    "159755": ("cn", "931719"),   # 电池(CS电池)
    "159870": ("cn", "000813"),   # 化工(细分化工)
    "159859": ("cn", "399441"),   # 生物医药
    "159698": ("cn", "399365"),   # 粮食(国证粮食)
    "159851": ("cn", "930986"),   # 金融科技
    "562800": ("cn", "930632"),   # 稀有金属(CS稀金属)
    "159766": ("cn", "930633"),   # 旅游
    "159825": ("cn", "000949"),   # 农业(中证农业)
    "159227": ("cn", "930875"),   # 航空航天(空天军工)
    "159997": ("cn", "930652"),   # 电子(CS电子)
    "560280": ("cn", "931752"),   # 工程机械
    "562550": ("cn", "399438"),   # 绿电(绿色电力)
    "159930": ("cn", "000928"),   # 能源(中证能源)
    "515030": ("cn", "930997"),   # 新能源车
    "159697": ("cn", "H11057"),   # 石油(石化产业)
    "159667": ("cn", "931866"),   # 工业母机(中证机床)
    "516510": ("cn", "930851"),   # 云计算
    "159309": ("cn", "931248"),   # 油气(油气资源)
    "515790": ("cn", "931151"),   # 光伏
    "516160": ("cn", "000941"),   # 新能源
    "159883": ("cn", "H30217"),   # 医疗器械
    "512200": ("cn", "931775"),   # 房地产
    "510230": ("cn", "000018"),   # 金融(180金融)
    "517900": ("cn", "931039"),   # 银行AH
    "159546": ("cn", "932087"),   # 集成电路
    "512670": ("cn", "399973"),   # 国防
    "515400": ("cn", "930902"),   # 大数据(中证数据)
    "560710": ("cn", "932420"),   # 船舶(智选船舶产业)
    "159625": ("cn", "399438"),   # 绿色电力
    "159998": ("cn", "H30182"),   # 计算机
    "561330": ("cn", "931892"),   # 矿业(有色矿业)
    "515210": ("cn", "930606"),   # 钢铁(中证钢铁)
    "516620": ("cn", "930781"),   # 影视(中证影视)
    "159731": ("cn", "H11057"),   # 石化(石化产业)
    "560080": ("cn", "930641"),   # 中药
    "516910": ("cn", "930716"),   # 物流(CS物流)
    "159996": ("cn", "930697"),   # 家电(家用电器)
    "563010": ("cn", "931235"),   # 电信(中证电信)
    "159939": ("cn", "000993"),   # 信息技术(全指信息)
    "159745": ("cn", "931009"),   # 建材(建筑材料)
    "515170": ("cn", "000807"),   # 食品饮料
    "159616": ("cn", "930910"),   # 农牧(农牧渔)
    "562700": ("cn", "931230"),   # 汽车零部件
    "515650": ("cn", "000126"),   # 消费50
    "516820": ("cn", "931484"),   # 医疗创新(CS医药创新)
    "159666": ("cn", "H30171"),   # 交通运输(运输指数)
    "159837": ("cn", "930743"),   # 生物科技(中证生科)
    "159230": ("cn", "931855"),   # 通用航空
    "159811": ("cn", "931079"),   # 5G(5G通信)
    "516520": ("cn", "931783"),   # 智能驾驶
    "512220": ("cn", "399610"),   # TMT(TMT50)
    "516970": ("cn", "930608"),   # 基建(中证基建)
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


def fetch_lixinger_valuation() -> dict:
    """从理杏仁API拉取指数估值分位，返回 {理杏仁指数代码: percentile(0~1)}
    优先PE(TTM)10年分位(pe_ttm.y10.mcw.cvpos)，PE无效(≤0/缺失)时退回PB分位"""
    token_path = "/opt/quant/.lixinger_token"
    try:
        with open(token_path) as f:
            token = f.read().strip()
    except Exception:
        print("  理杏仁token读取失败(跳过)", file=sys.stderr)
        return {}
    if not token:
        return {}

    # 按地区分组去重
    by_area = {}
    for _etf, (area, idx) in LIXINGER_INDEX_MAP.items():
        by_area.setdefault(area, [])
        if idx not in by_area[area]:
            by_area[area].append(idx)

    metrics = ["pe_ttm.y10.mcw.cvpos", "pe_ttm.mcw", "pb.y10.mcw.cvpos", "pb.mcw"]
    result = {}

    def _post(url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)

    today = datetime.now()
    for area, codes in by_area.items():
        for i in range(0, len(codes), 100):
            batch = codes[i:i + 100]
            got = False
            for back in range(10):
                d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
                body = {"token": token, "date": d,
                        "stockCodes": batch, "metricsList": metrics}
                try:
                    resp = _post(f"https://open.lixinger.com/api/{area}/index/fundamental", body)
                except Exception:
                    continue
                if resp.get("code") == 1 and resp.get("data"):
                    for item in resp["data"]:
                        code = item.get("stockCode")
                        pe = item.get("pe_ttm.mcw")
                        pe_pct = item.get("pe_ttm.y10.mcw.cvpos")
                        pb = item.get("pb.mcw")
                        pb_pct = item.get("pb.y10.mcw.cvpos")
                        if pe and pe > 0 and pe_pct is not None:
                            pct = pe_pct
                        elif pb and pb > 0 and pb_pct is not None:
                            pct = pb_pct
                        else:
                            pct = None
                        result[code] = pct
                    got = True
                    break
            if not got:
                print(f"  理杏仁 {area} 部分指数无数据: {batch[:3]}...", file=sys.stderr)
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
    print("  拉取指数估值分位(理杏仁)...", end=" ")
    lix_valuation = fetch_lixinger_valuation()
    print(f"{len(lix_valuation)}个指数")
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
            lix = LIXINGER_INDEX_MAP.get(asset["code"])
            if lix and lix[1] in lix_valuation and lix_valuation[lix[1]] is not None:
                metrics["valuation_percentile"] = round(lix_valuation[lix[1]] * 100, 1)
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
