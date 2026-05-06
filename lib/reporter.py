#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告渲染：极简模板，模板里支持 {RUN_DATE} {JOB_NAME} {SUMMARY} {TRIGGERED} {SNAPSHOT} {COOLED} {ANOMALIES} {ANALYSIS} 等占位符。"""

import os
from datetime import datetime
from typing import Dict, List


_DIRECTION_ICON = {"buy": "🟢", "sell": "🔴", "watch": "🟡"}
_DIRECTION_TEXT = {"buy": "买入信号", "sell": "卖出信号", "watch": "观察信号"}


def _category_badge(job: Dict) -> str:
    cat = ((job.get("signal") or {}).get("category") or {})
    direction = (cat.get("direction") or "").lower()
    icon = _DIRECTION_ICON.get(direction, "📡")
    text = _DIRECTION_TEXT.get(direction, "信号")
    label = cat.get("label") or ""
    return f"{icon} {text}" + (f" · {label}" if label else "")


def _fmt_record(rec: Dict) -> str:
    fields = rec.get("fields") or {}
    cells = [f"{k}={v}" for k, v in fields.items()]
    return f"- **{rec['company']} ({rec['ticker']})** trigger_value={rec.get('trigger_value')} {' '.join(cells)} ({rec.get('date','')})"


def _render_table(records: List[Dict]) -> str:
    if not records:
        return "_无_"
    keys = []
    for r in records:
        for k in (r.get("fields") or {}).keys():
            if k not in keys:
                keys.append(k)
    header = "| 公司 | 代码 | 触发 | " + " | ".join(keys) + " |"
    sep = "|---|---|---|" + "|".join(["---:"] * len(keys)) + "|"
    rows = []
    for r in records:
        f = r.get("fields") or {}
        row_vals = [str(f.get(k, "")) for k in keys]
        flag = "🔔" if r.get("triggered") else ""
        rows.append(f"| {r['company']} | {r['ticker']} | {flag} | " + " | ".join(row_vals) + " |")
    return "\n".join([header, sep] + rows)


def render_default(job: Dict, result: Dict, analysis: str = "") -> str:
    name = job.get("name") or job["id"]
    run_time = result.get("run_time") or result.get("run_date", "")
    summary = result.get("summary", {})
    badge = _category_badge(job)
    cat = ((job.get("signal") or {}).get("category") or {})

    parts = [f"# {name} 监控报告 — {run_time}", ""]
    parts.append(f"> **{badge}**")
    if cat.get("description"):
        parts.append(f"> _{cat['description']}_")
    parts.append(f"> job_id: `{job['id']}` | 触发 {summary.get('triggered_count', 0)} / 冷静期 {summary.get('cooled_down_count', 0)} / 异常 {summary.get('anomalies_count', 0)} / 总资产 {summary.get('total_assets', 0)}")
    if summary.get("fatal"):
        parts.append(f"\n> ⚠️ fatal: `{summary['fatal']}`")
    parts.append("")

    parts.append("## 一、今日触发")
    if not result.get("triggered"):
        parts.append("_无_")
    else:
        for r in result["triggered"]:
            parts.append(_fmt_record(r))
    parts.append("")

    if analysis:
        parts.append("## 二、分析")
        parts.append(analysis)
        parts.append("")

    parts.append("## 三、全景快照")
    parts.append(_render_table(result.get("all_stocks") or []))
    parts.append("")

    parts.append("## 四、冷静期跳过")
    if not result.get("cooled_down"):
        parts.append("_无_")
    else:
        for r in result["cooled_down"]:
            parts.append(f"- {r['company']} ({r['ticker']}) 上次触发 {r.get('last_triggered')}，冷静期至 {r.get('cooldown_end')}")
    parts.append("")

    parts.append("## 五、数据异常")
    if not result.get("anomalies"):
        parts.append("_无_")
    else:
        for r in result["anomalies"]:
            parts.append(f"- {r.get('company','?')} ({r.get('ticker','?')}): {r.get('error','')}")
    parts.append("")

    parts.append("---")
    parts.append(f"_由 scheduled-task 生成 @ {datetime.now().isoformat(timespec='seconds')}_")
    return "\n".join(parts)


