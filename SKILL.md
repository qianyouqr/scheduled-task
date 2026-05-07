---
name: scheduled-task
description: >
  通用量化定时任务框架。一个 skill 管理 N 个独立定时 job，每个 job 有自己的公式/调度/推送/报告。
  支持两种 task_type：
  ① signal_monitor（信号监控）— 对固定资产池逐资产判信号、冷静期去重、触发推送；
  ② stock_picker（定时选股）— 全市场/指定板块筛选+排序+TopN 快照推送，无冷静期。
  核心职责：调度（Windows schtasks）+ 执行（调 quant-buddy-skill）+ 推送（企微）+ 报告/历史/CLI 管理。
  公式由 quant-buddy-skill 负责生成（confirmDataMulti / TopN 算子等）；scheduled-task 只透传公式字符串，不解析语义。
  当用户说到：定时任务、信号监控、定时选股、盘中扫描、每 N 分钟推送、Top10 推送、加一个监控、改下定时、
  暂停/恢复/删除任务、查看任务列表、为什么没提醒、推个测试到群里、立即跑一遍、scheduled scan，都触发本 skill。
metadata:
  requires: "quant-buddy-skill"
---

# scheduled-task — 通用量化定时任务框架

把"跑公式 → 提取结果 → 出报告 → 推企微 → 定时调度"这套骨架抽象成一个**多 job** 的模板 skill。
每个 job 是 `jobs/<id>/job.json` 里一段声明式配置，由统一的 `scripts/cli.py` 驱动。

**核心数据源**：quant-buddy-skill（统一 A/港/美股/指数/ETF/期货）
**调度**：Windows schtasks（每 job 独立任务名 `ScheduledTask_<id>`）
**通知**：内置企微 webhook（每 job 可独立 webhook + @ 列表）

---

## 详细文档（docs/）

| 文档 | 内容 |
|------|------|
| [docs/signal-monitor-sop.md](docs/signal-monitor-sop.md) | signal_monitor SOP：触发判定、冷静期、**资产池文件管理规则**、信号 presets、完整工作流、analysis_hook |
| [docs/stock-picker-sop.md](docs/stock-picker-sop.md) | stock_picker SOP：result_handler 字段、begin_date 占位符、工作流 D/E |
| [docs/cli-reference.md](docs/cli-reference.md) | CLI 速查表、工作流 A-G（含暂停/恢复/删除）、常见问题排查 |
| [docs/job-schema.md](docs/job-schema.md) | 目录结构、公共字段、signal_monitor/stock_picker 专属字段、关键约束 & 心智模型、cron 示例 |

---

## 职责边界（最重要）

| 维度 | scheduled-task 干什么 | quant-buddy-skill 干什么 |
|---|---|---|
| 公式字符串 | 原样存进 job.json，原样透传给 quant-buddy | **唯一作者**；负责 confirmDataMulti、口径选择、TopN 算子（取前）、全市场写法（板块(万得全A)）、隐含约束 |
| 字段名/口径 | 不解析、不校验语义 | confirmDataMulti 查询、data_catalog 预设 |
| 调度/推送/报告/历史/CLI | **全包** | 不参与 |

**双入口 job 创建**：
- **A. 已有公式**（推荐）：直接用 `--from preset:...` 或 `--formulas-file <file>` 或
  `--scaffold` 后 `cli.py set ... formulas ...`，公式字符串已是 quant-buddy 可跑的形态。
- **B. 没有公式**：先 `cli.py add ... --scaffold` 建空骨架，再在对话里调 **quant-buddy-skill**
  生成公式（走 quant-standard.md 流程），最后 `cli.py set ... formulas <list>` 写回。
  CLI 不内置 LLM，公式生成发生在对话层。

运行时（schtasks 触发）永远是路径 A：纯执行、确定性、无 LLM。

---

## 两种 task_type

### task_type = signal_monitor（信号监控，向后兼容默认）

对**固定资产池**（excel / csv / inline）逐资产跑公式 → 判触发 → 冷静期去重 → 推送触发列表。

**触发判定**：`trigger_formula` 末日值 ≥ 0.5 为触发。
**冷静期**：`cooldown_days` 内同一 ticker 不重复推。
**结果键**：`triggered[]` / `cooled_down[]` / `all_stocks[]`

