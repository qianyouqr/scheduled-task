#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资产源加载：excel / csv / inline 三选一。统一返回 [{ticker, company}]。"""

import csv
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def _norm(items: List[Dict]) -> List[Dict]:
    out, seen = [], set()
    for it in items:
        t = (it.get("ticker") or "").strip()
        c = (it.get("company") or "").strip()
        if not t or not c or t in seen:
            continue
        if t in ("代码", "股票代码") or c in ("股票名称", "名称"):
            continue
        seen.add(t)
        out.append({"ticker": t, "company": c})
    return out


def load_assets(source: Dict, base_dir: str) -> List[Dict]:
    """source 来自 job.asset_source；base_dir 是 job 目录。"""
    typ = (source or {}).get("type", "inline")
    disabled = set((source or {}).get("disabled_tickers") or [])

    if typ == "inline":
        assets = _norm(source.get("assets") or [])
    elif typ == "excel":
        assets = _load_excel(_resolve_path(source.get("path"), base_dir))
    elif typ == "csv":
        assets = _load_csv(_resolve_path(source.get("path"), base_dir))
    else:
        raise ValueError(f"未知 asset_source.type: {typ}")

    return [a for a in assets if a["ticker"] not in disabled]


def _resolve_path(path: Optional[str], base_dir: str) -> str:
    if not path:
        raise ValueError("asset_source.path 为空")
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def _load_excel(path: str) -> List[Dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到 Excel: {path}")
    import pandas as pd  # type: ignore
    df = pd.read_excel(path, header=None)
    items = []
    for _, row in df.iterrows():
        cells = [str(c).strip() for c in row if pd.notna(c)]
        if len(cells) < 2:
            continue
        items.append({"company": cells[-2], "ticker": cells[-1]})
    return _norm(items)


def _load_csv(path: str) -> List[Dict]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到 CSV: {path}")
    items = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            cells = [c.strip() for c in row if c and c.strip()]
            if len(cells) < 2:
                continue
            items.append({"company": cells[-2], "ticker": cells[-1]})
    return _norm(items)


# ─────────────────────────────────────────
# 快照（excel/csv 模式才用）
# ─────────────────────────────────────────

def snapshot_path(state_dir: str) -> str:
    return os.path.join(state_dir, "assets_snapshot.json")


def load_snapshot(state_dir: str) -> Optional[List[Dict]]:
    p = snapshot_path(state_dir)
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, "r", encoding="utf-8"))
        return _norm(d.get("assets") or [])
    except Exception:
        return None


def save_snapshot(state_dir: str, assets: List[Dict]):
    os.makedirs(state_dir, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(assets),
        "assets": assets,
    }
    with open(snapshot_path(state_dir), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def diff(old: List[Dict], new: List[Dict]) -> Dict:
    om = {a["ticker"]: a for a in (old or [])}
    nm = {a["ticker"]: a for a in (new or [])}
    added = [nm[t] for t in nm if t not in om]
    removed = [om[t] for t in om if t not in nm]
    modified = []
    for t, na in nm.items():
        if t in om and na["company"] != om[t]["company"]:
            modified.append({"ticker": t, "old_company": om[t]["company"], "new_company": na["company"]})
    return {"added": added, "removed": removed, "modified": modified}


def is_empty_diff(d: Dict) -> bool:
    return not (d.get("added") or d.get("removed") or d.get("modified"))


def reconcile(source_type: str, current_assets: List[Dict], state_dir: str) -> Tuple[str, List[Dict], Optional[Dict]]:
    """返回 (status, assets_to_use, diff_or_None)
    status: first_run | ok | auto_synced | inline
    """
    if source_type == "inline":
        return "inline", current_assets, None
    snap = load_snapshot(state_dir)
    if snap is None:
        save_snapshot(state_dir, current_assets)
        return "first_run", current_assets, None
    d = diff(snap, current_assets)
    if is_empty_diff(d):
        return "ok", snap, None
    save_snapshot(state_dir, current_assets)
    return "auto_synced", current_assets, d
