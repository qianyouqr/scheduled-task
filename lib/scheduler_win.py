#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows schtasks 包装 + cron→schtasks 简易转换。

支持的 cron 子集（5 字段：分 时 日 月 周）：
- 每 20 分钟 `*/20 * * * *`       → /SC MINUTE /MO 20
- 每天   `M H * * *`              → /SC DAILY /ST HH:MM
- 工作日 `M H * * 1-5`            → /SC WEEKLY /D MON,TUE,WED,THU,FRI
- 每周几 `M H * * 1`              → /SC WEEKLY /D MON
- 每月几 `M H D * *` (D 是数字)   → /SC MONTHLY /D D
- 不支持的会 raise，让上层报清楚错给用户
"""

import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DOW = {"0": "SUN", "1": "MON", "2": "TUE", "3": "WED", "4": "THU", "5": "FRI", "6": "SAT", "7": "SUN"}


def cron_to_schtasks(cron: str) -> List[str]:
    """返回 ['/SC','DAILY','/ST','08:30'] 这样的参数片段。"""
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError(f"cron 必须 5 字段: '{cron}'")
    minute, hour, dom, mon, dow = parts
    if mon != "*":
        raise ValueError("不支持月份字段")

    minute_step = re.fullmatch(r"\*/(\d+)", minute)
    if minute_step:
        interval = int(minute_step.group(1))
        if interval <= 0:
            raise ValueError("分钟步长必须大于 0")
        # hour/dow 范围约束由 cli.py 的 _is_in_cron_window() 在运行时守卫
        return ["/SC", "MINUTE", "/MO", str(interval)]

    if not minute.isdigit() or not hour.isdigit():
        raise ValueError("分 / 时必须为数字")
    st = f"{int(hour):02d}:{int(minute):02d}"

    if dom == "*" and dow == "*":
        return ["/SC", "DAILY", "/ST", st]
    if dom != "*" and dow == "*":
        if not dom.isdigit():
            raise ValueError(f"不支持日字段: {dom}")
        return ["/SC", "MONTHLY", "/D", dom, "/ST", st]
    if dom == "*" and dow != "*":
        days = _parse_dow(dow)
        return ["/SC", "WEEKLY", "/D", ",".join(days), "/ST", st]
    raise ValueError("日 与 周 不能同时指定")


def _parse_dow(spec: str) -> List[str]:
    """1-5 → MON,TUE,WED,THU,FRI ; 1,3 → MON,WED ; 1 → MON。"""
    out = []
    for token in spec.split(","):
        if "-" in token:
            a, b = token.split("-", 1)
            for i in range(int(a), int(b) + 1):
                out.append(_DOW[str(i)])
        else:
            out.append(_DOW[token])
    return out


def _runner_path(job_id: str) -> str:
    """每个 job 跑一个 .bat —— 调 cli.py run <id>。"""
    runner_dir = os.path.join(SKILL_ROOT, "jobs", job_id)
    os.makedirs(runner_dir, exist_ok=True)
    runner = os.path.join(runner_dir, "_run.bat")
    cli = os.path.join(SKILL_ROOT, "scripts", "cli.py").replace("/", "\\")
    log_dir = os.path.join(runner_dir, "state", "logs").replace("/", "\\")
    content = (
        "@echo off\r\n"
        f'set "JOB_DIR={runner_dir}"\r\n'
        f'if not exist "{log_dir}" mkdir "{log_dir}"\r\n'
        f'for /f "tokens=2 delims==" %%I in (\'wmic os get localdatetime /value 2^>nul ^| find "="\') do set DT=%%I\r\n'
        f'set "DAY=%DT:~0,8%"\r\n'
        f'python "{cli}" run {job_id} 1>>"{log_dir}\\%DAY%.log" 2>>&1\r\n'
    )
    with open(runner, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return runner


def _schtasks(args: List[str]) -> Tuple[int, str, str]:
    """直接调 schtasks，绕过 PATH 问题。"""
    exe = shutil.which("schtasks") or r"C:\Windows\System32\schtasks.exe"
    p = subprocess.run([exe] + args, capture_output=True, text=True, encoding="gbk", errors="replace")
    return p.returncode, p.stdout or "", p.stderr or ""


def apply_schedule(job: Dict) -> Dict:
    sched = job.get("schedule") or {}
    if not sched.get("enabled", True):
        return delete(job)
    task_name = sched.get("task_name") or f"ScheduledTask_{job['id']}"
    # 迁移旧 SignalMonitor_ 前缀的任务
    old_name = f"SignalMonitor_{job['id']}"
    rc_q, _, _ = _schtasks(["/Query", "/TN", old_name])
    if rc_q == 0:
        _schtasks(["/Delete", "/TN", old_name, "/F"])
    runner = _runner_path(job["id"])

    if sched.get("cron"):
        sc_args = cron_to_schtasks(sched["cron"])
    else:
        t = sched.get("time") or "08:30"
        if not re.match(r"^\d{1,2}:\d{2}$", t):
            raise ValueError(f"schedule.time 格式错: {t}")
        h, m = t.split(":")
        sc_args = ["/SC", "DAILY", "/ST", f"{int(h):02d}:{int(m):02d}"]

    args = ["/Create", "/TN", task_name, "/TR", f'"{runner}"', "/F"] + sc_args
    rc, out, err = _schtasks(args)
    return {"ok": rc == 0, "task_name": task_name, "args": sc_args,
            "stdout": out.strip(), "stderr": err.strip()}


def pause(job: Dict) -> Dict:
    name = (job.get("schedule") or {}).get("task_name") or f"ScheduledTask_{job['id']}"
    rc, out, err = _schtasks(["/Change", "/TN", name, "/DISABLE"])
    return {"ok": rc == 0, "task_name": name, "stdout": out.strip(), "stderr": err.strip()}


def resume(job: Dict) -> Dict:
    name = (job.get("schedule") or {}).get("task_name") or f"ScheduledTask_{job['id']}"
    rc, out, err = _schtasks(["/Change", "/TN", name, "/ENABLE"])
    return {"ok": rc == 0, "task_name": name, "stdout": out.strip(), "stderr": err.strip()}


def delete(job: Dict) -> Dict:
    name = (job.get("schedule") or {}).get("task_name") or f"ScheduledTask_{job['id']}"
    rc, out, err = _schtasks(["/Delete", "/TN", name, "/F"])
    return {"ok": rc == 0, "task_name": name, "stdout": out.strip(), "stderr": err.strip()}


def query(job: Dict) -> Dict:
    name = (job.get("schedule") or {}).get("task_name") or f"ScheduledTask_{job['id']}"
    rc, out, err = _schtasks(["/Query", "/TN", name, "/V", "/FO", "LIST"])
    if rc != 0:
        return {"ok": False, "task_name": name, "exists": False, "stderr": err.strip()}
    info: Dict[str, Optional[str]] = {"Status": None, "Last Run Time": None, "Next Run Time": None,
                                       "Scheduled Task State": None, "上次运行时间": None, "下次运行时间": None,
                                       "状态": None, "计划任务状态": None}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip(); v = v.strip()
            if k in info:
                info[k] = v
    return {"ok": True, "task_name": name, "exists": True, "info": {k: v for k, v in info.items() if v}}
