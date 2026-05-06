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
   ├─ 立即跑               → cli run <id> [--dry-run]
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

---

## CLI 速查

| 用户说 | 命令 |
|---|---|
| "现在有哪些任务" | `cli.py list` |
| "看下 xxx 上次结果" | `cli.py show xxx` |
| "立即跑一遍（不推送）" | `cli.py run xxx --dry-run` |
| "正式跑一次" | `cli.py run xxx` |
| "推个测试到群里" | `cli.py test-push xxx` |
| "加一个低PE高股息 Top10 任务" | `cli.py add high-yield-low-pe --task-type stock_picker --from preset:stock_picker_value` |
| "加一个抄底监控" | `cli.py add my-dip --task-type signal_monitor --from preset:dip_2sigma` |
| "先建空任务（没有公式）" | `cli.py add my-task --task-type stock_picker --scaffold` |
| "把公式写进去" | `cli.py set my-task formulas '["公式1","公式2"]'` |
| "把 result_handler 写进去" | `cli.py set my-task result_handler '{"mode":"topn_by_mask",...}'` |
| "改成早 8:30 跑" | `cli.py set xxx schedule.time 08:30` → `cli.py apply-schedule xxx` |
| "改成每 30 分钟盘中跑" | `cli.py set xxx schedule.cron "*/30 9-15 * * 1-5"` → `cli.py apply-schedule xxx` |
| "改成无论是否触发都推" | `cli.py set xxx notification.push_when always` |
| "改成增量报告" | `cli.py set xxx report.report_mode incremental` |
| "暂停定时" | `cli.py pause xxx` |
| "恢复定时" | `cli.py resume xxx` |
| "彻底删掉" | `cli.py delete xxx --yes` |
| "重置冷静期" | `cli.py reset-cooldown xxx --all` |
| "今天怎么没提醒？" | `cli.py diagnose xxx` |
| "撤销上次改动" | `cli.py history xxx` → `cli.py rollback xxx` |
| "校验一下配置" | `cli.py validate xxx` |

---

## 工作流（LLM 标准动作）

### A. 用户问"现在有哪些任务"
1. `cli.py list` → 列出 task_type / schedule / last_run / 上次结果摘要
2. 让用户选具体 job 后走 B/C/D/E

### B. 用户要"改字段"
1. 必要时先 `cli.py show <id>` 拿当前值
2. `cli.py set <id> <jsonpath> <value>`（自动备份 `.history/`，保留最近 20 份）
3. 若改的是 `formulas` / `signal.formulas` → `cli.py validate <id>`
4. 若改的是 `schedule.*` → `cli.py apply-schedule <id>`
5. 一句话回报：改了什么 / 备份在哪 / 是否 rollback

### C. 用户要"立即跑一遍"
1. `cli.py run <id> --dry-run` 拿结构化 JSON
2. **signal_monitor**：解读 `triggered[]` / `cooled_down[]` / `anomalies[]`；若 `pending_analysis=true` → WebSearch + 框架分析 → 写进报告
3. **stock_picker**：解读 `selected[]`（rank / 各字段值 / 数据日期）；dry-run 不推企微

### D. 用户要"加 stock_picker job"（已有公式）
1. `cli.py add <id> --task-type stock_picker --from preset:stock_picker_value`
2. `cli.py set <id> notification.wecom.webhook <webhook>`
3. `cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"`
4. `cli.py set <id> report.report_mode incremental`
5. `cli.py run <id> --dry-run`（验证 selected[] 符合预期）
6. `cli.py apply-schedule <id>`

### E. 用户要"加 stock_picker job"（没有公式）
1. `cli.py add <id> --task-type stock_picker --scaffold` 建空骨架
2. 在对话里调 quant-buddy-skill，走 quant-standard.md：confirmDataMulti → 组装 formulas
3. `cli.py set <id> formulas '<公式数组>'`
4. `cli.py set <id> result_handler '<handler JSON>'`
5. 走 D 步骤 2-6

### F. 用户问"今天怎么没提醒"
1. `cli.py diagnose <id>` → enabled / last_run / scheduler_status / cooldown_hits（仅 signal_monitor）/ quant_buddy_reachable
2. 翻译给用户

---

## job.json 公共字段（两种 task_type 都有）

`task_type` 缺省时默认 `signal_monitor`（向后兼容）。
`push_when`：`triggered_only`（有结果才推）| `always`（每次都推，包括 0 条）。
`report_mode`：`overwrite`（每天一份）| `incremental`（带时间戳，盘中多次推荐）。
`schedule.cron` 优先于 `schedule.time`；支持 `*/30 9-15 * * 1-5`（工作日 9-15 时每 30 分钟）。
`schedule.task_name` 留空时由 CLI 自动填为 `ScheduledTask_<id>`。

---

## 目录结构

