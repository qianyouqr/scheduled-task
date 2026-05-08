# agent-runtime-flow — schtasks 触发后 Agent 的标准运行流

> ⚠️ **创建态参考文档** — 此文档描述通用 13 步流，适用于：
> 1. **对话里"立即跑一次"**（非 schtasks 触发）
> 2. **创建态**理解完整流程
>
> **schtasks 定时触发的执行态**不读本文档——执行态 claude 只读 `jobs/<id>/workflow.md`。  
> 若还没有 `workflow.md`，先在对话里执行 `cli.py generate-workflow <id>` 生成它。

`_run.bat` 由 schtasks 启动后会调起 `claude -p "运行 scheduled-task 任务 <id> ..."`。
Agent 在**单次**调用内必须按下面 13 步顺序跑完整个 job，不允许跳步、不允许把 5–9 并行化。

> 本文档给 agent 看；用户/对话场景请看 [cli-reference.md](cli-reference.md)。

---

## 13 步运行流

### 步骤 0 — 记录本次执行时间
在调用任何 CLI 命令之前，用 `datetime.now()` 记录本次执行的开始时间：
```
run_start_time = datetime.now()  # 格式：YYYY-MM-DD HH:MM:SS
```
此值将在步骤 9 写入报告头部、在步骤 13 写入结果摘要。

### 步骤 1 — 读 job 配置
```bash
python {SKILL_ROOT}/scripts/cli.py show <id>
```
拿到 `task_type` / `formulas` / `signal` / `cooldown_days` / `notification` / `analysis_hook` / `report` / `context` 等字段。

重点关注：
- `context.run_sop`：创建 job 时用户写入的额外执行指导，**若非空，整个运行流必须遵守其约束**。
- `report.job_template`：job 级别报告模板路径（相对 `jobs/<id>/`），非空时步骤 9 优先用它。
- `context.references[]`：参考资料文件列表，步骤 1b 会逐一读取。

### 步骤 1b — 加载参考资料（若 context.references 非空）

读取 `context.references[]` 里列出的每个文件（路径相对 `jobs/<id>/`），把内容作为**背景知识**放入 agent 上下文，供步骤 8（归因）和步骤 9（写报告）使用。

常见内容：行业/公司背景、归因框架说明文档、历史触发记录摘要、用户对该资产池的特殊投资逻辑。

> 文件不存在时跳过，不报错；记录到步骤 13 的结果 `context.missing_references[]`。

### 步骤 2 — 加载资产池
```bash
python {SKILL_ROOT}/scripts/cli.py load-assets <id>
```
返回 `assets[]` + `disabled_tickers[]`。`stock_picker` 模式 `assets` 通常为空（全市场扫）。

### 步骤 3 — 加载冷静期
```bash
python {SKILL_ROOT}/scripts/cli.py load-cooldown <id>
```
返回 `cooled_tickers[]`。**signal_monitor 模式必须在第 7 步之后用它过滤触发列表；stock_picker 通常 `cooldown_days=0`，可跳过。**

### 步骤 4 — 实例化公式（仅 signal_monitor）
对 `signal.formulas[]` 把 `{ASSETS}` 占位符替换为 `assets` 中所有 `company` 的逗号串：
```
"资产池构造({ASSETS})"  →  "资产池构造(贵州茅台, 五粮液, ...)"
```
然后拼成 `name = expression` 形式的字符串列表。`stock_picker` 的 `formulas` 已是字符串列表，直接透传。

### 步骤 5 — 调 quant-buddy-skill 跑公式
**强制使用** quant-buddy-skill 的 `runMultiFormulaBatch` 原生工具（**不要**自己 import quant_api，也**不要**调子进程）。

- **切批**：单批 ≤10 条公式（quant-buddy 服务端硬上限）。多于 10 条时分批，且**所有批次必须共用同一 task_id**，并设置 `force_reusable_array=true`，使后批可以引用前批生成的中间变量。
- **begin_date**：按 quant-buddy 的 `tools/run_multi_formula.md` 分层规则选；`signal_monitor` 取 `today - lookback_days * 1.4`，`stock_picker` 看 `runMultiFormula_args.begin_date`（如 `auto_minus_60d` ⇒ `today - 90`）。
- **use_minute_data**：默认 `true`。

