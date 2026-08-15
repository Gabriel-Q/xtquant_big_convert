---
name: qmt-trader
description: "通过统一 CLI 脚本驱动大 QMT 迅投量化交易端的全部能力，含实时行情查询、K线历史数据、账户资产与持仓查询、委托与成交查询、买入卖出下单、撤单、板块龙虎榜北向资金财务数据等。适用于大模型辅助量化交易分析、行情研判、持仓监控、半自动下单等场景。当用户需要查看股票行情、分析K线、查询持仓资产、查看今日委托成交、下单买卖、撤单、查询北向资金龙虎榜财务数据时触发此 skill。"
---

# QMT Trader — 大模型驱动的 QMT 交易/行情工具

## 概述

本 skill 提供一个确定性 CLI 脚本 `scripts/qmt.py`，让大模型通过命令行调用大 QMT 的全部
交易与行情能力，避免每次现场写 Python 代码。所有命令默认输出 JSON（便于解析），加 `--table`
切换人类可读表格。

**前置条件**：大 QMT 进程已启动并在运行 `BIGQMT_REDIS_DRYRUN.py`（RPC 服务端就绪），
Redis/ZMQ 可连通，客户端已配置账号信息（环境变量或配置文件）。

## 快速开始

### 第 0 步：确认连通性

```bash
python scripts/qmt.py ping
```

返回 `ok: true` 且 `latency_ms` 合理（redis ~13ms / zmq ~0.7ms）即表示服务端就绪。

### 第 1 步：一键快照（资产+持仓+委托+成交）

```bash
python scripts/qmt.py snapshot
```

一次 RPC 往返返回账户全景，适合快速了解当前状态。

## 命令速查

### 行情分析

| 命令 | 用途 | 示例 |
|------|------|------|
| `tick <codes...>` | 实时五档盘口 | `tick 600000.SH 000001.SZ` |
| `kline <code>` | K线/历史行情 | `kline 600000.SH --period 1d --count 60 --dividend front` |
| `instrument <code>` | 合约详情 | `instrument 600000.SH` |
| `sector [name]` | 板块成分股/板块列表 | `sector "沪深A股"` |
| `trading-dates` | 交易日历 | `trading-dates --count 10` |
| `north` | 北向资金 | `north --period 1d` |
| `longhubang <code>` | 龙虎榜 | `longhubang 600000.SH --count 5` |
| `financial <codes...>` | 财务数据 | `financial 000001.SZ --tables Capital.CAPITAL` |
| `download <codes...>` | 下载历史数据 | `download 600654.SH --period 1d --dividend front` |
| `quote-subscribe <codes...>` | 实时全推订阅 | `quote-subscribe SH SZ --max 10` |

### 账户/持仓/委托

| 命令 | 用途 | 示例 |
|------|------|------|
| `account` | 账户资产 | `account` |
| `positions [code]` | 持仓列表 | `positions` / `positions 600000.SH` |
| `orders` | 今日委托 | `orders --cancelable` |
| `trades` | 今日成交 | `trades` |
| `snapshot` | 一键全景 | `snapshot` |

### 下单/撤单

| 命令 | 用途 | 示例 |
|------|------|------|
| `buy <code> <volume>` | 买入 | `buy 600000.SH 100 --price 7.50` |
| `sell <code> <volume>` | 卖出 | `sell 600000.SH 100 --price 7.50` |
| `cancel <order_id>` | 撤单 | `cancel 12345 --market SH` |

> 下单命令支持 `--dry-run`（只打印不下单）、`--latest`（最新价）、`--strategy`、`--remark`。

## 典型工作流

### 场景一：行情分析

分析某只股票的技术面：

```bash
# 1. 看实时盘口
python scripts/qmt.py tick 600000.SH

# 2. 拉最近 60 根日 K（前复权），输出含 MA5/MA20/MA60 统计
python scripts/qmt.py kline 600000.SH --period 1d --count 60 --dividend front

# 3. 看合约详情（名称、上市日、最小变动价位等）
python scripts/qmt.py instrument 600000.SH

# 4. 看近期龙虎榜
python scripts/qmt.py longhubang 600000.SH --count 5
```

