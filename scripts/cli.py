#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scheduled-task 统一 CLI 入口。所有 stdout 都是 JSON。

子命令：
  list                                              列出所有 job
  show <id>                                         单 job 配置 + 上次结果摘要
  add <id> --task-type T --from preset:<name>|<file>  新增 job（从模板）
  add <id> --task-type T --scaffold                   新增空骨架（无公式）
  add <id> --task-type T --formulas-file <file>       新增 job（读公式文件）
  delete <id> [--yes]                               删除 job 目录 + 调度
  set <id> <jsonpath> <value>                       改字段（自动备份；value 是 JSON 字面量或裸字符串）
  disable-asset <id> <ticker>                       把 ticker 加入 disabled_tickers
  enable-asset <id> <ticker>                        从 disabled_tickers 移除
  run <id> [--dry-run]                              立即跑一次
  validate <id>                                     公式语法校验
  test-push <id>                                    强制推一条测试到企微
  reset-cooldown <id> [--ticker T|--all]            清冷静期
  pause <id>                                        暂停定时
  resume <id>                                       恢复定时
  apply-schedule <id>                               把 job.schedule 同步到 schtasks
  diagnose <id>                                     解释"今天为什么没提醒"
  history <id>                                      列出 .history/ 备份
  rollback <id> [--to <ts>]                         回滚到指定备份（默认上一份）
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

from lib import job_schema, asset_source, cooldown, scanner, validator, wecom, scheduler_win, reporter  # noqa: E402


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
    return os.path.join(job_schema.job_dir(job_id), "state")


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


def _is_in_cron_window(cron: str) -> bool:
    """对于 */N H-range * * DOW-range 类型的 cron，检查当前时刻是否在 H-range 和 DOW-range 内。"""
    import re as _re
    parts = cron.strip().split()
    if len(parts) != 5:
        return True  # 无法解析，不拦截
    _, hour, dom, mon, dow = parts
    now = datetime.now()
    # 检查 hour 范围
    if hour != "*":
        hour_match = _re.fullmatch(r"(\d+)-(\d+)", hour)
        if hour_match:
            h_start, h_end = int(hour_match.group(1)), int(hour_match.group(2))
            if not (h_start <= now.hour <= h_end):
                return False
        elif hour.isdigit():
            if now.hour != int(hour):
                return False
    # 检查 dow 范围（1=Mon,...,7=Sun，Python weekday(): Mon=0,...,Sun=6）
    if dow != "*":
        dow_match = _re.fullmatch(r"(\d+)-(\d+)", dow)
        py_dow = now.weekday() + 1  # Mon=1, Sun=7
        if dow_match:
            d_start, d_end = int(dow_match.group(1)), int(dow_match.group(2))
            if not (d_start <= py_dow <= d_end):
                return False
        elif dow.isdigit():
            if py_dow != int(dow):
                return False
    return True


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
            "notification": {"wecom": {"enabled": True, "webhook": "", "mentioned_list": [], "mentioned_mobile_list": []}, "push_when": "always"},
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
        if src.startswith("preset:"):
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
    job_schema.rebuild_registry()
    task_type = data.get("task_type", "signal_monitor")
    if task_type == "stock_picker":
        next_steps = [
            f"cli.py set {args.id} notification.wecom.webhook <webhook_url>",
            f"cli.py set {args.id} schedule.cron \"*/30 9-15 * * 1-5\"",
            f"cli.py run {args.id} --dry-run",
            f"cli.py apply-schedule {args.id}",
        ]
    else:
        next_steps = [
            f"cli.py set {args.id} asset_source.assets <list>  或编辑 jobs/{args.id}/job.json",
            f"cli.py validate {args.id}",
            f"cli.py run {args.id} --dry-run",
            f"cli.py apply-schedule {args.id}",
        ]
    _emit({"ok": True, "id": args.id, "task_type": task_type, "next": next_steps})


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


