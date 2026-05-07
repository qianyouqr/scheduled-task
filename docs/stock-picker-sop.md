# stock_picker — 定时选股 SOP

定时选股场景：对**全市场/指定板块**跑一组筛选排序公式 → 取 TopN 快照 → 每次推送。
与 signal_monitor 的核心区别：**无固定资产池、无冷静期，每次都是当前市场截面快照**。

---

## 概念

| 术语 | 说明 |
|------|------|
| 公式链 | 若干条 quant-buddy 公式，由 quant-buddy-skill 生成，scheduled-task 原样透传 |
| result_formula | 最终判定"入选"的那条公式名，非零值为入选 |
| value_columns | 推送报告中展示的字段列表（每列指定来源公式名和标签） |
| TopN | `limit` 控制最多展示几条，按 `sort_by` 字段排序 |
| cooldown_days | 默认 0（每次都推），无需冷静期 |

---

## 内置 preset

| preset | 说明 | 触发条件 |
|--------|------|----------|
| `stock_picker_value` | PE<15 且股息率>3% Top10 | A股全市场筛选，按股息率降序 |

---

## job.json 格式（stock_picker）

```json
{
  "task_type": "stock_picker",
  "name": "低PE高股息 Top10",
  "formulas": [
    "条件掩码 = 板块(万得全A) * (\"A股市盈率（PE, TTM）〔估值数据〕\"<15) * (\"A股股息率〔估值数据〕\">3)",
    "排序值 = \"条件掩码\" * \"A股股息率〔估值数据〕\"",
    "Top10股息率 = 取前(\"排序值\", 10, 返回数值)",
    "Top10PE = 取前(\"排序值\", 10) * \"A股市盈率（PE, TTM）〔估值数据〕\""
  ],
  "result_handler": {
    "mode": "topn_by_mask",
    "result_formula": "Top10股息率",
    "value_columns": [
      {"label": "股息率(%)", "from": "Top10股息率", "format": "{:.2f}"},
      {"label": "PE(TTM)",   "from": "Top10PE",     "format": "{:.2f}"}
    ],
    "sort_by": "股息率(%)",
    "sort_order": "desc",
    "limit": 10
  },
  "runMultiFormula_args": {
    "begin_date": "auto_minus_60d",
    "use_minute_data": true
  },
  "cooldown_days": 0,
  "notification": {
    "wecom": {
      "enabled": true,
      "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
    },
    "push_when": "always"
  },
  "schedule": {
    "cron": "*/30 9-15 * * 1-5"
  },
  "report": {
    "report_mode": "incremental"
  }
}
```

---

## result_handler 字段说明

| 字段 | 说明 |
|------|------|
| `mode` | 目前仅支持 `topn_by_mask`：取 `result_formula` 末日列非零 ticker，按 `sort_by` 排序，截 `limit` 行 |
| `result_formula` | 决定"入选"的公式名，末日列非零值为入选 |
| `value_columns` | 展示列列表；`from` 指来源公式名，`label` 为展示标签，`format` 为数字格式 |
| `sort_by` | 用于排序的 `value_columns` 中某列的 `label` |
| `sort_order` | `desc`（降序）/ `asc`（升序） |
| `limit` | 最多展示行数 |

---

## runMultiFormula_args.begin_date 占位符

| 值 | 实际计算 | 适用 |
|----|----------|------|
| `"auto_minus_60d"` | today - 90天（约60个交易日） | 常规选股（默认） |
| `"auto_minus_120d"` | today - 180天（约120个交易日） | 需要更长回溯期 |
| 整数（如 `20260101`） | 直接使用 | 固定起始日期 |

---

## 创建流程

### 场景 D：已有 preset 公式（推荐）

```bash
# 1. 从 preset 建 job
python scripts/cli.py add <id> --task-type stock_picker --from preset:stock_picker_value

# 2. 配置企微推送（必须询问用户 webhook URL）
python scripts/cli.py set <id> notification.wecom.webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
python scripts/cli.py set <id> notification.wecom.enabled true

# 3. 配置调度（工作日盘中每30分钟）
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"

# 4. 配置报告模式
python scripts/cli.py set <id> report.report_mode incremental

# 5. 验证
python scripts/cli.py run <id> --dry-run

# 6. 注册 Windows 计划任务
python scripts/cli.py apply-schedule <id>
```

> 若用户未提供 webhook：`cli.py set <id> notification.wecom.enabled false`，告知后续可用 `set ... webhook <url>` + `set ... enabled true` 开启。

### 场景 E：没有 preset，需要自定义公式

```bash
# 1. 建空骨架
python scripts/cli.py add <id> --task-type stock_picker --scaffold

# 2. 对话层调 quant-buddy-skill 生成公式（走 quant-standard.md 流程）
#    quant-buddy 负责：confirmDataMulti 查字段口径、TopN 算子（取前）、全市场写法（板块(万得全A)）

# 3. 写入公式
python scripts/cli.py set <id> formulas '["公式1","公式2","公式3"]'

# 4. 写入 result_handler
python scripts/cli.py set <id> result_handler \
  '{"mode":"topn_by_mask","result_formula":"Top结果","value_columns":[{"label":"字段","from":"公式名","format":"{:.2f}"}],"sort_by":"字段","sort_order":"desc","limit":10}'

# 5. 校验
python scripts/cli.py validate <id>

# 6. 走场景 D 步骤 2-6
```

---

## 注意事项

- stock_picker **无 `asset_source` 字段**，不需要资产池文件
- 公式中的字段名（如 `A股市盈率（PE, TTM）〔估值数据〕`）须与 quant-buddy confirmDataMulti 实测结果一致，全角括号/中文逗号等**原文保留**
- 运行时**无 LLM**：公式已物化在 job.json，schtasks 触发时纯执行
- `push_when` 建议设为 `always`（每次都推快照），不同于 signal_monitor 的 `triggered_only`
