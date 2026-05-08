#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scheduled-task 统一 CLI 入口。所有 stdout 都是 JSON。

子命令：
  list                                              列出所有 job
  show <id>                                         单 job 配置 + 上次结果摘要
  add <id> --task-type T --from preset:<name>|bundle:<name>|<file>  新增 job（从模板/bundle）
  add <id> --task-type T --scaffold                   新增空骨架（无公式）
  add <id> --task-type T --formulas-file <file>       新增 job（读公式文件）
  delete <id> [--yes]                               删除 job 目录 + 调度
  set <id> <jsonpath> <value>                       改字段（自动备份；value 是 JSON 字面量或裸字符串）
  disable-asset <id> <ticker>                       把 ticker 加入 disabled_tickers
  enable-asset <id> <ticker>                        从 disabled_tickers 移除
  validate <id>                                     公式语法校验
  test-push <id>                                    推一条测试到企微（不读 last_result）
  load-assets <id>                                  解析 asset_source，输出 [{ticker,company}] 与 disabled
  load-cooldown <id>                                输出当前冷静期 ticker 列表（供 agent 过滤）
  mark-triggered <id> --tickers T1,T2               推送成功后调用，写入 triggered.json
  push <id> --report-file <md> | --content <text>   读 markdown 推到企微
  save-result <id> --file <json>                    把 agent 跑完的结果存到 state/last_result.json
  save-report <id> --file <md>                      按 report_mode 落到 output/reports/
  reset-cooldown <id> [--ticker T|--all]            清冷静期
  pause <id>                                        暂停定时
  resume <id>                                       恢复定时
  apply-schedule <id>                               把 job.schedule 同步到 schtasks
  diagnose <id>                                     解释"今天为什么没提醒"
  history <id>                                      列出 .history/ 备份
  rollback <id> [--to <ts>]                         回滚到指定备份（默认上一份）
  run-formulas <id>                                 直连 quant_api 跑公式（执行态专用，不走 MCP 协议）
                                                    输出 {triggered/selected/errors/run_start_time} JSON
  generate-workflow <id>                            输出 job 配置摘要，供 Agent 生成 jobs/<id>/workflow.md
