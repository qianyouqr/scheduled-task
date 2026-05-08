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

## 状态模型

Skill 按**使用场景**分三态，不同态有不同 SOP 和工具约束：

| 状态 | 触发方式 | quant-buddy-skill | 公式执行方式 | 核心文档 |
|------|----------|:-----------------:|---|---|
| **创建态** | 对话（用户请求） | ✅ 可用 | MCP `runMultiFormulaBatch`（公式设计/验证） | `SKILL.md` + task SOP |
| **执行态** | schtasks → `_run.bat` → `claude -p` | ❌ 禁止 | `cli.py run-formulas`（直连 HTTP） | `jobs/<id>/workflow.md` |
| **管理态** | 对话（用户请求） | ❌ 不需要 | 无公式执行 | `SKILL.md` + CLI |

### 创建态
对话场景下的任意操作：创建新 job、修改公式、配置调度、生成/更新 workflow.md。
**quant-buddy-skill 可用**，负责生成、验证公式。  
创建流程末尾（`apply-schedule` 之前）必须调 `cli.py generate-workflow <id>` 并生成 `jobs/<id>/workflow.md`。

### 执行态
`_run.bat` 由 schtasks 触发后启动的 `claude -p` 单次调用。  
**只读 `jobs/<id>/workflow.md`**，workflow.md 是此任务的唯一 SOP。  
**严禁调用 quant-buddy-skill 或 runMultiFormulaBatch 执行公式**——公式通过 `cli.py run-formulas` 直连 HTTP 完成，Claude 全程在场、读取结果 JSON、掌控错误处理。

### 管理态
对话中用 CLI 管理 job 生命周期：list / show / pause / resume / diagnose / history / rollback 等。  
无公式执行，无需 quant-buddy-skill。

---

## 详细文档（docs/）

| 文档 | 内容 |
|------|------|
| [docs/workflow-generation.md](docs/workflow-generation.md) | **workflow.md 裁剪规则**（创建态必读）：如何按 job 配置生成执行 SOP |
| [docs/agent-runtime-flow.md](docs/agent-runtime-flow.md) | 通用 13 步流（创建态参考 / 立即试跑用）；执行态实际按 `jobs/<id>/workflow.md` |
| [docs/signal-monitor-sop.md](docs/signal-monitor-sop.md) | signal_monitor SOP：触发判定、冷静期、**资产池文件管理规则**、信号 presets、完整工作流、analysis_hook |
| [docs/stock-picker-sop.md](docs/stock-picker-sop.md) | stock_picker SOP：result_handler 字段、begin_date 占位符、工作流 D/E |
| [docs/cli-reference.md](docs/cli-reference.md) | CLI 速查表（含 6 个 agent-path 原语）、工作流 A-G、常见问题排查 |
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

### 创建阶段跨 skill 编排硬规则

当用户在同一句话里同时提出「量化信号/筛选条件」和「创建定时任务/监控/推送」时，必须按下面顺序执行：

1. **先用 quant-buddy-skill 生成并验证公式**：把用户的资产池、字段口径、阈值、窗口参数原样交给 quant-buddy-skill；拿到可运行的公式链后，再进入 scheduled-task 创建流程。
2. **再用 scheduled-task 落 job**：把上一步公式链写入 `job.json`，scheduled-task 只负责调度、推送、报告和运行时透传。
3. **不得用 bundle/preset 公式替代用户条件**：bundle/preset 只能作为骨架。只要用户条件与模板内置条件不同，必须覆盖 `description`、`signal.category.description`、`signal.formulas`、`signal.trigger_formula`、`signal.display_fields`、`signal.lookback_days` 等字段。
4. **用户给了资产池文件时，以用户文件为准**：若使用 bundle 创建 job，bundle 自带 `assets.xlsx` 只能作为缺省资产池；用户显式提供 `assets.xlsx` / `csv` 路径时，必须复制该文件到 `jobs/<id>/assets.xlsx`（或同名文件）并配置 `asset_source.type/path` 指向复制后的文件。
5. **上文复用必须有显式公式证据**：用户说「把上面的工作创建成定时任务」时，先从本对话可见上下文提取上一轮的公式链；若上一轮只有结果名单、没有显式公式链，必须重新调用 quant-buddy-skill 生成公式，禁止凭记忆、自然语言条件或 bundle 默认公式猜写。

推荐的公式交接格式（对话层产物）：

```json
{
  "task_type": "signal_monitor",
  "condition_text": "C < MA20 - 1.5*ATR20",
  "signal": {
    "formulas": [
      {"name": "资产池收盘价", "expression": "..."},
      {"name": "信号", "expression": "..."}
    ],
    "trigger_formula": "信号",
    "display_fields": ["资产池收盘价", "MA20", "ATR20", "下轨"],
    "lookback_days": 60
  }
}
```

