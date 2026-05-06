#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job.json 加载 / 保存 / 字段编辑（jsonpath-lite）/ 备份 / 回滚。"""

import copy
import glob
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS_DIR = os.path.join(SKILL_ROOT, "jobs")
REGISTRY_FILE = os.path.join(JOBS_DIR, "registry.json")
HISTORY_KEEP = 20

DEFAULT_JOB: Dict[str, Any] = {
    "id": "",
    "name": "",
    "enabled": True,
    "description": "",
    "asset_source": {"type": "inline", "assets": [], "disabled_tickers": []},
    "signal": {
        "category": {"direction": "buy", "label": "", "description": ""},
        "formulas": [],
        "trigger_formula": "信号",
        "display_fields": [],
        "lookback_days": 60,
    },
    "cooldown_days": 7,
    "analysis_hook": {
        "enabled": False,
        "framework_doc": "",
        "websearch_template": {},
        "output_sections": [],
    },
    "notification": {
        "wecom": {
            "enabled": True,
            "webhook": "",
            "mentioned_list": [],
            "mentioned_mobile_list": [],
        },
        "push_when": "triggered_only",
    },
    "schedule": {
        "enabled": True,
        "time": "08:30",
        "cron": None,
        "task_name": "",
    },
    "report": {
        "template_path": "../../templates/default_report.md",
        "output_dir": "output/reports",
        "report_mode": "overwrite",
    },
}


def _deep_merge(base: Dict, override: Dict) -> Dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def job_dir(job_id: str) -> str:
    return os.path.join(JOBS_DIR, job_id)


def job_file(job_id: str) -> str:
    return os.path.join(job_dir(job_id), "job.json")


def list_jobs() -> List[str]:
    if not os.path.isdir(JOBS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(JOBS_DIR)):
        if name.startswith("_") or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(JOBS_DIR, name, "job.json")):
            out.append(name)
    return out


def load_job(job_id: str) -> Dict:
    path = job_file(job_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"job 不存在: {job_id}（{path}）")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    merged = _deep_merge(DEFAULT_JOB, raw)
    if not merged.get("id"):
        merged["id"] = job_id
    if not merged.get("task_type"):
        merged["task_type"] = "signal_monitor"
    if not merged.get("schedule", {}).get("task_name"):
        merged["schedule"]["task_name"] = f"ScheduledTask_{job_id}"
    return merged