```
{SKILL_ROOT}/
├── SKILL.md
├── scripts/
│   └── cli.py                       唯一入口（所有 stdout 都是 JSON）
├── lib/
│   ├── job_schema.py                load/save/set/backup/rollback
│   ├── scanner.py                   signal_monitor + stock_picker 执行逻辑
│   ├── asset_source.py              signal_monitor 资产池 loader
│   ├── cooldown.py                  冷静期状态读写
│   ├── wecom.py                     企微推送
│   ├── scheduler_win.py             schtasks 包装（ScheduledTask_ 前缀）
│   ├── validator.py                 schema 校验
│   ├── reporter.py                  报告渲染（按 task_type 选模板/格式）
│   └── quant_buddy.py               动态发现 quant-buddy-skill
├── jobs/
│   ├── registry.json
│   ├── _presets/
│   │   ├── dip_2sigma.json          signal_monitor: MA20-2σ 布林下轨
│   │   ├── breakout_20d.json        signal_monitor: 20日突破
│   │   ├── macd_cross.json          signal_monitor: MACD 金叉
│   │   └── stock_picker_value.json  stock_picker: PE<15+股息率>3% Top10（已验证）
│   └── <job_id>/
│       ├── job.json
│       ├── assets.xlsx              （仅 signal_monitor, type=excel 时）
│       ├── .history/
│       ├── state/
│       │   ├── triggered.json       （仅 signal_monitor 冷静期）
│       │   ├── last_result.json
│       │   └── logs/
│       └── output/reports/
└── templates/
    ├── default_report.md            signal_monitor 报告模板
    └── stock_picker_report.md       stock_picker TopN 表格模板
```

---

## signal_monitor 场景（原有功能，完全兼容）

job.json 格式、公式写法、资产池管理、冷静期、analysis_hook 与原 signal-monitor 完全兼容。
`apply-schedule` 会自动迁移旧 `SignalMonitor_<id>` → `ScheduledTask_<id>`。

signal_monitor job.json 关键字段：
- `signal.formulas`：LLM 写中文公式，第一条必须含 `{ASSETS}` 占位符
- `signal.trigger_formula`：哪条公式的布尔结果决定"触发"
- `signal.display_fields`：报告展示列
- `analysis_hook.enabled`：true 时 scanner 标 `pending_analysis=true`，LLM 外层完成 WebSearch + 分析
- `signal.category`：`direction`（buy/sell/watch）+ `label`（自由文本子类型）

| 信号名 | preset | 触发条件 |
|--------|--------|----------|
| R4A 短线抄底 | `dip_2sigma` | 收盘价 < MA20 − 2×STD20 |
| R4B 中线稳健 | `dip_2sigma_60d` | 收盘价 < MA60 − 2×STD60 |
| 20日突破 | `breakout_20d` | 收盘价 > 近20日最高 |
| MACD金叉 | `macd_cross` | DIF 上穿 DEA |

---

## stock_picker 场景（新增）

`jobs/_presets/stock_picker_value.json` 内置已验证的 PE+股息率 Top10 公式（字段名来自 quant-buddy confirmDataMulti 实测，全角括号、中文逗号等原文保留）。

**快速建任务**：
```bash
python scripts/cli.py add high-yield-low-pe --task-type stock_picker --from preset:stock_picker_value
python scripts/cli.py set high-yield-low-pe notification.wecom.webhook "https://..."
python scripts/cli.py run high-yield-low-pe --dry-run
python scripts/cli.py apply-schedule high-yield-low-pe
```

**自定义公式（入口 B）**：
```bash
python scripts/cli.py add my-picker --task-type stock_picker --scaffold
# 在对话里调 quant-buddy-skill 生成公式
python scripts/cli.py set my-picker formulas '["公式1","公式2","公式3"]'
python scripts/cli.py set my-picker result_handler \
  '{"mode":"topn_by_mask","result_formula":"Top结果","value_columns":[{"label":"字段","from":"公式名","format":"{:.2f}"}],"sort_by":"字段","sort_order":"desc","limit":10}'
python scripts/cli.py run my-picker --dry-run
python scripts/cli.py apply-schedule my-picker
```

---

## 资产参考库（仅 signal_monitor 需要）

资产信息来自 quant-buddy-skill 内置库：
```
quant-buddy-skill/presets/assets_db/
├── stock_a.yaml   # A 股（沪深京）
├── stock_hk.yaml  # 港股
└── stock_us.yaml  # 美股（.O=NASDAQ / .N=NYSE / .A=AMEX）
```

生成 `assets.xlsx`（两列无表头）：
```python
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
for company, ticker in selected_assets:
    ws.append([company, ticker])
wb.save("jobs/<id>/assets.xlsx")
```

---

## 关键约束 & 心智模型

- **stdout 永远是 JSON** —— LLM 直接 `json.loads`，stderr 给人看
- **set 永远先备份** —— 自动 snapshot 到 `.history/`，可 `rollback`
- **dry-run 是安全档** —— 用户说"试一下/看看"统统加 `--dry-run`
- **stock_picker 运行时无 LLM** —— 公式已物化存在 job.json，schtasks 触发时纯执行
- **公式语义归 quant-buddy** —— scheduled-task 不知道"全A股""PE""股息率"是什么，只透传字符串
- **任务名前缀** —— `ScheduledTask_<id>`（apply-schedule 自动迁移旧 `SignalMonitor_<id>`）