**运行时架构（v3 — workflow.md 执行态）**：schtasks 触发 `_run.bat` → 启动 `claude -p` → agent 只读 `jobs/<id>/workflow.md`（按 job 类型裁剪的精简 SOP）→ 调 `cli.py run-formulas` 直连 HTTP 跑公式（Claude 全程在场、读取 JSON 结果、掌控错误处理）→ 末日值/TopN → 触发判定 →（有触发时 WebSearch 归因）→ 写 markdown 报告 → push 到企微 → push 成功后才 mark-triggered。  
**⚠️ 执行态严禁使用 quant-buddy-skill**；创建态可用 quant-buddy-skill 生成和验证公式。

---

## 可用 Bundle（打包任务模板）

Bundle = 一整套打法的打包，含 job 骨架（公式已写好）+ 资产池文件 + 参考资料 + 报告模板 + 执行 SOP。  
用 `--from bundle:<name>` 创建时，所有文件自动复制到 `jobs/<id>/`，无需手动传路径。

| bundle 名 | task_type | 适用场景 | 典型触发语 |
|---|---|---|---|
| `dip_r4a_signal_monitor` | signal_monitor | A 股固定资产池跌破 MA20−2σ 抄底信号监控 + WebSearch 归因 + 价格/价值框架判断 | 跌破下轨、抄底信号、R4A、2σ 抄底、核心资产跌破 |

**一句话创建**（以 `dip_r4a_signal_monitor` 为例）：

```bash
python scripts/cli.py add <job_id> --from bundle:dip_r4a_signal_monitor
python scripts/cli.py set <job_id> notification.wecom.webhook "https://..."
python scripts/cli.py set <job_id> schedule.cron "*/20 * * * *"
python scripts/cli.py apply-schedule <job_id>
# 立即跑一次：在对话里让 agent 按 docs/agent-runtime-flow.md 的 13 步流执行
```

> Bundle 与 preset 的区别：preset 只有 job.json 骨架（公式），bundle 额外携带资产池文件、参考资料、报告模板、执行 SOP。

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

> ⚠️ **公式分批约束（agent 必须遵守）**：quant-buddy 服务端单次公式数硬上限为 **10 条**。`formulas` 数组可以完整写入 job.json —— 但运行时 agent 必须按 quant-buddy-skill 的 `tools/run_multi_formula.md` 切批：每批 ≤10 条、共用同一 `task_id`、`force_reusable_array=true`，使后批可以引用前批生成的中间变量。

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
   ├─ 立即跑一次（对话里） → 直接按 docs/agent-runtime-flow.md 的 13 步流走
   │                          （不要再找 cli run / trigger，它们已被删除）
   ├─ 推送测试             → cli test-push <id>
   ├─ 改字段               → cli set <id> <jsonpath> <value>   （写入前自动备份）
   ├─ 加 job（有公式，有打包打法）→ cli add <id> --from bundle:<name>
   │                          自动复制资产池/模板/参考资料/SOP，只需再 set webhook + cron
   │                          → 创建完成后调 cli generate-workflow <id>，生成 workflow.md
   │                          → 最后 cli apply-schedule <id>
   ├─ 加 job（有公式）     → cli add <id> --task-type <type> --from preset:<name>
   │                          或 --formulas-file <formulas.json>
   │                          → 创建完成后调 cli generate-workflow <id>，生成 workflow.md
   │                          → 最后 cli apply-schedule <id>
   ├─ 加 job（没有公式）   → cli add <id> --task-type <type> --scaffold
   │                          → 对话里调 quant-buddy-skill 生成公式
   │                          → cli set <id> formulas '[...]'
   │                          → cli generate-workflow <id>，生成 workflow.md
   │                          → cli apply-schedule <id>
   ├─ 更新 workflow.md     → cli generate-workflow <id> → 重新生成 jobs/<id>/workflow.md
   ├─ 删 job               → cli delete <id> --yes
   ├─ 暂停/恢复            → cli pause <id> / resume <id>
   ├─ 改公式后             → cli validate <id> → 重新生成 workflow.md → cli apply-schedule <id>
   └─ 改时间               → cli set schedule.cron/time ... → cli apply-schedule <id>

定时触发时（_run.bat → claude -p）：
   只读 jobs/<id>/workflow.md，严格按其步骤执行。
   ⚠️ 禁止调用 quant-buddy-skill 或 runMultiFormulaBatch——公式由 cli run-formulas 完成。
```

所有命令统一入口：
```bash
python {SKILL_ROOT}/scripts/cli.py <command> [...args]
```
**所有 stdout 都是 JSON**；人类可读消息走 stderr。

> 完整 CLI 速查表、工作流 A-G 见 [docs/cli-reference.md](docs/cli-reference.md)。