收集所有批次的 `errors[]`、`results[]`、`last_column_full`。

### 步骤 6 — 提取末日值（signal_monitor）/ TopN（stock_picker）

**signal_monitor**：
- 找出 `signal.trigger_formula` 对应的结果块；`last_column_full.values[]` 是每个 ticker 的末日布尔/数值。
- ≥ 0.5 视为触发；同时收集 `signal.display_fields` 中每个字段在末日的值。

**stock_picker**：
- 按 `result_handler.result_formula` 取末日列非零 ticker。
- 按 `result_handler.value_columns[]` 各自从对应 result 块取末日值。
- 按 `sort_by` + `sort_order` 排序，截 `limit` 行。

### 步骤 7 — 触发判定 & 冷静期过滤

**signal_monitor**：
- `triggered = [t for t in 末日触发 if t not in cooled_tickers]`
- `cooled_down = [t for t in 末日触发 if t in cooled_tickers]`

**stock_picker**：直接用 `selected = TopN`（无冷静期）。

### 步骤 8 — 归因（仅 signal_monitor 且有触发 且 analysis_hook.enabled = true）

按 `analysis_hook.framework_doc`（默认 `framework.md`，相对 job 目录）读取归因框架，对每个触发 ticker 用 **WebSearch** 收集近 3 个交易日的：
- 行业/公司新闻
- 政策面
- 资金面（北上、龙虎榜）
- 个股近期异动

把结论按框架小节组织。无 hook / 无触发时跳过本步。

### 步骤 9 — 写 markdown 报告

**模板优先级**（由高到低）：
1. `job.report.job_template` 非空 → 读取 `jobs/<id>/<job_template>` 作为骨架
2. 全局默认 → `{SKILL_ROOT}/templates/agent_report.md`

按模板骨架填入：
- 触发列表（步骤 7 的结果）
- 归因（步骤 8 的结果；若有参考资料背景，写报告时直接引用其中的框架/结论）
- 冷静期跳过列表
- 数据异常（步骤 5 的 errors）

**⚠️ 执行时间必须写入报告**：无论是否有触发，报告头部的元信息行必须包含步骤 0 记录的 `run_start_time`，格式如下（紧跟在第一个 `>` 引用块内）：
```
> 执行时间：YYYY-MM-DD HH:MM:SS
```
该行会随报告一起被步骤 10 推送到企微，也会随步骤 12 落盘到本地。

写到临时文件 `{SKILL_ROOT}/jobs/<id>/state/_pending_report.md`。

### 步骤 9.5 — 生成企微精简推送稿

> **目的**：企微 Markdown 消息有长度上限，且用户只需快速浏览关键结论。全量报告（表格、归因详文）落到本地文件；企微只推精简摘要。

根据 `task_type` 分别组装精简稿，写到 `{SKILL_ROOT}/jobs/<id>/state/_pending_push.md`：

---

#### signal_monitor（有触发，经冷静期过滤后 triggered 非空）

```
**【{job.name}】{date}**
> 执行时间：{run_start_time} | 策略：{signal.category.description} | 冷静期：{cooldown_days} 天

🔔 {触发标签} {N} 只：
```
> **触发标签规则**：`cooldown_days > 0` 时写 `新增触发`（已排除冷静期内重复触发）；`cooldown_days = 0` 时写 `本轮触发`（无冷静期，每次执行均全量推送）。

```
**{公司名} {ticker}** 收盘 {close}（{date}）
· {展示字段1}：{value1} | {展示字段2}：{value2}（跌破幅度 X.X%）
· 归因：{下跌归因 1 句话}
· 结论：{可抄底/不能抄/需观察}（{首笔仓位建议，如"首笔 1/3 位"}）

（如触发超过 3 只，后续只列名称+结论，省略字段明细）

📊 冷静期跳过 {M} 只：{ticker1名、ticker2名...}（如有；cooldown_days=0 时此行省略）
⚠️ 数据异常 {K} 只（如有，仅列公司名）
📄 完整报告已落盘
```

