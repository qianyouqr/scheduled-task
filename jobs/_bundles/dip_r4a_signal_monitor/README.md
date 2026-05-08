# dip_r4a_signal_monitor — R4A 抄底信号监控

**适用场景**：对固定资产池（Excel）扫 `收盘价 < MA20 − 2×STD20` 跌破下轨信号，触发时 WebSearch 归因，按"价格变了 vs 价值变了"框架给出抄底结论。

**典型触发语**：跌破下轨 / 抄底信号 / R4A / 2σ 抄底 / MA20 下轨监控 / 核心资产跌破

**自带内容**：

| 内容 | 路径（相对 jobs/<id>/） |
|------|------------------------|
| 资产池文件 | `assets.xlsx` |
| 抄底判断框架 | `references/抄底判断框架.md` |
| 报告模板 | `templates/报告模板.md` |
| 执行 SOP | 已写入 `job.context.run_sop` |

**创建命令**（一条）：

```bash
python scripts/cli.py add <job_id> --from bundle:dip_r4a_signal_monitor
python scripts/cli.py set <job_id> notification.wecom.webhook "https://..."
python scripts/cli.py set <job_id> schedule.cron "*/20 * * * *"
python scripts/cli.py apply-schedule <job_id>
```
