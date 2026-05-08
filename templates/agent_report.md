# 【{job.name}】{date}

> 执行时间：{run_start_time} | 任务类型：{task_type} | 冷静期：{cooldown_days} 天

---

## 触发结果

<!-- signal_monitor：触发列表 -->
<!-- stock_picker：Top N 入选列表 -->

| 代码 | 公司 | {display_field_1} | {display_field_2} | 数据日期 |
|------|------|-------------------|-------------------|----------|
| - | - | - | - | - |

> 触发 / 入选：{N} 只

---

## 归因分析

<!-- signal_monitor 且 analysis_hook.enabled = true 时填写 -->
<!-- stock_picker 通常跳过本节 -->

### {ticker} — {公司名}

**行业 / 政策面**：

**资金面**（北上 / 龙虎榜）：

**个股异动**：

**结论**：{可抄底 / 不能抄 / 需观察}（{仓位建议}）

---

## 冷静期跳过

<!-- signal_monitor：被 cooldown_days 过滤掉的 ticker -->
<!-- stock_picker：无冷静期，本节省略 -->

| 代码 | 公司 | 冷静期到期日 |
|------|------|-------------|
| - | - | - |

> 冷静期内跳过：{M} 只

---

## 数据异常

<!-- quant-buddy 返回的 errors[] -->

| 代码 | 错误信息 |
|------|----------|
| - | - |

---

## 运行摘要

| 项目 | 值 |
|------|----|
| 执行时间 | {run_start_time} |
| 公式批次 | {batch_count} 批 |
| 触发 / 入选 | {N} 只 |
| 冷静期跳过 | {M} 只 |
| 数据异常 | {K} 只 |
| 推送状态 | {push.ok} |