def save_job(job_id: str, data: Dict, *, backup: bool = True) -> Optional[str]:
    """保存 job.json。先备份当前版本，返回备份路径（如有）。"""
    path = job_file(job_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup_path = None
    if backup and os.path.exists(path):
        backup_path = _make_backup(job_id, path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return backup_path


def _make_backup(job_id: str, src: str) -> str:
    bdir = os.path.join(job_dir(job_id), ".history")
    os.makedirs(bdir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(bdir, f"job-{ts}.json")
    shutil.copy2(src, dst)
    # rotate
    files = sorted(glob.glob(os.path.join(bdir, "job-*.json")), reverse=True)
    for old in files[HISTORY_KEEP:]:
        try:
            os.remove(old)
        except Exception:
            pass
    return dst


def list_history(job_id: str) -> List[Dict]:
    bdir = os.path.join(job_dir(job_id), ".history")
    if not os.path.isdir(bdir):
        return []
    out = []
    for f in sorted(glob.glob(os.path.join(bdir, "job-*.json")), reverse=True):
        st = os.stat(f)
        out.append({
            "file": os.path.relpath(f, SKILL_ROOT).replace("\\", "/"),
            "timestamp": os.path.basename(f).replace("job-", "").replace(".json", ""),
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })
    return out


def rollback(job_id: str, to_timestamp: Optional[str] = None) -> Dict:
    history = list_history(job_id)
    if not history:
        raise RuntimeError(f"{job_id} 没有备份可回滚")
    if to_timestamp:
        match = [h for h in history if h["timestamp"] == to_timestamp]
        if not match:
            raise RuntimeError(f"找不到备份: {to_timestamp}")
        target = match[0]
    else:
        target = history[0]
    src = os.path.join(SKILL_ROOT, target["file"])
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    # save 当前为新备份后再覆写
    save_job(job_id, data, backup=True)
    return target


# ─────────────────────────────────────────
# jsonpath-lite：a.b.c / a.b[0] / a.b[name=x].c
# ─────────────────────────────────────────

_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[([^\]]+)\]")


def _parse_path(path: str) -> List[Tuple[str, str]]:
    """('key', name) | ('idx', '0') | ('match', 'name=x')"""
    tokens = []
    for m in _TOKEN_RE.finditer(path):
        if m.group(1) is not None:
            tokens.append(("key", m.group(1)))
        else:
            inner = m.group(2)
            if inner.lstrip("-").isdigit():
                tokens.append(("idx", inner))
            else:
                tokens.append(("match", inner))
    return tokens


def _resolve(node: Any, tokens: List[Tuple[str, str]]) -> Any:
    cur = node
    for kind, val in tokens:
        if kind == "key":
            if not isinstance(cur, dict):
                raise KeyError(f"路径不通：在非 dict 节点上找 key={val}")
            cur = cur.setdefault(val, {}) if False else cur[val]
        elif kind == "idx":
            cur = cur[int(val)]
        elif kind == "match":
            k, _, v = val.partition("=")
            if not isinstance(cur, list):
                raise KeyError(f"路径不通：[{val}] 要求 list")
            found = [x for x in cur if isinstance(x, dict) and str(x.get(k)) == v]
            if not found:
                raise KeyError(f"找不到 {k}={v}")
            cur = found[0]
    return cur


def get_by_path(data: Dict, path: str) -> Any:
    return _resolve(data, _parse_path(path))


def set_by_path(data: Dict, path: str, value: Any) -> Dict:
    """支持后缀操作符：
       path 末尾 '+' = 追加到 list；
       path 末尾 '-' = 从 list 删除（按值或按 ticker 字段）。
    """
    op = "set"
    if path.endswith("+"):
        op = "append"; path = path[:-1]
    elif path.endswith("-"):
        op = "remove"; path = path[:-1]

    tokens = _parse_path(path)
    if not tokens:
        raise ValueError("空 path")
    parent_tokens, last = tokens[:-1], tokens[-1]
    parent = _resolve(data, parent_tokens) if parent_tokens else data

    if op == "append":
        target = _resolve(data, tokens)
        if not isinstance(target, list):
            raise TypeError(f"{path} 不是 list，无法 append")
        target.append(value)
        return data
    if op == "remove":
        target = _resolve(data, tokens)
        if not isinstance(target, list):
            raise TypeError(f"{path} 不是 list，无法 remove")
        if isinstance(value, dict) and "ticker" in value:
            target[:] = [x for x in target if not (isinstance(x, dict) and x.get("ticker") == value["ticker"])]
        else:
            try:
                target.remove(value)
            except ValueError:
                pass
        return data

    if last[0] == "key":
        if not isinstance(parent, dict):
            raise TypeError(f"{path} 父节点非 dict")
        parent[last[1]] = value
    elif last[0] == "idx":
        parent[int(last[1])] = value
    elif last[0] == "match":
        # 末段是匹配的话不支持赋值，要再深入一层
        raise ValueError("不能在 [k=v] 末段直接赋值，请追加子字段")
    return data


# ─────────────────────────────────────────
# registry
# ─────────────────────────────────────────

def rebuild_registry() -> Dict:
    reg = {"jobs": []}
    for jid in list_jobs():
        try:
            j = load_job(jid)
        except Exception as e:
            reg["jobs"].append({"id": jid, "error": str(e)})
            continue
        last_result = _read_last_result(jid)
        reg["jobs"].append({
            "id": jid,
            "name": j.get("name", ""),
            "enabled": j.get("enabled", True),
            "schedule": j.get("schedule", {}),
            "asset_source_type": j.get("asset_source", {}).get("type"),
            "asset_count": _count_assets(j),
            "trigger_formula": j.get("signal", {}).get("trigger_formula"),
            "cooldown_days": j.get("cooldown_days"),
            "last_run": (last_result.get("run_time") or last_result.get("run_date")) if last_result else None,
            "last_triggered_count": (last_result.get("summary") or {}).get("triggered_count") if last_result else None,
        })
    os.makedirs(JOBS_DIR, exist_ok=True)
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    return reg


def _count_assets(j: Dict) -> int:
    src = j.get("asset_source", {})
    if src.get("type") == "inline":
        return len(src.get("assets") or [])
    snap = os.path.join(job_dir(j["id"]), "state", "assets_snapshot.json")
    if os.path.exists(snap):
        try:
            return len(json.load(open(snap, "r", encoding="utf-8")).get("assets") or [])
        except Exception:
            pass
    return -1


def _read_last_result(job_id: str) -> Optional[Dict]:
    p = os.path.join(job_dir(job_id), "state", "last_result.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, "r", encoding="utf-8"))
    except Exception:
        return None