#### signal_monitor（无新增触发，且 push_when = "always"）

```
**【{job.name}】{date}**
> 执行时间：{run_start_time} | 冷静期：{cooldown_days} 天

本轮无新增触发。
冷静期内 {M} 只：{ticker1名、ticker2名...}（最多列 5 只，超过则写"等 M 只"）
```

> `push_when = "triggered_only"` 时，无新增触发则**跳过步骤 10**（不推送），直接进入步骤 11。

#### stock_picker

```
**【{job.name}】{date}**
> 执行时间：{run_start_time}

📊 本轮入选 {N} 只（Top {limit}）：

1. **{公司名} {ticker}**　{value_column1}：{v1}　{value_column2}：{v2}
2. ...（最多展示 10 行，超过时截断并写"…共 N 只，完整见报告"）

📄 完整报告已落盘
```

---

**写入**：将上述精简稿写到 `{SKILL_ROOT}/jobs/<id>/state/_pending_push.md`。

### 步骤 10 — 推送精简稿
```bash
python {SKILL_ROOT}/scripts/cli.py push <id> --report-file {SKILL_ROOT}/jobs/<id>/state/_pending_push.md
```
检查返回的 `push.ok` 必须为 `true`，否则视为推送失败 — **不要** 进入步骤 11/12。

### 步骤 11 — mark-triggered（推送成功后才执行）
**仅 signal_monitor 模式**且 `triggered` 非空时：
```bash
python {SKILL_ROOT}/scripts/cli.py mark-triggered <id> --tickers T1,T2,T3
```
启动冷静期。

### 步骤 12 — 落盘报告
```bash
python {SKILL_ROOT}/scripts/cli.py save-report <id> --file {SKILL_ROOT}/jobs/<id>/state/_pending_report.md
```
按 `job.report.report_mode`（`overwrite` / `incremental`）写到 `jobs/<id>/<output_dir>/`。  
`output_dir` 来自 `job.report.output_dir`（默认 `output/reports`），路径相对于 `jobs/<id>/`，即报告实际落在 `{SKILL_ROOT}/jobs/<id>/output/reports/`。

### 步骤 13 — 保存结果摘要
把本轮跑的 JSON 结果（含 triggered/cooled/anomalies/run_time/push.ok 等）写到临时文件，然后：
```bash
python {SKILL_ROOT}/scripts/cli.py save-result <id> --file <result.json>
```
供 `cli show` / `cli diagnose` 查询。

---

## Agent 严格约束

1. **顺序锁定**：步骤 5–9 之间存在数据依赖，**不可并行**。步骤 10 必须在步骤 8/9 之后，步骤 11 必须在步骤 10 成功之后。
2. **不要绕过 quant-buddy-skill**：跑公式必须走 `runMultiFormulaBatch` 原生工具；不要 import `quant_api`，不要拷贝公式去别处算。
3. **不要猜公式**：`job.signal.formulas` / `job.formulas` 是声明式真相源，原样使用，不改写、不优化。
4. **推送失败不写冷静期**：`push.ok != true` 时禁止调 `mark-triggered`，下次定时仍能重推。`push_when = "triggered_only"` 且无新增触发时，跳过步骤 10（不推送）也视为正常流程，可继续执行步骤 11。
5. **单次调用完成**：必须在本次 `claude -p` 内跑完 13 步。失败时把异常写到 `state/logs/YYYYMMDD.log` 后 exit。
6. **不要触发 schtasks**：本次执行不要调 `cli.py apply-schedule` / `pause` / `resume` / `delete`。
7. **不要重写 job.json**：所有运行时副作用只写 `jobs/<id>/state/` 和 `jobs/<id>/output/`（即 job 目录内部），不要写 SKILL_ROOT 级别的共享目录。
8. **run_sop 优先于一切默认行为**：若 `context.run_sop` 非空，其内容视为用户对本 job 的最高优先级指令，在不违反安全约束（不改 job.json、不触发 schtasks）的前提下必须遵守。
