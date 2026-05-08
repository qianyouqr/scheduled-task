# workflow-generation — Agent 生成 workflow.md 的裁剪规则

**触发时机**：`cli.py add <id> ...` 完成后、`cli.py apply-schedule <id>` 之前，Agent 必须生成 `jobs/<id>/workflow.md`。

**补生成已有 job**：
```bash
python scripts/cli.py generate-workflow <id>
```
命令输出 job 配置摘要 + 指引，Agent 据此生成 workflow.md。

---

## 为什么需要 workflow.md

定时任务触发时（`_run.bat` → `claude -p`），Claude 只读 `workflow.md`，**不读 `docs/agent-runtime-flow.md`**。  
`agent-runtime-flow.md` 是完整的 13 步通用流，适合创建态参考，但对执行态来说有三个问题：

1. 大量无用步骤（如无归因的 stock_picker 无需步骤 8）导致 Claude 反复确认
2. 公式执行仍引导调 MCP 工具 `runMultiFormulaBatch`，带来大量文档加载延迟
3. 没有具体的命令路径和参数值，Claude 需要额外 grep/read 补全

`workflow.md` 是**为这个 job 量身裁剪的执行 SOP**，步骤数少、命令完整、路径具体。

---

## 第一步：确定模板骨架

| task_type | analysis_hook.enabled | cooldown_days | 模板 | 步骤数 |
|---|---|---|---|---|
| `stock_picker` | — | 0（无冷静期） | `templates/workflow_stock_picker.md` | 6 步 |
| `signal_monitor` | false | 0 | `templates/workflow_signal_monitor.md` | 7 步（删步骤 2、6） |
| `signal_monitor` | false | > 0 | `templates/workflow_signal_monitor.md` | 8 步（删步骤 2） |
| `signal_monitor` | true | > 0 | `templates/workflow_signal_monitor.md` | 全 8 步 |

---

## 第二步：裁剪步骤

**stock_picker** 必须删除的注释块和 mark-triggered 步骤（模板中没有，无需处理）。

**signal_monitor** 根据配置删除以下步骤：

- `analysis_hook.enabled = false`：**删除步骤 2（归因）**，步骤编号顺移
- `cooldown_days = 0`：**删除步骤 6（mark-triggered）**，同时在步骤 3 精简推送稿中删除冷静期相关行

---

## 第三步：填写具体值

将模板中的 `{占位符}` 替换为 job 配置的实际值：

| 占位符 | 来源 |
|---|---|
| `{JOB_ID}` | `job.id` |
| `{JOB_NAME}` | `job.name` |
| `{COOLDOWN_DAYS}` | `job.cooldown_days` |
| `{PUSH_WHEN}` | `job.notification.push_when` |
| `{ANALYSIS_HOOK_ENABLED}` | `job.analysis_hook.enabled` |
| `{FRAMEWORK_DOC}` | `job.analysis_hook.framework_doc`（默认 `references/framework.md`） |
| `{SIGNAL_DESCRIPTION}` | `job.signal.category.description` |
| `{LIMIT}` | `job.result_handler.limit` |
| `{SKILL_ROOT}` | 当前 SKILL.md 所在绝对路径（运行时 claude 的 cwd） |
| `{DATA_ROOT}` | 若 SKILL_ROOT 在 `~/.claude` 内，填 `%LOCALAPPDATA%/scheduled-task-data`；否则填 `{SKILL_ROOT}/jobs` |

**`{DATA_ROOT}` 的判定规则**：  
运行时如果 `SCHEDULED_TASK_DATA_ROOT` 环境变量被 `_run.bat` 设置，所有 state/ 和 output/ 路径都在该变量指定目录下。在生成 workflow.md 时，用 `jobs/<id>/state/` 作为相对路径占位，或写入实际的绝对路径（从 `cli.py show <id>` 返回的 `scheduler.runner` 字段的 bat 内容推断）。

---

## 第四步：添加 value_column 信息

**stock_picker**：在步骤 1（run-formulas 结果读取）的说明里，列出 `result_handler.value_columns` 中每列的 `label`，以便 Claude 知道 `selected[]` 里有哪些字段。

**signal_monitor**：在步骤 3（精简推送稿）的 display_fields 部分，列出 `signal.display_fields` 中的字段名。

---

## 第五步：写入文件

将生成的内容写入 `jobs/<id>/workflow.md`（`generate-workflow` 输出的 `workflow_target_path`）。

---

## 关键约束（必须体现在 workflow.md 里）

1. **禁止 quant-buddy-skill**：workflow.md 顶部的 ⚠️ 约束块必须包含"禁止调用 quant-buddy-skill 或 runMultiFormulaBatch"这句话
2. **步骤 1 用 `cli.py run-formulas`**：这是公式计算的唯一入口，workflow.md 里的步骤 1 必须写完整的 `python {SKILL_ROOT}/scripts/cli.py run-formulas {JOB_ID}` 命令
3. **推送失败不写冷静期**：signal_monitor 的步骤 6（mark-triggered）必须在步骤 5（push）成功后才执行
4. **报告头含执行时间**：无论什么模板，_pending_report.md 的头部必须包含 `> 执行时间：{run_start_time}`

---

## 示例：hs300_dip_5pct（stock_picker，无归因，无冷静期）

此 job 的 workflow.md 只需 6 步：
- 步骤 0：记录时间
- 步骤 1：`cli.py run-formulas hs300_dip_5pct` → 读 `selected[]`、`errors[]`
- 步骤 2：写 `_pending_push.md`（入选 N 只，按日内跌幅排序）
- 步骤 3：写 `_pending_report.md`
- 步骤 4：`cli.py push hs300_dip_5pct --report-file ...`
- 步骤 5：`cli.py save-report hs300_dip_5pct --file ...`
- 步骤 6：`cli.py save-result hs300_dip_5pct --file ...`

无需步骤：加载资产（`run-formulas` 内部已处理）、加载冷静期、mark-triggered（cooldown=0）、归因（stock_picker 无 analysis_hook）。
