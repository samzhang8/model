# 🔬 Model — A股量化因子与策略研究系统

> **单一真相源。一切以真实数据说话。不画饼，只验真。**

[![Pages](https://img.shields.io/badge/Live_Dashboard-samzhang8.github.io%2Fmodel-blue)](https://samzhang8.github.io/model/)
[![Strategies](https://img.shields.io/badge/Strategies-93-orange)](https://samzhang8.github.io/model/strategy.html)
[![Factors](https://img.shields.io/badge/Factors-908-green)](https://samzhang8.github.io/model/factors.html)

---

## 📊 实时面板

| 面板 | 链接 | 说明 |
|------|------|------|
| 🏠 **资产排名** | [/model/](https://samzhang8.github.io/model/) | 68只A股ETF全量排名，含收益率/夏普/回撤/相关性矩阵 |
| 📈 **策略仪表盘** | [/model/strategy.html](https://samzhang8.github.io/model/strategy.html) | 93个策略回测结果，净值曲线，实盘信号 |
| 🧬 **因子监控** | [/model/factors.html](https://samzhang8.github.io/model/factors.html) | 908个因子IC/IR排名，滚动窗口，动量衰减预警 |
| 📡 **订阅信号** | [/model/subscribe.html](https://samzhang8.github.io/model/subscribe.html) | 策略信号订阅页面 |

---

## 🏗 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                     数据层                                │
│  ClickHouse (115服务器)  ←  RQData日频/财务数据           │
│  腾讯API ← ETF实时行情                                    │
└──────────────────┬───────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────┐
│                   计算层                                   │
│  gen_strategy.py      → 策略回测 (5年历史/半年调仓)       │
│  gen_metrics.py       → 资产业绩指标                      │
│  factor_monitor_v2.py → 因子IC滚动窗口 (ClickHouse直连)   │
│  verify_all.py        → 7道验真流程                       │
│  daily_signal.py      → 每日交易信号                      │
│  daily_scanner.py     → 每日市场扫描                      │
│  multifactor_synthesis.py → 多因子合成                    │
└──────────────────┬───────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────┐
│                   输出层 (docs/)                           │
│  metrics.json       → 68 ETF 完整指标 (3.3MB)             │
│  strategies.json    → 93 策略回测结果 (4.1MB)             │
│  factor_metrics.json → 908 因子IC数据 (2.7MB)             │
│  *.html             → GitHub Pages 前端面板               │
└──────────────────────────────────────────────────────────┘
```

---

## 📁 项目结构

```
model/
├── docs/                          # GitHub Pages 部署目录
│   ├── index.html                 # 资产排名主页
│   ├── strategy.html              # 策略面板
│   ├── factors.html               # 因子面板
│   ├── subscribe.html             # 信号订阅
│   ├── metrics.json               # 68 ETF 全量指标
│   ├── strategies.json            # 93 策略 + 净值历史
│   ├── factor_metrics.json        # 908 因子 IC 数据
│   ├── audit_v5.json              # 策略审计结果
│   └── verification_report.json   # 验真报告
│
├── gen_strategy.py                # 策略回测引擎（主力）
├── gen_metrics.py                 # 资产业绩指标生成
├── daily_signal.py                # 每日信号日报
├── daily_scanner.py               # 每日市场扫描
├── daily_factor_ic.py             # 每日因子IC计算
├── risk_control.py                # 风控模块（止损/仓位）
├── verify_all.py                  # 7道验真流程
├── validate_strategies.py         # 策略校验
├── multifactor_synthesis.py       # 多因子合成
├── factor_monitor_v2.py           # 因子监控仪表盘（115 ClickHouse）
├── asset_tracker.py               # 资产追踪
├── report_positions.py            # 持仓报告
│
├── phoenix_v2.py ~ v20.py         # Phoenix策略迭代 (v2→v20)
├── arxiv_factors_*.py             # arXiv论文因子复现
│
├── strategies/                    # 独立策略脚本
│   ├── low_turnover_juejin.py     # 低换手率掘金信号
│   └── low_turnover_backtest.py   # 低换手率回测
│
└── reproduction_log.md            # 论文复现日志
```

---

## 🔬 验真体系

不是跑个回测就完事。每一份结果进面板前，必须过7道关：

| # | 关卡 | 标准 | 说明 |
|---|------|------|------|
| 1 | IC稳定性 | Bootstrap CI区间不含0 | 不是运气 |
| 2 | OOS留存 | 样本外IC ≥ 样本内70% | 不是过拟合 |
| 3 | 成本韧性 | 30bp成本后IC不归零 | 不是纸上富贵 |
| 4 | 市值中性 | 扣除市值暴露后仍有alpha | 不是赌大小盘 |
| 5 | 市场分段 | ≥3个市场周期保持正向 | 不是靠一轮牛市 |
| 6 | 换手率合理 | 隐含换手率 < 50% | 不是高频幻觉 |
| 7 | 策略复验 | 样本外年化 > 10% | 不是因子层面有意义但策略层面无效 |

> `verify_all.py` 一键跑全量验真，输出可投/待验证/不可用三元分类。

---

## 📈 当前结果摘要

### 策略
| 指标 | 数值 |
|------|------|
| 总策略数 | 93 |
| 经验真盈利 | **2 个** (2.2%) |
| 面板平均声称年化 | +16.8% |
| 真实平均年化 | -0.025% |

### 唯二真实盈利策略
| 策略 | 年化 | Sharpe | MDD |
|------|:--:|:--:|:--:|
| N5+止损3% 🔥 | +27.7% | 0.92 | 28.3% |
| N10+止损3% | +22.2% | 0.78 | 32.1% |

> 85%的策略是过拟合噪声。验证比挖掘重要一百倍。

### 因子
| 指标 | 数值 |
|------|------|
| 总因子数 | 908 |
| 数据源 | ClickHouse (115服务器) |
| 覆盖范围 | Alpha158 + 学术论文 + 自研 |
| 监控维度 | IC均值 / IR / 胜率 / 滚动3m/6m/12m / 衰减预警 |

---

## 🚀 快速开始

### 1. 更新策略面板
```bash
# 拉取最新行情 → 重跑所有策略 → 生成HTML
python3 gen_strategy.py
python3 gen_metrics.py
# 推送 docs/ 到 GitHub Pages
```

### 2. 每日信号
```bash
python3 daily_signal.py     # 生成 /tmp/daily_report.md
python3 daily_scanner.py    # 全市场扫描
```

### 3. 全量验真
```bash
python3 verify_all.py       # 7道验真 → 三元分类
```

### 4. 因子监控 (115服务器)
```bash
# 需要 115 上的 ClickHouse
/usr/bin/python3 /opt/quant/factor_monitor_v2.py
```

---

## 📡 数据源

| 数据 | 来源 | 更新频率 |
|------|------|:--:|
| ETF日线行情 | 腾讯证券API | 每日 |
| ETF基本信息 | metrics.json | 手动维护 |
| 因子IC数据 | ClickHouse (115服务器) | 每月 |
| 财务数据 | RQData | 每日 |
| 策略回测数据 | gen_strategy.py | 按需 |

---

## 🔄 Phoenix策略演进

从 v2 到 v20，记录了策略思想从简单到复杂的完整进化路径：

| 版本 | 方向 | 关键突破 |
|------|------|------|
| v2-v8 | 基础因子轮动 | 单因子→多因子过渡 |
| v9-v11 | 真实化 | 引入成本/滑点/容量约束 |
| v12 | 因子分解 | 收益拆解为alpha+beta+噪声 |
| v13 | MA扫描 | 移动平均信号扫描 |
| v14 | 频率扫描 | 调仓频率最优解 |
| v15 | 多因子合成 | 等权→IC加权→ML加权 |
| v16 | 诊断 | 策略归因诊断 |
| v17 | 趋势 | 趋势/反转双模 |
| v18 | All-A | 全A股选股 |
| v19 | ETF轮动 | ETF动量/风格轮动 |
| v20 | 风格轮动 | 大小盘/价值成长切换 |

---

## 📝 论文复现

`reproduction_log.md` 记录了已复现的学术论文因子：

- **LSY三因子** (Liu-Stambaugh-Yuan 2019) — Size/EP/Turn，OOS 25.5%
- **MAX效应** (Bali-Cakici-Whitelaw 2011) — A股MAX存在但OOS衰减严重
- **Amihud非流动性** (2002) — A股完全失效
- **52周高** (George-Hwang 2004) — OOS 106%年化，极稳定
- **低波动率** (Blitz-van Vliet 2007) — 20.3%年化
- **动量** (Jegadeesh-Titman 1993) — A股中期动量有效

---

## ⚠️ 核心理念

1. **无法证明等于无用。** 所有策略必须通过真实数据、样本外验证。
2. **面板数字不等于真相。** 93个策略中91个是过拟合——验证比挖掘重要100倍。
3. **单一真相源。** 所有数据从真实回测产出，不凭记忆，不靠感觉。
4. **持续演进。** Phoenix v2→v20 的版本记录本身就是最有价值的资产。

---

## 🔗 相关项目

- [agentmatrix-research](https://github.com/samzhang8/agentmatrix-research) — Agent驱动的量化研究框架
- [AlphaEval](https://github.com/samzhang8/AlphaEval) — Alpha因子评估

---

*最后更新：2026-07-06 · 验真驱动，数据说话。*