def cmd_run(args):
    job = job_schema.load_job(args.id)
    if not job.get("enabled", True) and not args.force:
        _err(f"job {args.id} 已 disabled；用 --force 强制跑")

    task_type = job.get("task_type", "signal_monitor")
    base_dir = job_schema.job_dir(args.id)
    state_dir = _state_dir(args.id)
    os.makedirs(state_dir, exist_ok=True)

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # cron 时间窗口守卫（用于 */N H-range * * DOW-range 场景）
    cron = (job.get("schedule") or {}).get("cron")
    if cron and not args.dry_run and not args.force:
        if not _is_in_cron_window(cron):
            _emit({"ok": True, "id": args.id, "skipped": True, "reason": f"当前时刻不在 cron 窗口: {cron}"})

    if task_type == "stock_picker":
        # ── stock_picker 分支 ──────────────────────────────────
        sig = scanner.run_stock_picker(job)
        payload = {
            "ok": sig.get("fatal") is None,
            "id": args.id,
            "run_time": run_time,
            "dry_run": bool(args.dry_run),
            "task_type": task_type,
            "formulas": job.get("formulas") or [],
            "selected": sig.get("selected") or [],
            "total_passed": sig.get("total_passed", 0),
            "anomalies": sig.get("anomalies") or [],
            "last_date": sig.get("last_date", ""),
            "fatal": sig.get("fatal"),
            "summary": {
                "total_passed": sig.get("total_passed", 0),
                "selected_count": len(sig.get("selected") or []),
                "fatal": sig.get("fatal"),
            },
        }
        try:
            report_path = reporter.write_report(job, payload, base_dir)
            payload["report_path"] = os.path.relpath(report_path, SKILL_ROOT).replace("\\", "/")
        except Exception as e:
            payload["report_error"] = f"{type(e).__name__}: {e}"

        if not args.dry_run:
            _save_last_result(args.id, payload)
            push_when = (job.get("notification") or {}).get("push_when", "always")
            wecom_cfg = ((job.get("notification") or {}).get("wecom") or {})
            should_push = push_when == "always" or bool(sig.get("selected"))
            if wecom_cfg.get("enabled") and should_push:
                try:
                    push_text = reporter.render_push(job, payload)
                    push_res = wecom.push_markdown(
                        wecom_cfg.get("webhook", ""), push_text,
                        mentioned_list=wecom_cfg.get("mentioned_list") or None,
                        mentioned_mobile_list=wecom_cfg.get("mentioned_mobile_list") or None,
                    )
                    payload["push_result"] = push_res
                except Exception as e:
                    payload["push_result"] = {"ok": False, "errcode": -99, "errmsg": f"{type(e).__name__}: {e}"}
        job_schema.rebuild_registry()
        _emit(payload)

    else:
        # ── signal_monitor 分支（原有逻辑）──────────────────────
        today = date.today()

        try:
            current_assets = asset_source.load_assets(job["asset_source"], base_dir)
        except Exception as e:
            _err(f"加载资产失败: {e}")

        status, assets, pool_diff = asset_source.reconcile(
            job["asset_source"]["type"], current_assets, state_dir,
        )
        if status == "auto_synced":
            s = pool_diff
            print(f"ℹ️ 资产池已同步：+{len(s.get('added') or [])} -{len(s.get('removed') or [])} ~{len(s.get('modified') or [])}",
                  file=sys.stderr, flush=True)

        sig = scanner.run_signal(job, assets)
        state = cooldown.load_state(state_dir)
        triggered, cooled = scanner.apply_cooldown(sig["by_ticker"], state, today, int(job.get("cooldown_days") or 7))

        all_stocks = list(sig["by_ticker"].values())

        if not args.dry_run:
            for r in triggered:
                state[r["ticker"]] = run_time
            cooldown.save_state(state_dir, state)

        summary = {
            "total_assets": len(assets),
            "triggered_count": len(triggered),
            "cooled_down_count": len(cooled),
            "anomalies_count": len(sig.get("anomalies") or []),
            "fatal": sig.get("fatal"),
        }

        pending_analysis = bool((job.get("analysis_hook") or {}).get("enabled")) and bool(triggered)

        payload = {
            "ok": sig.get("fatal") is None,
            "id": args.id,
            "run_time": run_time,
            "formulas": sig.get("formulas") or [],
            "dry_run": bool(args.dry_run),
            "task_type": task_type,
            "snapshot_status": status,
            "asset_pool_change": pool_diff,
            "triggered": triggered,
            "cooled_down": cooled,
            "all_stocks": all_stocks,
            "anomalies": sig.get("anomalies") or [],
            "summary": summary,
            "pending_analysis": pending_analysis,
            "analysis_hook": job.get("analysis_hook") if pending_analysis else None,
        }

        try:
            report_path = reporter.write_report(job, payload, base_dir)
            payload["report_path"] = os.path.relpath(report_path, SKILL_ROOT).replace("\\", "/")
        except Exception as e:
            payload["report_error"] = f"{type(e).__name__}: {e}"

        if not args.dry_run:
            _save_last_result(args.id, payload)
            report_mode = (job.get("report") or {}).get("report_mode", "overwrite")
            push_when = (job.get("notification") or {}).get("push_when", "triggered_only")
            wecom_cfg = ((job.get("notification") or {}).get("wecom") or {})
            should_push = push_when == "always" or triggered or (report_mode == "incremental" and cooled)
            if wecom_cfg.get("enabled") and should_push:
                try:
                    push_text = reporter.render_push(job, payload)
                    push_res = wecom.push_markdown(
                        wecom_cfg.get("webhook", ""), push_text,
                        mentioned_list=wecom_cfg.get("mentioned_list") or None,
                        mentioned_mobile_list=wecom_cfg.get("mentioned_mobile_list") or None,
                    )
                    payload["push_result"] = push_res
                except Exception as e:
                    payload["push_result"] = {"ok": False, "errcode": -99, "errmsg": f"{type(e).__name__}: {e}"}

        job_schema.rebuild_registry()
        _emit(payload)


