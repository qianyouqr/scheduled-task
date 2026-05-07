# CLI Reference & 工作流

所有命令从 `skill_dir/scheduled-task/` 目录运行。

> **架构提醒（v2）**：CLI 不再包含 `run` / `trigger` 这种"一键全跑"命令。
> 运行时由 schtasks 启动 `_run.bat` → `claude -p` → agent 按 [agent-runtime-flow.md](agent-runtime-flow.md) 的 13 步流执行。
> CLI 只暴露**管理命令** + **运行时原语**。立即跑一次请在对话里让 agent 直接走 13 步流。

---

## Step 0 — 依赖路径挂载检查

`cli.py` 启动时按下列优先级搜索 quant-buddy-skill：

```
1. {SKILL_ROOT}/../quant-buddy-skill/
2. ~/.claude/skills/quant-buddy-skill/
3. ~/.agents/skills/quant-buddy-skill/
4. ~/.openclaw/skills/quant-buddy-skill/
5. ~/.codex/skills/quant-buddy-skill/
6. {cwd}/.{claude|openclaw|codex|github}/skills/quant-buddy-skill/
```

如果报 `quant-buddy-skill not found` 且需手动指定，可在 `lib/quant_buddy.py` 的搜索列表里加路径。

---

## CLI 速查表

### 管理命令（人工/对话层用）

| 命令 | 说明 |
|------|------|
| `python scripts/cli.py list` | 列出所有 job |
| `python scripts/cli.py show <id>` | 查看 job 完整配置 + 上次运行结果 |
| `python scripts/cli.py add <id> --task-type <type> [--from preset:<name>] [--scaffold] [--formulas-file <f>]` | 新建 job |
| `python scripts/cli.py delete <id> --yes` | 删除 job 目录 + 定时任务 |
| `python scripts/cli.py set <id> <jsonpath> <value>` | 修改字段（如 `signal.trigger_formula`、`schedule.cron`） |
| `python scripts/cli.py disable-asset <id> <ticker>` | 把 ticker 加入黑名单 |
| `python scripts/cli.py enable-asset <id> <ticker>` | 从黑名单移除 |
| `python scripts/cli.py validate <id>` | 跑一次 runMultiFormula 校验公式语法（仅 signal_monitor）；stock_picker 只查 schema |
| `python scripts/cli.py test-push <id>` | 推一条静态测试消息到企微 |
| `python scripts/cli.py reset-cooldown <id> [--ticker T \| --all]` | 清冷静期 |
| `python scripts/cli.py pause <id>` / `resume <id>` | 暂停 / 恢复定时任务 |
| `python scripts/cli.py apply-schedule <id>` | 把 `job.schedule` 同步到 schtasks |
| `python scripts/cli.py diagnose <id>` | 解释"今天为什么没提醒" |
| `python scripts/cli.py history <id>` / `rollback <id> [--to <ts>]` | 查看/回滚 job.json 备份 |

### 运行时原语（schtasks 触发的 agent 用）

| 命令 | 说明 | 在 13 步流中的位置 |
|------|------|-------------|
| `python scripts/cli.py load-assets <id>` | 解析 `asset_source`，输出 `assets[]` + `disabled_tickers[]` | 步骤 2 |
| `python scripts/cli.py load-cooldown <id>` | 输出 `cooled_tickers[]` 供过滤 | 步骤 3 |
| `python scripts/cli.py mark-triggered <id> --tickers T1,T2` | 推送成功后写冷静期 | 步骤 11 |
| `python scripts/cli.py push <id> --report-file <md>` | 读 markdown 推到企微（也支持 `--content "<text>"`） | 步骤 10 |
| `python scripts/cli.py save-report <id> --file <md>` | 按 `report.report_mode` 落到 `output/reports/<id>/` | 步骤 12 |
| `python scripts/cli.py save-result <id> --file <json>` | 把本轮结果写到 `state/last_result.json` | 步骤 13 |

> **所有 stdout 都是 JSON**；人类可读消息走 stderr。

---

## 工作流

### 工作流 A — 快速建 signal_monitor（preset）

```bash
# 若有 job 级别报告模板和参考资料，在 add 时一并传入，cli 会复制到 templates/ 和 references/ 子目录
python scripts/cli.py add <id> --task-type signal_monitor --from preset:dip_2sigma \
  [--template-file path/to/报告模板.md] \
  [--reference-files path/to/框架.md,path/to/背景.md] \
  [--run-sop "每次触发后必须先查龙虎榜"]
# 准备 assets.xlsx（见 signal-monitor-sop.md）
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/15 9-15 * * 1-5"
python scripts/cli.py validate <id>
# 立即试跑：在对话里让 agent 按 docs/agent-runtime-flow.md 走 13 步
python scripts/cli.py apply-schedule <id>
```

