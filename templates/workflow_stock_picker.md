# 执行流：{JOB_NAME}（stock_picker）

> 适用任务：`{JOB_ID}` | 类型：stock_picker | 无冷静期 | push_when: {PUSH_WHEN}

⚠️ **执行约束（严格遵守）**
- 禁止调用 quant-buddy-skill 或 runMultiFormulaBatch 执行公式——公式由步骤 2 的 `cli.py run-formulas` 完成
- 禁止读取 docs/agent-runtime-flow.md（那是创建态参考文档）
- 步骤 2 返回 errors 时：错误数 < 公式数的一半则继续；否则写日志后终止
- 推送失败（push.ok != true）时：终止，不执行 save-report 和 save-result

---

## 步骤 0 — 记录执行时间

```
run_start_time = 当前时间（YYYY-MM-DD HH:MM:SS）
```

---

## 步骤 1 — 跑公式 & 获取选股结果

```bash
python {SKILL_ROOT}/scripts/cli.py run-formulas {JOB_ID}
```

从返回 JSON 中读取：
- `run_start_time`：本次执行时间（覆盖步骤 0 的值）
- `today`：数据日期
- `selected[]`：入选 ticker 列表（含各 value_column 值），已按排序字段排好序
- `errors[]`：数据异常列表
- `last_column_full_keys`：调试用

**错误处理**：若 `ok=false` 则写日志后终止。若 `errors` 非空，记录但继续。

---

## 步骤 2 — 写精简推送稿

将以下内容写入 `{DATA_ROOT}/{JOB_ID}/state/_pending_push.md`：

```
**【{JOB_NAME}】{today}**
> 执行时间：{run_start_time}

📊 本轮入选 {N} 只（Top {LIMIT}）：

1. **{公司名} {ticker}**　{value_column_label_1}：{v1}　{value_column_label_2}：{v2}
2. ...（最多展示 10 行，超过时截断并写"…共 N 只，完整见报告"）

📄 完整报告已落盘
```

若 `selected` 为空且 push_when = "triggered_only"，**跳过步骤 3-6，直接执行步骤 7**。

---

## 步骤 3 — 写完整报告

将完整表格报告写入 `{DATA_ROOT}/{JOB_ID}/state/_pending_report.md`。

报告骨架参考 `templates/agent_report.md`（若 job 配置了 `report.job_template`，优先读取该模板）。

报告头必须包含：
```
> 执行时间：{run_start_time}
```

---

## 步骤 4 — 推送

```bash
python {SKILL_ROOT}/scripts/cli.py push {JOB_ID} --report-file {DATA_ROOT}/{JOB_ID}/state/_pending_push.md
```

检查返回 `push.ok` 必须为 `true`，否则终止（不执行步骤 5/6）。

---

## 步骤 5 — 落盘报告

```bash
python {SKILL_ROOT}/scripts/cli.py save-report {JOB_ID} --file {DATA_ROOT}/{JOB_ID}/state/_pending_report.md
```

---

## 步骤 6 — 保存结果摘要

将以下 JSON 写入临时文件后调用：

```json
{
  "run_time": "{run_start_time}",
  "summary": {
    "selected_count": {N},
    "error_count": {K},
    "push_ok": true
  }
}
```

```bash
python {SKILL_ROOT}/scripts/cli.py save-result {JOB_ID} --file <result_tmp.json>
```
