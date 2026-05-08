# 执行流：{JOB_NAME}（signal_monitor）

> 适用任务：`{JOB_ID}` | 类型：signal_monitor | 冷静期：{COOLDOWN_DAYS} 天 | push_when: {PUSH_WHEN} | 归因：{ANALYSIS_HOOK_ENABLED}

⚠️ **执行约束（严格遵守）**
- 禁止调用 quant-buddy-skill 或 runMultiFormulaBatch 执行公式——公式由步骤 1 的 `cli.py run-formulas` 完成
- 禁止读取 docs/agent-runtime-flow.md（那是创建态参考文档）
- 步骤 1 返回 errors 时：错误数 < 公式数的一半则继续；否则写日志后终止
- 推送失败（push.ok != true）时：禁止调 mark-triggered，终止后续步骤

---

## 步骤 0 — 记录执行时间

```
run_start_time = 当前时间（YYYY-MM-DD HH:MM:SS）
```

---

## 步骤 1 — 跑公式 & 获取触发结果

```bash
python {SKILL_ROOT}/scripts/cli.py run-formulas {JOB_ID}
```

从返回 JSON 中读取：
- `run_start_time`：本次执行时间（覆盖步骤 0 的值）
- `today`：数据日期
- `triggered[]`：经冷静期过滤后的新触发 ticker 列表
- `cooled_down[]`：被冷静期跳过的 ticker 列表
- `errors[]`：数据异常列表
- `last_column_full_keys`：调试用

**错误处理**：若 `ok=false` 则写日志后终止。若 `errors` 非空，记录但继续。

---

<!-- === 步骤 2（仅 analysis_hook.enabled=true 时保留） === -->
## 步骤 2 — 归因（仅 triggered 非空 且 analysis_hook.enabled=true）

> 若 triggered 为空 或 analysis_hook.enabled=false，**跳过本步骤**。

归因框架文档：`{FRAMEWORK_DOC}`（相对 jobs/{JOB_ID}/）

对每个触发 ticker，使用 WebSearch 收集近 3 个交易日的：
- 行业/公司新闻、政策面
- 资金面（北上、龙虎榜）
- 个股近期异动

按框架小节组织结论（可抄底/不能抄/需观察 + 仓位建议）。

---

## 步骤 3 — 写精简推送稿

将以下内容写入 `{DATA_ROOT}/{JOB_ID}/state/_pending_push.md`：

**有触发（triggered 非空）**：
```
**【{JOB_NAME}】{today}**
> 执行时间：{run_start_time} | 策略：{SIGNAL_DESCRIPTION} | 冷静期：{COOLDOWN_DAYS} 天

🔔 {触发标签} {N} 只：
（cooldown_days>0 写"新增触发"；cooldown_days=0 写"本轮触发"）

**{公司名} {ticker}** 收盘 {close}（{today}）
· {display_field_1}：{v1} | {display_field_2}：{v2}
· 归因：{一句话归因}（若有 analysis_hook）
· 结论：{可抄底/不能抄/需观察}（仓位建议）

（超过 3 只时后续只列名称+结论）

📊 冷静期跳过 {M} 只：{ticker1名...}（cooldown_days=0 时省略此行）
⚠️ 数据异常 {K} 只（如有）
📄 完整报告已落盘
```

**无触发（triggered 为空）且 push_when = "always"**：
```
**【{JOB_NAME}】{today}**
> 执行时间：{run_start_time} | 冷静期：{COOLDOWN_DAYS} 天

本轮无新增触发。
冷静期内 {M} 只：{ticker1名...}（最多 5 只，超过写"等 M 只"）
```

**无触发 且 push_when = "triggered_only"**：**跳过步骤 4（不推送）**，直接执行步骤 5。

---

## 步骤 4 — 写完整报告

将完整报告写入 `{DATA_ROOT}/{JOB_ID}/state/_pending_report.md`。

报告骨架参考 `templates/agent_report.md`（若 job 配置了 `report.job_template`，优先读取该模板）。

报告头必须包含：
```
> 执行时间：{run_start_time}
```

---

## 步骤 5 — 推送

```bash
python {SKILL_ROOT}/scripts/cli.py push {JOB_ID} --report-file {DATA_ROOT}/{JOB_ID}/state/_pending_push.md
```

检查返回 `push.ok` 必须为 `true`，否则终止（禁止调 mark-triggered）。

push_when = "triggered_only" 且 triggered 为空时，此步骤已在步骤 3 被跳过，视为正常流程继续。

---

## 步骤 6 — mark-triggered（仅 push 成功后）

> 仅当 triggered 非空 且 push.ok = true 时执行。冷静期为 0 时也可跳过。

```bash
python {SKILL_ROOT}/scripts/cli.py mark-triggered {JOB_ID} --tickers {T1},{T2},...
```

---

## 步骤 7 — 落盘报告

```bash
python {SKILL_ROOT}/scripts/cli.py save-report {JOB_ID} --file {DATA_ROOT}/{JOB_ID}/state/_pending_report.md
```

---

## 步骤 8 — 保存结果摘要

将以下 JSON 写入临时文件后调用：

```json
{
  "run_time": "{run_start_time}",
  "summary": {
    "triggered_count": {N},
    "cooled_count": {M},
    "error_count": {K},
    "push_ok": true
  }
}
```

```bash
python {SKILL_ROOT}/scripts/cli.py save-result {JOB_ID} --file <result_tmp.json>
```