### 工作流 B — 自定义公式 signal_monitor

```bash
# 若有 job 级别报告模板或参考资料（归因框架文档等），在 add 时传入
python scripts/cli.py add <id> --task-type signal_monitor --scaffold \
  [--template-file path/to/报告模板.md] \
  [--reference-files path/to/框架.md,path/to/其他参考.md] \
  [--run-sop "额外运行约束文本"]
# 对话层调 quant-buddy-skill 生成公式
python scripts/cli.py set <id> signal.formulas '[{"name":"...","expression":"..."}]'
python scripts/cli.py set <id> signal.trigger_formula "信号"
python scripts/cli.py set <id> signal.display_fields '["字段1","字段2"]'
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"
python scripts/cli.py validate <id>
python scripts/cli.py apply-schedule <id>
```

### 工作流 C — 修改已有 job 并重新部署

```bash
python scripts/cli.py show <id>                  # 查看当前配置
python scripts/cli.py set <id> <jsonpath> <value>
python scripts/cli.py validate <id>
python scripts/cli.py apply-schedule <id>        # 仅在改了 schedule 字段时需要
```

### 工作流 D — 快速建 stock_picker（preset）

```bash
python scripts/cli.py add <id> --task-type stock_picker --from preset:stock_picker_value
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"
python scripts/cli.py apply-schedule <id>
```

### 工作流 E — 自定义公式 stock_picker

```bash
python scripts/cli.py add <id> --task-type stock_picker --scaffold
# 对话层调 quant-buddy-skill（confirmDataMulti → TopN 公式链）
python scripts/cli.py set <id> formulas '["公式1","公式2","Top结果=取前(...)"]'
python scripts/cli.py set <id> result_handler \
  '{"mode":"topn_by_mask","result_formula":"Top结果","value_columns":[...],"sort_by":"字段","sort_order":"desc","limit":10}'
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"
python scripts/cli.py validate <id>
python scripts/cli.py apply-schedule <id>
```

### 工作流 F — 暂停 / 恢复

```bash
python scripts/cli.py pause <id>
python scripts/cli.py diagnose <id>          # 确认 schtasks 状态变为 Disabled
python scripts/cli.py resume <id>
```

### 工作流 G — 彻底删除 job

> ⚠️ 此操作会删除 `jobs/<id>/` 目录及 Windows 计划任务，不可恢复。

```bash
python scripts/cli.py delete <id> --yes
```

### 工作流 H — 立即跑一次（对话场景）

不要再找 `cli run` / `cli trigger`。直接对 agent 说"现在跑一次 \<id\>"，
agent 应当按 [agent-runtime-flow.md](agent-runtime-flow.md) 的 13 步流执行：

```
1. cli show <id>          → 取 job 配置
2. cli load-assets <id>   → 取资产
3. cli load-cooldown <id> → 取冷静期
4. 实例化公式（替换 {ASSETS}）
5. 调 quant-buddy-skill.runMultiFormulaBatch（≤10 条/批，共用 task_id）
6. 提取末日值 / TopN
7. 触发判定 + 冷静期过滤
8. （signal_monitor 且 analysis_hook.enabled）WebSearch 归因
9. 按 templates/agent_report.md 写 markdown
10. cli push <id> --report-file <md>
11. （signal_monitor 且推送成功）cli mark-triggered <id> --tickers ...
12. cli save-report <id> --file <md>
13. cli save-result <id> --file <result.json>
```

---

## 常见问题

| 症状 | 可能原因 | 排查命令 |
|------|----------|----------|
| schtasks 已注册但没有推送 | cron 未到触发时间 / agent 跑时无触发 | `cli.py diagnose <id>` |
| `_run.bat` 跑了但企微没消息 | agent 在跑公式或归因阶段失败 | 看 `jobs/<id>/state/logs/YYYYMMDD.log` |
| validate 报 trigger_formula 缺失 | signal.formulas 没有匹配 `trigger_formula` 字段值的条目 | 检查 job.json |
| 推送成功但下次又被推 | mark-triggered 没调 / cooldown_days = 0 | 看 `state/triggered.json`、`cli.py show <id>` |
| 想看上次跑了什么 | `cli.py show <id>` 含 `last_result_summary` | 也可直接读 `state/last_result.json` |