```json
{
  "task_type": "signal_monitor",
  "asset_source": {"type": "excel", "path": "assets.xlsx", "disabled_tickers": []},
  "signal": {
    "category": {"direction": "buy", "label": "抄底"},
    "formulas": [
      {"name": "资产池收盘价", "expression": "资产池构造({ASSETS}) * \"全市场每日收盘价\""},
      {"name": "MA20",  "expression": "平均(\"资产池收盘价\", 20)"},
      {"name": "下轨",  "expression": "\"MA20\" - 2 * 标准差(\"资产池收盘价\", 20)"},
      {"name": "信号",  "expression": "\"资产池收盘价\" < \"下轨\""}
    ],
    "trigger_formula": "信号",
    "display_fields": ["资产池收盘价", "MA20", "下轨"],
    "lookback_days": 60
  },
  "cooldown_days": 7
}
```

---

### task_type = stock_picker（定时选股）

对**全市场/指定板块**跑一组筛选排序公式 → 取 TopN 快照 → 每次都推。
公式由 quant-buddy 生成，scheduled-task **原样透传**。

> ⚠️ **公式分批约束（scanner.py 自动执行，无需 Agent 干预）**：quant-buddy 服务端单次公式数硬上限为 **10 条**。`scanner.py` 在 `run_stock_picker()` 中已内置分批循环（每批 ≤10 条，同一 `task_id` 跨批次复用变量）。Agent 和用户**不需要**手动分批，`formulas` 数组可以完整写入 job.json，执行时自动切分。

```json
{
  "task_type": "stock_picker",
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
  "cooldown_days": 0
}
```

**result_handler.mode**：
- `topn_by_mask`：取 `result_formula` 末日列非零 ticker，按 `sort_by` 排序，截 `limit` 行。

**`runMultiFormula_args.begin_date` 占位符**：
- `"auto_minus_60d"` → 运行时自动算为 today - 90 天（约 60 个交易日）
- `"auto_minus_120d"` → today - 180 天；也可直接填整数如 `20260101`

**cooldown_days 默认 0**：每次都是当前市场快照，不需要冷静期。
**结果键**：`selected[]`（含 rank / 各 value_column 值 / 数据日期）

---

## Step 0 — 依赖挂载（硬前置）

执行任何命令前确认 quant-buddy-skill 已安装；CLI 启动按下列顺序找它：
```
{SKILL_ROOT}/../quant-buddy-skill/
~/.claude/skills/quant-buddy-skill/
~/.agents/skills/quant-buddy-skill/
~/.openclaw/skills/quant-buddy-skill/
~/.codex/skills/quant-buddy-skill/
{cwd}/.{claude|openclaw|codex|github}/skills/quant-buddy-skill/
```

> **路径约定**：`{SKILL_ROOT}` = 当前 SKILL.md 所在目录。

---

## 决策树（LLM 用这个判断走哪条路）

```
用户一句话
   │
   ├─ 看/查询类            → cli list / show / history / diagnose
   ├─ 立即跑（不写日志）    → cli run <id> [--dry-run]
   ├─ 全量/完整跑一次      → cli trigger <id>    （写日志、last_result 含 push_result）
   ├─ 推送测试             → cli test-push <id>
   ├─ 改字段               → cli set <id> <jsonpath> <value>   （写入前自动备份）
   ├─ 加 job（有公式）     → cli add <id> --task-type <type> --from preset:<name>
   │                          或 --formulas-file <formulas.json>
   ├─ 加 job（没有公式）   → cli add <id> --task-type <type> --scaffold
   │                          → 对话里调 quant-buddy-skill 生成公式
   │                          → cli set <id> formulas '[...]'
   ├─ 删 job               → cli delete <id> --yes
   ├─ 暂停/恢复            → cli pause <id> / resume <id>
   ├─ 改公式后             → cli validate <id>
   └─ 改时间               → cli set schedule.cron/time ... → cli apply-schedule <id>
```

所有命令统一入口：
```bash
python {SKILL_ROOT}/scripts/cli.py <command> [...args]
```
**所有 stdout 都是 JSON**；人类可读消息走 stderr。

> 完整 CLI 速查表、工作流 A-G 见 [docs/cli-reference.md](docs/cli-reference.md)。