def cmd_test_push(args):
    job = job_schema.load_job(args.id)
    last = _load_last_result(args.id)
    if last:
        text = "[TEST]\n" + reporter.render_push(job, last)
    else:
        text = f"[TEST] **{job.get('name') or job['id']}**\n_scheduled-task 测试推送 @ {datetime.now().isoformat(timespec='seconds')}_"
    wecom_cfg = ((job.get("notification") or {}).get("wecom") or {})
    res = wecom.push_markdown(
        wecom_cfg.get("webhook", ""), text,
        mentioned_list=wecom_cfg.get("mentioned_list") or None,
        mentioned_mobile_list=wecom_cfg.get("mentioned_mobile_list") or None,
    )
    _emit({"ok": res["ok"], "id": args.id, "push": res, "preview": text})


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
    cooldown_days = int(job.get("cooldown_days") or 7)

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
    sp.add_argument("--from", dest="from_", default=None, help="preset:<name> 或文件路径")
    sp.add_argument("--task-type", dest="task_type", default=None, help="signal_monitor 或 stock_picker")
    sp.add_argument("--scaffold", action="store_true", help="建空骨架（无公式）")
    sp.add_argument("--formulas-file", dest="formulas_file", default=None, help="从 JSON 文件读取公式")
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

    sp = sub.add_parser("run"); sp.add_argument("id")
    sp.add_argument("--dry-run", action="store_true"); sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("test-push"); sp.add_argument("id"); sp.set_defaults(func=cmd_test_push)

    sp = sub.add_parser("reset-cooldown"); sp.add_argument("id")
    sp.add_argument("--ticker"); sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_reset_cooldown)

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
