# Job Schema & 公共字段

---

## 目录结构

```
skill_dir/scheduled-task/
├── SKILL.md                  # skill 入口（保留 front matter，精简为索引）
├── docs/
│   ├── signal-monitor-sop.md # signal_monitor SOP + 资产池 SOP
│   ├── stock-picker-sop.md   # stock_picker SOP
│   ├── cli-reference.md      # CLI 速查 + 工作流 A-G
│   └── job-schema.md         # 本文件：公共字段 + 约束
├── jobs/
│   └── <job_id>/
│       ├── job.json          # 任务配置（由 cli.py 生成和维护）
│       ├── assets.xlsx       # 资产池文件（推荐，excel 类型时）
│       ├── assets.csv        # 资产池文件（csv 类型时）
│       └── history/          # 每次运行的结果快照（JSON）
├── lib/
│   ├── scanner.py            # 信号扫描核心（资产循环、触发判定、稀疏矩阵修复）
│   ├── reporter.py           # 推送报文渲染（WeCom Markdown）
│   ├── scheduler.py          # schtasks 注册/卸载封装
│   └── ...
├── scripts/
│   └── cli.py                # 统一 CLI 入口
└── presets/
    ├── signal_monitor/       # signal_monitor preset 定义
    └── stock_picker/         # stock_picker preset 定义
```

---

## 公共字段（所有 task_type 通用）

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `task_type` | string | ✅ | — | `signal_monitor` 或 `stock_picker` |
| `name` | string | ✅ | — | 任务显示名（用于推送标题） |
| `description` | string | 否 | — | 任务描述（仅注释用） |
| `cooldown_days` | int | 否 | `7` | 冷静期天数（0=每次都推）|
| `schedule.cron` | string | ✅ | — | 5段 cron 表达式（schtasks 格式） |
| `notification.wecom.enabled` | bool | ✅ | `false` | 是否开启企微推送 |
| `notification.wecom.webhook` | string | 条件必填 | — | 企微机器人 webhook URL |
| `notification.push_when` | string | 否 | `triggered_only` | `triggered_only` / `always` |
| `report.report_mode` | string | 否 | `incremental` | `incremental` / `full` |

---

## signal_monitor 专属字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `asset_source.type` | string | ✅ | `excel` / `csv` / `inline` |
| `asset_source.path` | string | 条件必填 | excel/csv 时必填，相对 jobs/<id>/ 的路径 |
| `asset_source.assets` | array | 条件必填 | inline 时必填，每项含 `name` 和 `ticker` |
| `asset_source.disabled_tickers` | array | 否 | 临时屏蔽的 ticker 列表 |
| `signal.formulas` | array | ✅ | quant-buddy 公式链，第一条含 `{ASSETS}` 占位符 |
| `signal.trigger_formula` | string | ✅ | 触发判定的公式名，末日值≥0.5为触发 |
| `signal.display_fields` | array | 否 | 推送中展示的字段名列表 |
| `signal.lookback_days` | int | 否 | 历史回溯天数（默认60） |
| `signal.category.direction` | string | 否 | `buy` / `sell` / `neutral` |
| `signal.category.label` | string | 否 | 信号标签（如"抄底"、"突破"） |
| `analysis_hook.enabled` | bool | 否 | 是否启用触发后 LLM 分析 |

---

## stock_picker 专属字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `formulas` | array of string | ✅ | quant-buddy 公式链（字符串列表） |
| `result_handler.mode` | string | ✅ | `topn_by_mask` |
| `result_handler.result_formula` | string | ✅ | 决定入选的公式名 |
| `result_handler.value_columns` | array | ✅ | 展示列定义（label/from/format） |
| `result_handler.sort_by` | string | 否 | 排序字段（value_columns 中某 label） |
| `result_handler.sort_order` | string | 否 | `desc` / `asc` |
| `result_handler.limit` | int | 否 | 最多行数（默认10） |
| `runMultiFormula_args.begin_date` | string/int | 否 | 起始日期，支持占位符（见 stock-picker-sop.md） |
| `runMultiFormula_args.use_minute_data` | bool | 否 | 是否使用分钟级数据 |

---

## 关键约束 & 心智模型

1. **scheduled-task 不生成公式，只透传**
   公式字符串由 quant-buddy-skill 负责确认字段口径和生成；scheduled-task 原样写入 job.json 后执行。

2. **运行时无 LLM**
   schtasks 触发后只运行 Python 脚本，不调用任何 LLM。所有决策（公式、阈值、排序）均已物化在 job.json。

3. **稀疏矩阵处理（scanner.py）**
   quant 平台对布尔公式返回稀疏矩阵：未触发 = NaN（不是 0）。scanner.py 对此的正确判断：
   - 如果 trigger_formula 值缺失 **且** 有展示字段数据 → `trigger=False`（正常未触发）
   - 如果 trigger_formula 值缺失 **且** 无任何展示字段数据 → 归入 anomalies（数据异常）

4. **cooldown_days=0 的特殊语义**
   0 表示"每次都推，不去重"。代码使用 `job.get("cooldown_days", 7)`（不是 `x or 7`），确保 0 被正确处理。

5. **资产池类型选择默认规则**
   只要用户提供了股票列表，必须生成 `assets.xlsx`，配置 `type=excel`。
   inline 仅用于用户明确指定、临时测试、≤5只一次性场景。
   详见 [signal-monitor-sop.md](./signal-monitor-sop.md) 资产池 SOP 章节。

6. **公式字段名原文保留**
   quant 平台字段名含全角括号、中文逗号（如 `A股市盈率（PE, TTM）〔估值数据〕`），写入 job.json 时原样保留，不能替换为半角。

---

## cron 表达式常用示例

| cron | 含义 |
|------|------|
| `*/15 9-15 * * 1-5` | 工作日 9-15 点，每15分钟 |
| `*/30 9-15 * * 1-5` | 工作日 9-15 点，每30分钟 |
| `0 9 * * 1-5` | 工作日 9:00 整点 |
| `0 15 * * 1-5` | 工作日收盘（15:00） |
| `0 8,12,18 * * *` | 每天 8:00 / 12:00 / 18:00 |
