# CLI Reference & 工作流

所有命令从 `skill_dir/scheduled-task/` 目录运行（或根据挂载情况调整）。

---

## Step 0 — 依赖路径挂载检查

`cli.py` 启动时搜索以下 6 条路径（按优先级从高到低），找到任一即可：

```
1. ${QUANT_BUDDY_SKILL_PATH}                      # 环境变量（最高优先级）
2. ./quant-buddy-skill                             # 与 scheduled-task 平行目录
3. ../quant-buddy-skill
4. ../../quant-buddy-skill
5. ${HOME}/quant-buddy-skill
6. ${HOME}/.quant-buddy-skill
```

如果报 `quant-buddy-skill not found`，请手动设置：

```powershell
$env:QUANT_BUDDY_SKILL_PATH = "D:\path\to\quant-buddy-skill"
```

---

## CLI 速查表

| 命令 | 说明 |
|------|------|
| `python scripts/cli.py list` | 列出所有 job |
| `python scripts/cli.py show <id>` | 查看 job 完整配置 |
| `python scripts/cli.py add <id> --task-type <type> [--from preset:<name>] [--scaffold]` | 新建 job |
| `python scripts/cli.py set <id> <key> <value>` | 修改 job 某字段（支持 JSON path, 如 `signal.cooldown_days`） |
| `python scripts/cli.py validate <id>` | 验证 job.json 结构合法性 |
| `python scripts/cli.py run <id>` | 立即执行一次（正式推送） |
| `python scripts/cli.py run <id> --dry-run` | 试运行（不推送，打印结果） |
| `python scripts/cli.py apply-schedule <id>` | 注册/更新 Windows schtasks 定时任务 |
| `python scripts/cli.py remove-schedule <id>` | 删除 Windows 定时任务 |
| `python scripts/cli.py logs <id> [--last N]` | 查看最近 N 次运行日志 |
| `python scripts/cli.py status <id>` | 查看 Windows 计划任务状态 |
| `python scripts/cli.py pause <id>` | 暂停定时任务（不删除） |
| `python scripts/cli.py resume <id>` | 恢复暂停的定时任务 |
| `python scripts/cli.py delete <id>` | 删除 job 目录 + 定时任务 |
| `python scripts/cli.py set <id> asset_source.type excel` | 切换资产池到 excel 模式 |
| `python scripts/cli.py set <id> asset_source.path assets.xlsx` | 设置资产池文件路径 |

---

## 工作流

### 工作流 A — 快速建 signal_monitor（preset）

```bash
python scripts/cli.py add <id> --task-type signal_monitor --from preset:dip_2sigma
# 生成 assets.xlsx（见 signal-monitor-sop.md 资产池 SOP）
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/15 9-15 * * 1-5"
python scripts/cli.py validate <id>
python scripts/cli.py run <id> --dry-run
python scripts/cli.py apply-schedule <id>
```

### 工作流 B — 自定义公式 signal_monitor

```bash
python scripts/cli.py add <id> --task-type signal_monitor --scaffold
# 对话层调 quant-buddy-skill 确认公式
python scripts/cli.py set <id> signal.formulas '[{"name":"...","expression":"..."}]'
python scripts/cli.py set <id> signal.trigger_formula "信号"
python scripts/cli.py set <id> signal.display_fields '["字段1","字段2"]'
# 生成 assets.xlsx（见 signal-monitor-sop.md 资产池 SOP）
python scripts/cli.py set <id> asset_source.type excel
python scripts/cli.py set <id> asset_source.path assets.xlsx
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"
python scripts/cli.py validate <id>
python scripts/cli.py run <id> --dry-run
python scripts/cli.py apply-schedule <id>
```

### 工作流 C — 修改已有 job 并重新部署

```bash
python scripts/cli.py show <id>                  # 查看当前配置
python scripts/cli.py set <id> <key> <value>     # 修改字段
python scripts/cli.py validate <id>              # 验证
python scripts/cli.py run <id> --dry-run         # 试运行
python scripts/cli.py apply-schedule <id>        # 重新注册（幂等）
```

### 工作流 D — 快速建 stock_picker（preset）

```bash
python scripts/cli.py add <id> --task-type stock_picker --from preset:stock_picker_value
python scripts/cli.py set <id> notification.wecom.webhook "https://..."
python scripts/cli.py set <id> notification.wecom.enabled true
python scripts/cli.py set <id> schedule.cron "*/30 9-15 * * 1-5"
python scripts/cli.py run <id> --dry-run
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
python scripts/cli.py run <id> --dry-run
python scripts/cli.py apply-schedule <id>
```

### 工作流 F — 暂停 / 恢复

```bash
python scripts/cli.py pause <id>           # 暂停（保留 job.json）
python scripts/cli.py status <id>          # 确认状态变为 Disabled
python scripts/cli.py resume <id>          # 恢复
```

### 工作流 G — 彻底删除 job

> ⚠️ 此操作会删除 `jobs/<id>/` 目录及 Windows 计划任务，不可恢复。请用户确认后执行。

```bash
python scripts/cli.py delete <id>
```

---

## 常见问题

| 症状 | 可能原因 | 排查命令 |
|------|----------|----------|
| schtasks 已注册但没有推送 | cron 未到触发时间 / 信号未触发 | `cli.py run <id> --dry-run` |
| 推送全是"异常"股票 | quant 平台返回稀疏矩阵（非触发=NaN，不是0） | 升级 scanner.py（已内置修复） |
| cooldown_days=0 还是有去重 | 旧版 `x or 7` 语义 bug | 升级 cli.py（已内置修复） |
| validate 报 trigger_formula 缺失 | signal.formulas 没有名为 trigger_formula 值的条目 | 检查 job.json 的 `signal.trigger_formula` 字段 |
| 资产池 Excel 改了但 job 没更新 | 正常，scanner 每次运行时实时读取 xlsx 文件 | 无需操作 |