### 场景二：持仓监控

```bash
# 一键看全景
python scripts/qmt.py snapshot

# 只看持仓（含浮动盈亏）
python scripts/qmt.py positions

# 看可撤委托
python scripts/qmt.py orders --cancelable
```

### 场景三：下单交易

```bash
# 0. 先看当前价
python scripts/qmt.py tick 600000.SH

# 1. 干跑确认参数
python scripts/qmt.py buy 600000.SH 100 --price 7.50 --dry-run

# 2. 真实下单（限价 7.50 买 100 股）
python scripts/qmt.py buy 600000.SH 100 --price 7.50 --strategy my_strat

# 3. 确认委托进了系统
python scripts/qmt.py orders

# 4. 需要时撤单
python scripts/qmt.py cancel <order_sysid> --market SH
```

### 场景四：批量行情分析

```bash
# 同时看多只股票的盘口
python scripts/qmt.py tick 600000.SH 000001.SZ 600519.SH

# 看板块成分股
python scripts/qmt.py sector "沪深A股"

# 看北向资金流向
python scripts/qmt.py north
```

## 安全须知

1. **下单默认关闭**：服务端 `rpc_allow_order_methods` 默认 `False`。必须由人工在服务端配置中
   显式开启后才能下单，否则 `buy`/`sell`/`cancel` 会报 `ORDER_DISABLED` 错误。

2. **下单前先看价**：始终先用 `tick` 确认当前价格，避免下出明显不合理的委托。

3. **超时防重复**：如果 `buy`/`sell` 报 `ORDER_TIMEOUT`，委托可能已提交。**先用 `orders` 查询确认**，
   不要直接重试，避免重复下单。

4. **strategy_name 一致性**：下单时的 `--strategy` 和查询时的 `--strategy` 必须一致。
   查全部委托用 `orders --strategy ""`（空字符串=不过滤）。

5. **实盘模式**：QMT 必须运行在实盘模式（非模拟/模型交易）才能收到完整回报。

## 脚本说明

### scripts/qmt.py

统一 CLI 入口，包含以下子命令：

- `ping` — 连通性检测（含延迟测量）
- `account` — 查询账户资产（现金/冻结/总资产/市值）
- `positions [code]` — 查询持仓（含浮动盈亏计算）
- `orders [--cancelable] [--strategy ""]` — 查询今日委托（含语义化状态名）
- `trades [--strategy ""]` — 查询今日成交
- `snapshot` — 一键全景（资产+持仓+委托+成交）
- `tick <codes...>` — 实时五档盘口（含涨跌幅计算）
- `kline <code> [--period 1d] [--count N] [--dividend front]` — K线（含 MA5/20/60 统计）
- `instrument <code>` — 合约详情
- `sector [name]` — 板块成分股/板块列表
- `trading-dates [--count N]` — 交易日历
- `north [--period 1d]` — 北向资金
- `longhubang <code> [--count N]` — 龙虎榜
- `financial <codes...> [--tables T1,T2]` — 财务数据
- `download <codes...>` — 下载历史数据到服务端
- `buy <code> <volume> [--price P] [--latest]` — 买入下单
- `sell <code> <volume> [--price P] [--latest]` — 卖出下单
- `cancel <order_id> [--market SH]` — 撤单
- `quote-subscribe <codes...> [--max N] [--timeout S]` — 实时全推行情订阅

**配置自动发现**：脚本会自动把仓库 `src/` 加入 `sys.path`，支持开发模式直接运行（无需 pip install）。
配置从环境变量（`BIGQMT_ACCOUNT_ID`/`BIGQMT_REDIS_HOST` 等）或配置文件读取。

**输出格式**：默认 JSON（`ok`/`data`/`ts` 三字段），加 `--table` 切换表格输出。错误返回 `ok: false`
+ `error`/`detail`/`code`，退出码 1。

## 参考

详细的 API 参数、返回值结构、常量定义和已知陷阱见 `references/api_reference.md`。
当命令速查不够用时（如需要直接 RPC 调用、查看信用交易类型、了解回调系统等），查阅该文件。