"""

import argparse
import io
import json
import os
import shutil
import sys
from datetime import date, datetime
from typing import Any, Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_ROOT)

from lib import job_schema, asset_source, cooldown, validator, wecom, scheduler_win  # noqa: E402


# ─────────────────────────────────────────
# helpers
# ─────────────────────────────────────────

def _emit(obj: Any, *, code: int = 0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def _err(msg: str, **extra):
    payload = {"ok": False, "error": msg}
    payload.update(extra)
    _emit(payload, code=1)


def _state_dir(job_id: str) -> str:
    return os.path.join(job_schema.data_dir(job_id), "state")


def _last_result_path(job_id: str) -> str:
    return os.path.join(_state_dir(job_id), "last_result.json")


def _save_last_result(job_id: str, payload: Dict):
    os.makedirs(_state_dir(job_id), exist_ok=True)
    with open(_last_result_path(job_id), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_last_result(job_id: str) -> Dict:
    p = _last_result_path(job_id)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, "r", encoding="utf-8")) or {}
    except Exception:
        return {}


def _parse_value(raw: str) -> Any:
    """优先 JSON 解析，失败再当裸字符串。"""
    if raw is None:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ─────────────────────────────────────────
# commands
# ─────────────────────────────────────────

def cmd_list(args):
    reg = job_schema.rebuild_registry()
    _emit({"ok": True, **reg})


def cmd_show(args):
    job = job_schema.load_job(args.id)
    last = _load_last_result(args.id)
    sched = scheduler_win.query(job)
    _emit({"ok": True, "job": job,
           "last_result_summary": last.get("summary") if last else None,
           "last_result_run_time": (last.get("run_time") or last.get("run_date")) if last else None,
           "scheduler": sched})


def cmd_add(args):
    if os.path.exists(job_schema.job_file(args.id)):
        _err(f"job 已存在: {args.id}")

    bundle_dir_src = None  # bundle 模式时指向 bundle 根目录

    if args.scaffold:
        # 建空骨架：task_type 必须由 --task-type 指定
        task_type = args.task_type or "signal_monitor"
        data = {
            "id": args.id,
            "name": args.id,
            "task_type": task_type,
            "enabled": True,
            "formulas": [] if task_type == "stock_picker" else None,
            "result_handler": {} if task_type == "stock_picker" else None,
            "runMultiFormula_args": {"begin_date": "auto_minus_60d", "use_minute_data": True} if task_type == "stock_picker" else None,
            "cooldown_days": 0 if task_type == "stock_picker" else 7,
            "notification": {"wecom": {"enabled": True, "webhook": "", "mentioned_list": [], "mentioned_mobile_list": []}, "push_when": "always" if task_type == "stock_picker" else "triggered_only"},
            "schedule": {"enabled": True, "cron": None, "time": "08:30", "task_name": ""},
            "report": {"output_dir": "output/reports", "report_mode": "incremental" if task_type == "stock_picker" else "overwrite"},
        }
        # 清理 None 值（不写入不相关字段）
        data = {k: v for k, v in data.items() if v is not None}
        data["schedule"]["task_name"] = f"ScheduledTask_{args.id}"
    elif args.formulas_file:
        # 从 formulas JSON 文件创建 stock_picker job
        if not os.path.exists(args.formulas_file):
            _err(f"formulas-file 不存在: {args.formulas_file}")
        with open(args.formulas_file, "r", encoding="utf-8") as f:
            fdata = json.load(f)
        task_type = args.task_type or "stock_picker"
        data = {
            "id": args.id, "name": args.id, "task_type": task_type, "enabled": True,
            "formulas": fdata if isinstance(fdata, list) else fdata.get("formulas", []),
            "result_handler": fdata.get("result_handler", {}) if isinstance(fdata, dict) else {},
            "runMultiFormula_args": fdata.get("runMultiFormula_args", {"begin_date": "auto_minus_60d", "use_minute_data": True}) if isinstance(fdata, dict) else {},
            "cooldown_days": 0,
            "notification": {"wecom": {"enabled": True, "webhook": "", "mentioned_list": [], "mentioned_mobile_list": []}, "push_when": "always"},
            "schedule": {"enabled": True, "cron": None, "time": "08:30", "task_name": f"ScheduledTask_{args.id}"},
            "report": {"output_dir": "output/reports", "report_mode": "incremental"},
        }
    else:
        src = args.from_
        if not src:
            _err("需要 --from, --scaffold, 或 --formulas-file")
        if src.startswith("bundle:"):
            bundle_name = src.split(":", 1)[1]
            bundle_dir_src = os.path.join(SKILL_ROOT, "jobs", "_bundles", bundle_name)
            path = os.path.join(bundle_dir_src, "bundle.json")
        elif src.startswith("preset:"):
            name = src.split(":", 1)[1]
            path = os.path.join(SKILL_ROOT, "jobs", "_presets", f"{name}.json")
        else:
            path = src
        if not os.path.exists(path):
            _err(f"模板不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["id"] = args.id
        if not data.get("name"):
            data["name"] = args.id
        if args.task_type:
            data["task_type"] = args.task_type
        data.setdefault("schedule", {})["task_name"] = f"ScheduledTask_{args.id}"

    job_schema.save_job(args.id, data, backup=False)
    job_dir = job_schema.job_dir(args.id)

    # ── bundle 模式：自动复制资产池、模板、参考资料、run_sop ──────────
    bundle_copied_assets = None
    if bundle_dir_src:
        # assets/ → jobs/<id>/assets.xlsx（取第一个文件）
        b_assets = os.path.join(bundle_dir_src, "assets")
        if os.path.isdir(b_assets):
            for fname in os.listdir(b_assets):
                src_f = os.path.join(b_assets, fname)
                dst_f = os.path.join(job_dir, fname)
                shutil.copy2(src_f, dst_f)
                bundle_copied_assets = fname
            # 若 bundle.json 里 asset_source.path 已配置则不覆盖
            if not (data.get("asset_source") or {}).get("path"):
                data.setdefault("asset_source", {}).update(
                    {"type": "excel", "path": bundle_copied_assets, "disabled_tickers": []}
                )
                job_schema.save_job(args.id, data, backup=False)

        # templates/ → jobs/<id>/templates/（若未传 --template-file）
        if not args.template_file:
            b_tpl = os.path.join(bundle_dir_src, "templates")
            if os.path.isdir(b_tpl):
                tpl_dir = os.path.join(job_dir, "templates")
                os.makedirs(tpl_dir, exist_ok=True)
                first_tpl = None
                for fname in os.listdir(b_tpl):
                    shutil.copy2(os.path.join(b_tpl, fname), os.path.join(tpl_dir, fname))
                    if first_tpl is None:
                        first_tpl = "templates/" + fname
                # 若 bundle.json 里 report.job_template 已配置则不覆盖
                if first_tpl and not (data.get("report") or {}).get("job_template"):
                    data.setdefault("report", {})["job_template"] = first_tpl
                    job_schema.save_job(args.id, data, backup=False)

        # references/ → jobs/<id>/references/（若未传 --reference-files）
        if not args.reference_files:
            b_refs = os.path.join(bundle_dir_src, "references")
            if os.path.isdir(b_refs):
                ref_dir = os.path.join(job_dir, "references")
                os.makedirs(ref_dir, exist_ok=True)
                ref_paths = []
                for fname in os.listdir(b_refs):
                    shutil.copy2(os.path.join(b_refs, fname), os.path.join(ref_dir, fname))
                    ref_paths.append("references/" + fname)
                # 若 bundle.json 里 context.references 已配置则不覆盖
                if ref_paths and not (data.get("context") or {}).get("references"):
                    data.setdefault("context", {})["references"] = ref_paths
                    job_schema.save_job(args.id, data, backup=False)

        # run_sop.md → job.context.run_sop（若未传 --run-sop 且 bundle.json 里为空）
        if not args.run_sop:
            b_sop = os.path.join(bundle_dir_src, "run_sop.md")
            if os.path.exists(b_sop) and not (data.get("context") or {}).get("run_sop"):
                with open(b_sop, "r", encoding="utf-8") as f:
                    sop_text = f.read().strip()
                if sop_text:
                    data.setdefault("context", {})["run_sop"] = sop_text
                    job_schema.save_job(args.id, data, backup=False)

    # ── 复制 job 级别模板 ──────────────────────────────────────
    copied_template = None
    if args.template_file:
        src = args.template_file
        if not os.path.isabs(src):
            src = os.path.normpath(os.path.join(os.getcwd(), src))
        if not os.path.exists(src):
            _err(f"--template-file 不存在: {src}")
        tpl_dir = os.path.join(job_dir, "templates")
        os.makedirs(tpl_dir, exist_ok=True)
        dest = os.path.join(tpl_dir, os.path.basename(src))
        shutil.copy2(src, dest)
        rel = os.path.relpath(dest, job_dir).replace("\\", "/")
        data.setdefault("report", {})["job_template"] = rel
        copied_template = rel
        job_schema.save_job(args.id, data, backup=False)  # 用模板路径更新 job.json

    # ── 复制参考资料 ───────────────────────────────────────────
    copied_refs = []
    if args.reference_files:
        ref_dir = os.path.join(job_dir, "references")
        os.makedirs(ref_dir, exist_ok=True)
        ref_paths = []
        for raw in args.reference_files.split(","):
            src = raw.strip()
            if not src:
                continue
            if not os.path.isabs(src):
                src = os.path.normpath(os.path.join(os.getcwd(), src))
            if not os.path.exists(src):
                _err(f"--reference-files 中有文件不存在: {src}")
            dest = os.path.join(ref_dir, os.path.basename(src))
            shutil.copy2(src, dest)
            rel = os.path.relpath(dest, job_dir).replace("\\", "/")
            ref_paths.append(rel)
            copied_refs.append(rel)
        if ref_paths:
            data.setdefault("context", {})["references"] = ref_paths
            job_schema.save_job(args.id, data, backup=False)

    # ── 写入 run_sop ──────────────────────────────────────────
    if args.run_sop:
        data.setdefault("context", {})["run_sop"] = args.run_sop
        job_schema.save_job(args.id, data, backup=False)

    job_schema.rebuild_registry()
    task_type = data.get("task_type", "signal_monitor")
    if task_type == "stock_picker":
        next_steps = [
            f"cli.py set {args.id} notification.wecom.webhook <webhook_url>",
            f"cli.py set {args.id} schedule.cron \"*/30 9-15 * * 1-5\"",
            f"cli.py apply-schedule {args.id}",
        ]
    elif bundle_copied_assets:
        # bundle 已自动配好资产池，跳过 set asset_source 步骤
        next_steps = [
            f"cli.py set {args.id} notification.wecom.webhook <webhook_url>",
            f"cli.py set {args.id} schedule.cron \"*/20 * * * *\"",
            f"cli.py validate {args.id}",
            f"cli.py apply-schedule {args.id}",
        ]
    else:
        next_steps = [
            f"cli.py set {args.id} asset_source.type excel",
            f"cli.py set {args.id} asset_source.path assets.xlsx",
            f"cli.py validate {args.id}",
            f"cli.py apply-schedule {args.id}",
        ]
    _emit({
        "ok": True, "id": args.id, "task_type": task_type,
        "copied_template": copied_template,
        "copied_references": copied_refs,
        "bundle_assets": bundle_copied_assets,
        "next": next_steps,
    })


def cmd_delete(args):
    if not args.yes:
        _err("delete 需要 --yes 确认")
    job_dir = job_schema.job_dir(args.id)
    if not os.path.exists(job_dir):
        _err(f"job 不存在: {args.id}")
    try:
        job = job_schema.load_job(args.id)
        scheduler_win.delete(job)
    except Exception:
        pass
    shutil.rmtree(job_dir, ignore_errors=True)
    job_schema.rebuild_registry()
    _emit({"ok": True, "deleted": args.id})


def cmd_set(args):
    job = job_schema.load_job(args.id)
    value = _parse_value(args.value)
    try:
        job_schema.set_by_path(job, args.path, value)
    except Exception as e:
        _err(f"set 失败: {type(e).__name__}: {e}", path=args.path)
    backup = job_schema.save_job(args.id, job, backup=True)
    job_schema.rebuild_registry()
    hint = []
    if args.path.startswith("signal."):
        hint.append(f"建议跑：cli.py validate {args.id}")
    if args.path.startswith("schedule."):
        hint.append(f"建议跑：cli.py apply-schedule {args.id}")
    _emit({"ok": True, "id": args.id, "path": args.path, "new_value": value,
           "backup": os.path.relpath(backup, SKILL_ROOT).replace("\\", "/") if backup else None,
           "hint": hint})


def _toggle_disabled(args, *, enable: bool):
    job = job_schema.load_job(args.id)
    src = job.setdefault("asset_source", {})
    disabled = set(src.get("disabled_tickers") or [])
    changed = False
    if enable and args.ticker in disabled:
        disabled.remove(args.ticker); changed = True
    if not enable and args.ticker not in disabled:
        disabled.add(args.ticker); changed = True
    src["disabled_tickers"] = sorted(disabled)
    backup = job_schema.save_job(args.id, job, backup=True) if changed else None
    _emit({"ok": True, "id": args.id, "ticker": args.ticker,
           "disabled_tickers": src["disabled_tickers"], "changed": changed,
           "backup": os.path.relpath(backup, SKILL_ROOT).replace("\\", "/") if backup else None})


def cmd_disable_asset(args):
    _toggle_disabled(args, enable=False)


def cmd_enable_asset(args):
    _toggle_disabled(args, enable=True)


def cmd_validate(args):
    job = job_schema.load_job(args.id)
    task_type = job.get("task_type", "signal_monitor")
    if task_type == "stock_picker":
        result = validator.validate(job, [])
    else:
        try:
            assets = asset_source.load_assets(job["asset_source"], job_schema.job_dir(args.id))
        except Exception as e:
            _err(f"加载资产失败: {e}")
        result = validator.validate(job, assets)
    _emit({"ok": result["ok"], "id": args.id, **result})


def cmd_load_assets(args):
    """解析 asset_source（excel/csv/inline），输出资产列表与 disabled 名单。

    供 agent 在运行时第一步调用。"""
    job = job_schema.load_job(args.id)
    base_dir = job_schema.job_dir(args.id)
    src = job.get("asset_source") or {}
    task_type = job.get("task_type", "signal_monitor")

    if task_type == "stock_picker":
        # stock_picker 可能没有 asset_source（全市场扫）
        _emit({"ok": True, "id": args.id, "task_type": task_type,
               "assets": [], "disabled_tickers": list(src.get("disabled_tickers") or [])})

    try:
        assets = asset_source.load_assets(src, base_dir)
    except Exception as e:
        _err(f"加载资产失败: {type(e).__name__}: {e}")
    _emit({
        "ok": True,
        "id": args.id,
        "task_type": task_type,
        "asset_source_type": src.get("type"),
        "assets": assets,
        "disabled_tickers": sorted(set(src.get("disabled_tickers") or [])),
        "count": len(assets),
    })


def cmd_load_cooldown(args):
    """输出当前冷静期内的 ticker（agent 在判触发前需要先过滤掉这些）。"""
    job = job_schema.load_job(args.id)
    state_dir = _state_dir(args.id)
    today = date.today()
    cooldown_days = int(job.get("cooldown_days", 7))
    state = cooldown.load_state(state_dir)
    cooled = []
    for ticker, last in state.items():
        if cooldown.is_cooled_down(last, today, cooldown_days):
            cooled.append({
                "ticker": ticker,
                "last_triggered": last,
                "cooldown_end": cooldown.cooldown_end(last, cooldown_days),
            })
    _emit({
        "ok": True,
        "id": args.id,
        "today": today.strftime("%Y-%m-%d"),
        "cooldown_days": cooldown_days,
        "cooled_tickers": [c["ticker"] for c in cooled],
        "cooled_detail": cooled,
        "raw_state": state,
    })


def cmd_mark_triggered(args):
    """推送成功后由 agent 调用：把 ticker 写进 triggered.json，启动冷静期。"""
    state_dir = _state_dir(args.id)
    os.makedirs(state_dir, exist_ok=True)
    tickers = [t.strip() for t in (args.tickers or "").split(",") if t.strip()]
    if not tickers:
        _err("--tickers 不能为空（用逗号分隔）")
    state = cooldown.load_state(state_dir)
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for t in tickers:
        state[t] = now_iso
    cooldown.save_state(state_dir, state)
    _emit({"ok": True, "id": args.id, "marked": tickers, "at": now_iso})


def cmd_push(args):
    """读 markdown 文件（或 --content 字符串）推到企微。"""
    job = job_schema.load_job(args.id)
    wecom_cfg = ((job.get("notification") or {}).get("wecom") or {})
    if not wecom_cfg.get("enabled"):
        _err("notification.wecom.enabled = false，拒绝推送")
    webhook = wecom_cfg.get("webhook") or ""
    if not webhook:
        _err("notification.wecom.webhook 为空")

    if args.report_file:
        path = args.report_file
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(SKILL_ROOT, path))
        if not os.path.exists(path):
            _err(f"报告文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.content:
        text = args.content
    else:
        _err("需要 --report-file <md> 或 --content <text>")

    res = wecom.push_markdown(
        webhook, text,
        mentioned_list=wecom_cfg.get("mentioned_list") or None,
        mentioned_mobile_list=wecom_cfg.get("mentioned_mobile_list") or None,
    )
    _emit({"ok": res["ok"], "id": args.id, "push": res, "bytes": len(text.encode("utf-8"))})


def cmd_save_result(args):
    """把 agent 跑完的 JSON 结果存到 state/last_result.json，供 show / diagnose 读。"""
    if not os.path.exists(args.file):
        _err(f"结果文件不存在: {args.file}")
    try:
        payload = json.load(open(args.file, "r", encoding="utf-8"))
    except Exception as e:
        _err(f"解析 JSON 失败: {type(e).__name__}: {e}")
    if not isinstance(payload, dict):
        _err("结果 JSON 顶层必须是对象")
    payload.setdefault("id", args.id)
    payload.setdefault("run_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _save_last_result(args.id, payload)
    job_schema.rebuild_registry()
    _emit({"ok": True, "id": args.id, "saved_to": _last_result_path(args.id)})


def cmd_save_report(args):
    """按 job.report.report_mode 把 agent 写的 markdown 存到 jobs/<id>/<output_dir>/。"""
    if not os.path.exists(args.file):
        _err(f"报告文件不存在: {args.file}")
    job = job_schema.load_job(args.id)
    mode = (job.get("report") or {}).get("report_mode", "overwrite")
    out_dir_rel = (job.get("report") or {}).get("output_dir", "output/reports")
    out_dir = os.path.normpath(os.path.join(job_schema.data_dir(args.id), out_dir_rel))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.file, "r", encoding="utf-8") as f:
        text = f.read()

    if mode == "incremental":
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = os.path.join(out_dir, f"{ts}.md")
    else:
        dest = os.path.join(out_dir, "latest.md")

    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    _emit({
        "ok": True,
        "id": args.id,
        "mode": mode,
        "saved_to": os.path.relpath(dest, SKILL_ROOT).replace("\\", "/"),
        "bytes": len(text.encode("utf-8")),
    })


def cmd_test_push(args):
    """推一条静态测试消息到企微（不依赖 last_result，不依赖任何渲染逻辑）。"""
    job = job_schema.load_job(args.id)
    wecom_cfg = ((job.get("notification") or {}).get("wecom") or {})
    text = (
        f"[TEST] **{job.get('name') or job['id']}**\n"
        f"_scheduled-task 测试推送 @ {datetime.now().isoformat(timespec='seconds')}_\n"
        f"id: `{job['id']}` | task_type: `{job.get('task_type', 'signal_monitor')}`"
    )
    res = wecom.push_markdown(
        wecom_cfg.get("webhook", ""), text,
        mentioned_list=wecom_cfg.get("mentioned_list") or None,
        mentioned_mobile_list=wecom_cfg.get("mentioned_mobile_list") or None,
    )
    _emit({"ok": res["ok"], "id": args.id, "push": res, "preview": text})


def cmd_run_formulas(args):
    """直连 quant_api 跑公式，输出完整结果 JSON（含触发/选股/冷静期）。

    执行态专用：不走 MCP 协议，省去 quant-buddy-skill 文档加载和 MCP 协议开销。
    批次管理、begin_date 计算、{ASSETS} 替换、触发判定、冷静期过滤全部在此完成。
    Claude 读取返回的 JSON 后执行归因、写报告、推送等后续步骤。
    """
    from datetime import timedelta

    job = job_schema.load_job(args.id)
    task_type = job.get("task_type", "signal_monitor")
    state_dir = _state_dir(args.id)
    job_dir_path = job_schema.job_dir(args.id)
    today = date.today()
    run_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 初始化 quant_api（直连 HTTP，不走 MCP）
    try:
        from lib import quant_buddy
        api = quant_buddy.get_api()
    except Exception as e:
        _err(f"quant_api 初始化失败: {type(e).__name__}: {e}")

    try:
        task_id = api.new_session()
    except Exception as e:
        _err(f"new_session 失败: {type(e).__name__}: {e}")

    # 加载资产池（signal_monitor 需要；stock_picker 跳过）
    assets = []
    if task_type == "signal_monitor":
        try:
            assets = asset_source.load_assets(job.get("asset_source", {}), job_dir_path)
        except Exception as e:
            _err(f"加载资产池失败: {type(e).__name__}: {e}")
        if not assets:
            _err(f"资产池为空（job {args.id}），请先配置 asset_source")

    # 加载冷静期
    cooldown_days = int(job.get("cooldown_days", 0))
    cooled_tickers: list = []
    if task_type == "signal_monitor" and cooldown_days > 0:
        cd_state = cooldown.load_state(state_dir)
        for ticker, last in cd_state.items():
            if cooldown.is_cooled_down(last, today, cooldown_days):
                cooled_tickers.append(ticker)

    # 准备公式列表（signal_monitor 替换 {ASSETS} 占位符）
    if task_type == "signal_monitor":
        signal = job.get("signal") or {}
        raw_formulas = signal.get("formulas") or []
        asset_names = ", ".join(a["company"] for a in assets)
        formulas = []
        for f in raw_formulas:
            if isinstance(f, dict):
                name = f.get("name", "")
                expr = f.get("expression", "")
            else:
                parts = str(f).split("=", 1)
                name = parts[0].strip()
                expr = parts[1].strip() if len(parts) > 1 else ""
            expr = expr.replace("{ASSETS}", asset_names)
            formulas.append(f"{name} = {expr}")
        lookback_days = int(signal.get("lookback_days", 60))
        begin_date = (today - timedelta(days=int(lookback_days * 1.4))).strftime("%Y%m%d")
    else:
        formulas = list(job.get("formulas") or [])
        rma = job.get("runMultiFormula_args") or {}
        bd = rma.get("begin_date", "auto_minus_60d")
        if isinstance(bd, int):
            begin_date = str(bd)
        elif bd == "auto_minus_120d":
            begin_date = (today - timedelta(days=180)).strftime("%Y%m%d")
        else:  # auto_minus_60d 或其他
            begin_date = (today - timedelta(days=90)).strftime("%Y%m%d")

    if not formulas:
        _err(f"job {args.id} 无公式（formulas 为空）")

    # 分批跑公式（单批 ≤10 条，共用 task_id，force_reusable_array=true）
    BATCH_SIZE = 10
    all_errors: list = []
    all_results: list = []
    merged_lcf: dict = {}  # last_column_full 合并

    for i in range(0, len(formulas), BATCH_SIZE):
        batch = formulas[i:i + BATCH_SIZE]
        try:
            result = api.run_multi_formula(
                formulas=batch,
                begin_date=int(begin_date),
                task_id=task_id,
                force_reusable_array=True,
                use_minute_data=True,
            )
        except Exception as e:
            all_errors.append({"batch_start": i, "error": f"{type(e).__name__}: {e}"})
            continue
        all_errors.extend(result.get("errors") or [])
        all_results.extend(result.get("results") or [])
        merged_lcf.update(result.get("last_column_full") or {})

    # 提取末日值 / 触发判定 / TopN 排序
    triggered: list = []
    cooled_down: list = list(cooled_tickers)
    selected: list = []

    if task_type == "signal_monitor":
        trigger_formula = (job.get("signal") or {}).get("trigger_formula", "信号")
        entry = merged_lcf.get(trigger_formula) or {}
        tickers_l = entry.get("tickers") or entry.get("ticker_list") or []
        values_l = entry.get("values") or []
        cooled_set = set(cooled_tickers)
        for ticker, val in zip(tickers_l, values_l):
            try:
                v = float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                v = 0.0
            if v >= 0.5:
                if ticker not in cooled_set:
                    triggered.append(ticker)
    else:
        rh = job.get("result_handler") or {}
        result_formula = rh.get("result_formula", "")
        entry = merged_lcf.get(result_formula) or {}
        tickers_l = entry.get("tickers") or entry.get("ticker_list") or []
        values_l = entry.get("values") or []
        value_columns = rh.get("value_columns") or []
        candidates: list = []
        for ticker, val in zip(tickers_l, values_l):
            try:
                v = float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                v = 0.0
            if v != 0:
                row: dict = {"ticker": ticker, result_formula: val}
                for vc in value_columns:
                    vc_from = vc.get("from", "")
                    vc_label = vc.get("label", vc_from)
                    vc_entry = merged_lcf.get(vc_from) or {}
                    vc_tickers = vc_entry.get("tickers") or vc_entry.get("ticker_list") or []
                    vc_vals = vc_entry.get("values") or []
                    if ticker in vc_tickers:
                        idx = vc_tickers.index(ticker)
                        row[vc_label] = vc_vals[idx] if idx < len(vc_vals) else None
                    else:
                        row[vc_label] = None
                candidates.append(row)
        sort_by = rh.get("sort_by", "")
        sort_order = rh.get("sort_order", "desc")
        if sort_by and candidates:
            reverse = sort_order != "asc"

            def _sort_key(r: dict) -> float:
                val = r.get(sort_by)
                try:
                    return float(val) if val is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0

            candidates.sort(key=_sort_key, reverse=reverse)
        limit = int(rh.get("limit", 50))
        selected = candidates[:limit]

    _emit({
        "ok": True,
        "id": args.id,
        "task_type": task_type,
        "run_start_time": run_start_time,
        "today": today.strftime("%Y-%m-%d"),
        "begin_date": begin_date,
        "task_id": task_id,
        "formula_count": len(formulas),
        "batch_count": (len(formulas) + BATCH_SIZE - 1) // BATCH_SIZE,
        "errors": all_errors,
        "results_count": len(all_results),
        # signal_monitor 结果（经冷静期过滤）
        "triggered": triggered,
        "cooled_down": cooled_down,
        # stock_picker 结果（TopN，已按 sort_by 排序）
        "selected": selected,
        # 供 Claude 调试用
        "last_column_full_keys": list(merged_lcf.keys()),
    })


def cmd_generate_workflow(args):
    """输出 job 配置摘要，供 Agent 生成 jobs/<id>/workflow.md。

    此命令本身不生成 workflow.md —— 它输出 job 配置摘要 + 指引，
    Agent 根据摘要和 docs/workflow-generation.md 的裁剪规则，
    选择正确的模板骨架并填写具体值后写入目标路径。
    """
    job = job_schema.load_job(args.id)
    job_dir_path = job_schema.job_dir(args.id)
    workflow_path = os.path.join(job_dir_path, "workflow.md")
    already_exists = os.path.exists(workflow_path)
    task_type = job.get("task_type", "signal_monitor")
    cooldown_days = int(job.get("cooldown_days", 7 if task_type == "signal_monitor" else 0))
    analysis_hook = job.get("analysis_hook") or {}
    notification = job.get("notification") or {}
    rh = job.get("result_handler") or {}
    _emit({
        "ok": True,
        "id": args.id,
        "workflow_target_path": os.path.relpath(workflow_path, SKILL_ROOT).replace("\\", "/"),
        "already_exists": already_exists,
        "task_type": task_type,
        "name": job.get("name", args.id),
        "cooldown_days": cooldown_days,
        "analysis_hook_enabled": analysis_hook.get("enabled", False),
        "analysis_hook_framework_doc": analysis_hook.get("framework_doc", ""),
        "push_when": notification.get("push_when", "triggered_only"),
        "has_references": bool((job.get("context") or {}).get("references")),
        "has_run_sop": bool(((job.get("context") or {}).get("run_sop") or "").strip()),
        "report_mode": (job.get("report") or {}).get("report_mode", "overwrite"),
        "job_summary": {
            "formulas_count": len(job.get("formulas") or (job.get("signal") or {}).get("formulas") or []),
            "display_fields": (job.get("signal") or {}).get("display_fields") or [],
            "trigger_formula": (job.get("signal") or {}).get("trigger_formula", ""),
            "result_formula": rh.get("result_formula", ""),
            "value_column_labels": [vc.get("label", "") for vc in rh.get("value_columns") or []],
        },
        "instruction": (
            "请参考 docs/workflow-generation.md 的裁剪规则，"
            "根据以上 job 配置，选择对应的模板骨架（templates/workflow_stock_picker.md "
            "或 templates/workflow_signal_monitor.md），填写具体值后生成 workflow.md，"
            "写入 workflow_target_path 指定的路径。"
        ),
    })


def cmd_reset_cooldown(args):
    job = job_schema.load_job(args.id)
    state_dir = _state_dir(args.id)
    if args.all:
        n = cooldown.reset(state_dir, ticker=None)
        _emit({"ok": True, "id": args.id, "cleared": n, "scope": "all"})
    elif args.ticker:
        n = cooldown.reset(state_dir, ticker=args.ticker)
        _emit({"ok": True, "id": args.id, "cleared": n, "ticker": args.ticker})
    else:
        _err("需要 --ticker T 或 --all")


def cmd_pause(args):
    job = job_schema.load_job(args.id)
    res = scheduler_win.pause(job)
    backup = None
    if res.get("ok"):
        sched = job.setdefault("schedule", {})
        if sched.get("enabled") is not False:
            sched["enabled"] = False
            backup = job_schema.save_job(args.id, job, backup=True)
            job_schema.rebuild_registry()
    _emit({"ok": res["ok"], "id": args.id, "scheduler": res,
           "schedule_enabled": False if res.get("ok") else job.get("schedule", {}).get("enabled"),
           "backup": os.path.relpath(backup, SKILL_ROOT).replace("\\", "/") if backup else None})


def cmd_resume(args):
    job = job_schema.load_job(args.id)
    res = scheduler_win.resume(job)
    backup = None
    if res.get("ok"):
        sched = job.setdefault("schedule", {})
        if sched.get("enabled") is not True:
            sched["enabled"] = True
            backup = job_schema.save_job(args.id, job, backup=True)
            job_schema.rebuild_registry()
    _emit({"ok": res["ok"], "id": args.id, "scheduler": res,
           "schedule_enabled": True if res.get("ok") else job.get("schedule", {}).get("enabled"),
           "backup": os.path.relpath(backup, SKILL_ROOT).replace("\\", "/") if backup else None})


def cmd_apply_schedule(args):
    job = job_schema.load_job(args.id)
    try:
        res = scheduler_win.apply_schedule(job)
    except Exception as e:
        _err(f"apply-schedule 失败: {type(e).__name__}: {e}")
    _emit({"ok": res["ok"], "id": args.id, "scheduler": res})


def cmd_diagnose(args):
    job = job_schema.load_job(args.id)
    state_dir = _state_dir(args.id)
    today = date.today()
    cooldown_days = int(job.get("cooldown_days", 7))

    state = cooldown.load_state(state_dir)
    cooled = []
    for ticker, last in state.items():
        if cooldown.is_cooled_down(last, today, cooldown_days):
            cooled.append({"ticker": ticker, "last_triggered": last,
                           "cooldown_end": cooldown.cooldown_end(last, cooldown_days)})

    last_result = _load_last_result(args.id)
    sched = scheduler_win.query(job)

    qb_ok = True; qb_err = None
    try:
        from lib import quant_buddy
        quant_buddy.find_quant_buddy()
    except Exception as e:
        qb_ok = False; qb_err = str(e)

    diagnosis = []
    if not job.get("enabled", True):
        diagnosis.append("job.enabled = false（job 整体被关闭）")
    if not (job.get("schedule") or {}).get("enabled", True):
        diagnosis.append("schedule.enabled = false（定时本身被关闭）")
    if sched.get("ok") and sched.get("info", {}).get("Scheduled Task State", "").lower() == "disabled":
        diagnosis.append("schtasks 任务处于 Disabled 状态（运行 cli.py resume 恢复）")
    if not sched.get("exists"):
        diagnosis.append("schtasks 找不到该任务（跑 cli.py apply-schedule 注册）")
    last_run_date = (last_result.get("run_time") or last_result.get("run_date")) if last_result else None
    if last_run_date and (last_run_date or "")[:10] != today.strftime("%Y-%m-%d"):
        diagnosis.append(f"最近一次运行是 {last_run_date}，今天没跑过")
    if last_result and (last_result.get("summary") or {}).get("triggered_count") == 0:
        diagnosis.append("上次跑完触发数=0（信号本就没命中）")
    if cooled:
        diagnosis.append(f"{len(cooled)} 个 ticker 命中冷静期（reset-cooldown 可清）")
    if not (((job.get("notification") or {}).get("wecom") or {}).get("enabled")):
        diagnosis.append("notification.wecom.enabled = false（推送被关闭）")
    if not qb_ok:
        diagnosis.append(f"quant-buddy-skill 不可达：{qb_err}")

    _emit({
        "ok": True,
        "id": args.id,
        "today": today.strftime("%Y-%m-%d"),
        "job_enabled": job.get("enabled", True),
        "schedule_enabled": (job.get("schedule") or {}).get("enabled", True),
        "scheduler": sched,
        "cooldown_hits": cooled,
        "last_result_summary": last_result.get("summary") if last_result else None,
        "last_run_time": last_run_date,
        "quant_buddy_reachable": qb_ok,
        "diagnosis": diagnosis or ["看起来一切正常；如果今天还没到调度时间，请耐心等待"],
    })


def cmd_history(args):
    items = job_schema.list_history(args.id)
    _emit({"ok": True, "id": args.id, "history": items})


def cmd_rollback(args):
    target = job_schema.rollback(args.id, to_timestamp=args.to)
    _emit({"ok": True, "id": args.id, "rolled_back_to": target})


# ─────────────────────────────────────────
# argparse
# ─────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(prog="scheduled-task")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    sp = sub.add_parser("show"); sp.add_argument("id"); sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("add"); sp.add_argument("id")
    sp.add_argument("--from", dest="from_", default=None, help="preset:<name>、bundle:<name> 或文件路径")
    sp.add_argument("--task-type", dest="task_type", default=None, help="signal_monitor 或 stock_picker")
    sp.add_argument("--scaffold", action="store_true", help="建空骨架（无公式）")
    sp.add_argument("--formulas-file", dest="formulas_file", default=None, help="从 JSON 文件读取公式")
    sp.add_argument("--template-file", dest="template_file", default=None, help="job 级别报告模板 .md，复制到 jobs/<id>/templates/")
    sp.add_argument("--reference-files", dest="reference_files", default=None, help="逗号分隔的参考资料文件列表，复制到 jobs/<id>/references/")
    sp.add_argument("--run-sop", dest="run_sop", default=None, help="给 agent 的额外运行 SOP 文本，写入 job.context.run_sop")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("delete"); sp.add_argument("id"); sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("set"); sp.add_argument("id"); sp.add_argument("path"); sp.add_argument("value")
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("disable-asset"); sp.add_argument("id"); sp.add_argument("ticker")
    sp.set_defaults(func=cmd_disable_asset)
    sp = sub.add_parser("enable-asset"); sp.add_argument("id"); sp.add_argument("ticker")
    sp.set_defaults(func=cmd_enable_asset)

    sp = sub.add_parser("validate"); sp.add_argument("id"); sp.set_defaults(func=cmd_validate)

    # ── agent-path 运行时原语 ─────────────────────────────────
    sp = sub.add_parser("load-assets"); sp.add_argument("id"); sp.set_defaults(func=cmd_load_assets)

    sp = sub.add_parser("load-cooldown"); sp.add_argument("id"); sp.set_defaults(func=cmd_load_cooldown)

    sp = sub.add_parser("mark-triggered"); sp.add_argument("id")
    sp.add_argument("--tickers", required=True, help="逗号分隔的 ticker 列表")
    sp.set_defaults(func=cmd_mark_triggered)

    sp = sub.add_parser("push"); sp.add_argument("id")
    sp.add_argument("--report-file", dest="report_file", default=None, help="markdown 文件路径")
    sp.add_argument("--content", default=None, help="直接传文本（与 --report-file 二选一）")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("save-result"); sp.add_argument("id")
    sp.add_argument("--file", required=True, help="结果 JSON 文件路径")
    sp.set_defaults(func=cmd_save_result)

    sp = sub.add_parser("save-report"); sp.add_argument("id")
    sp.add_argument("--file", required=True, help="markdown 报告文件路径")
    sp.set_defaults(func=cmd_save_report)

    sp = sub.add_parser("test-push"); sp.add_argument("id"); sp.set_defaults(func=cmd_test_push)

    sp = sub.add_parser("reset-cooldown"); sp.add_argument("id")
    sp.add_argument("--ticker"); sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_reset_cooldown)

    # ── 执行态专用 ───────────────────────────────────────────────────
    sp = sub.add_parser("run-formulas"); sp.add_argument("id"); sp.set_defaults(func=cmd_run_formulas)

    # ── workflow.md 生成辅助 ──────────────────────────────────────────
    sp = sub.add_parser("generate-workflow"); sp.add_argument("id"); sp.set_defaults(func=cmd_generate_workflow)

    sp = sub.add_parser("pause"); sp.add_argument("id"); sp.set_defaults(func=cmd_pause)
    sp = sub.add_parser("resume"); sp.add_argument("id"); sp.set_defaults(func=cmd_resume)
    sp = sub.add_parser("apply-schedule"); sp.add_argument("id"); sp.set_defaults(func=cmd_apply_schedule)
    sp = sub.add_parser("diagnose"); sp.add_argument("id"); sp.set_defaults(func=cmd_diagnose)

    sp = sub.add_parser("history"); sp.add_argument("id"); sp.set_defaults(func=cmd_history)
    sp = sub.add_parser("rollback"); sp.add_argument("id"); sp.add_argument("--to")
    sp.set_defaults(func=cmd_rollback)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except FileNotFoundError as e:
        _err(str(e))
    except Exception as e:
        _err(f"未捕获异常: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
