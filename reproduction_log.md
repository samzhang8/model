
### 2026-06-30 LSY三因子复现

**论文**: Liu, Stambaugh & Yuan (2019) "Size and Value in China"
**方法**: 复现三个因子：Size(小盘)、EP(价值)、Turn(换手)
**结果**:
- Size: 16.3% 扣成本 (样本内 20.5%)
- EP: 25.5% 扣成本 (样本内 30.0%)
- Turn: 23.6% 扣成本 (样本内 28.0%)
- 3因子组合 OOS: 25.5% (优于样本内22.1%)
**入库**: 3因子+3策略

### 2026-06-30 MAX效应复现

**论文**: Bali, Cakici & Whitelaw (2011) "Maxing Out: Stocks as Lotteries"
**因子**: MAX(月内最大日收益反转) + IVOL(异质波动率)
**结果**: MAX+Turn=19.3%(30bp) OOS=10.2%, IVOL=18.3%(30bp) OOS=12.8%
**结论**: A股MAX效应存在但OOS衰减严重，不如低换手率/小盘因子
**入库**: 2因子+3策略

### 2026-06-30 三论文并行复现

**论文**: Amihud(2002)+George-Hwang(2004)+Jegadeesh-Titman(1993)+Blitz-van Vliet(2007)
**结果**: 低波动率20.3%最佳, 52周高OOS=106%最稳定, Amihud在A股完全失效(-1.3%)
**入库**: 4因子+3策略