def render_stock_picker(job: Dict, result: Dict) -> str:
    name = job.get("name") or job["id"]
    run_time = result.get("run_time") or result.get("run_date", "")
    selected = result.get("selected") or []
    total_passed = result.get("total_passed", 0)
    rh = job.get("result_handler") or {}
    value_columns = rh.get("value_columns") or []

    parts = [f"# {name} 定时选股报告 — {run_time}", ""]
    parts.append(f"> job_id: `{job['id']}` | 通过筛选 {total_passed} 只 | 展示 Top{len(selected)}")
    if result.get("fatal"):
        parts.append(f"\n> ⚠️ fatal: `{result['fatal']}`")
    parts.append("")

    if not selected:
        parts.append("_无符合条件的股票_")
    else:
        col_labels = [vc["label"] for vc in value_columns]
        header = "| 排名 | 名称 | 代码 | " + " | ".join(col_labels) + " | 数据日期 |"
        sep = "|---:|---|---|" + "|".join(["---:"] * len(col_labels)) + "|---|"
        rows = [header, sep]
        for row in selected:
            fmt_vals = []
            for vc in value_columns:
                v = row.get(vc["label"])
                fmt = vc.get("format", "{}")
                try:
                    fmt_vals.append(fmt.format(float(v)) if v is not None else "")
                except (TypeError, ValueError):
                    fmt_vals.append(str(v) if v is not None else "")
            name = row.get("name") or ""
            rows.append(f"| {row.get('rank','')} | {name} | {row.get('ticker','')} | " + " | ".join(fmt_vals) + f" | {row.get('date','')} |")
        parts.extend(rows)
    parts.append("")
    parts.append("---")
    parts.append(f"_由 scheduled-task 生成 @ {datetime.now().isoformat(timespec='seconds')}_")
    return "\n".join(parts)


def render_stock_picker_push(job: Dict, result: Dict) -> str:
    name = job.get("name") or job["id"]
    run_time = result.get("run_time") or result.get("run_date", "")
    selected = result.get("selected") or []
    total_passed = result.get("total_passed", 0)
    rh = job.get("result_handler") or {}
    value_columns = rh.get("value_columns") or []
    parts = [f"**【{name}】{run_time}**", f"📊 全市场通过 {total_passed} 只 · 展示 Top{len(selected)}"]
    if result.get("fatal"):
        parts.append(f"⚠️ {result['fatal']}")
    elif selected:
        for row in selected:
            col_str = "  ".join(
                f"{vc['label']}={row.get(vc['label'], '')}" for vc in value_columns[:3]
            )
            name = row.get("name") or ""
            ticker = row.get("ticker", "")
            label = f"**{name}**（{ticker}）" if name else f"**{ticker}**"
            parts.append(f"{row.get('rank','')}.  {label}  {col_str}")
    else:
        parts.append("✅ 无符合条件股票")
    parts.append(f"\n_scheduled-task / {job['id']}_")
    return "\n".join(parts)


def write_report(job: Dict, result: Dict, base_dir: str, analysis: str = "") -> str:
    task_type = job.get("task_type", "signal_monitor")
    report_cfg = job.get("report") or {}
    out_dir_rel = report_cfg.get("output_dir") or "output/reports"
    out_dir = os.path.normpath(os.path.join(base_dir, out_dir_rel))
    os.makedirs(out_dir, exist_ok=True)
    run_date = (result.get("run_time") or result.get("run_date", "undated"))[:10]
    report_mode = report_cfg.get("report_mode", "overwrite")
    if report_mode == "incremental":
        ts = datetime.now().strftime("%H%M%S")
        fname = f"{run_date}_{ts}_{job['id']}.md"
    else:
        fname = f"{run_date}_{job['id']}.md"
    path = os.path.join(out_dir, fname)
    if task_type == "stock_picker":
        content = render_stock_picker(job, result)
    else:
        content = render_default(job, result, analysis)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def render_push(job: Dict, result: Dict) -> str:
    if job.get("task_type") == "stock_picker":
        return render_stock_picker_push(job, result)
    name = job.get("name") or job["id"]
    run_time = result.get("run_time") or result.get("run_date", "")
    triggered = result.get("triggered") or []
    badge = _category_badge(job)
    parts = [f"**【{name}】{run_time}**", badge]
    if triggered:
        parts.append(f"🔔 触发 {len(triggered)} 只：")
        for r in triggered[:8]:
            f = r.get("fields") or {}
            f_str = " ".join(f"{k}={v}" for k, v in list(f.items())[:3])
            parts.append(f"· **{r['company']} ({r['ticker']})** {f_str}")
        if len(triggered) > 8:
            parts.append(f"…另 {len(triggered)-8} 只见报告")
    else:
        parts.append("✅ 无触发")
    parts.append(f"\n_scheduled-task / {job['id']}_")
    return "\n".join(parts)
