#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""triggered.json 冷静期管理。"""

import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, Optional


def state_file(state_dir: str) -> str:
    return os.path.join(state_dir, "triggered.json")


def load_state(state_dir: str) -> Dict[str, str]:
    p = state_file(state_dir)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, "r", encoding="utf-8")) or {}
    except Exception:
        return {}


def save_state(state_dir: str, state: Dict[str, str]):
    os.makedirs(state_dir, exist_ok=True)
    with open(state_file(state_dir), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _parse_date(iso: str) -> date:
    """Parse YYYY-MM-DD or YYYY-MM-DD HH:MM:SS into a date, raise ValueError on failure."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        return datetime.strptime(iso, "%Y-%m-%d").date()


def is_cooled_down(last_iso: str, today: date, cooldown_days: int) -> bool:
    try:
        d = _parse_date(last_iso)
    except Exception:
        return False
    return (today - d).days < cooldown_days


def cooldown_end(last_iso: str, cooldown_days: int) -> Optional[str]:
    try:
        d = _parse_date(last_iso)
    except Exception:
        return None
    return (d + timedelta(days=cooldown_days)).strftime("%Y-%m-%d")


def reset(state_dir: str, ticker: Optional[str] = None) -> int:
    state = load_state(state_dir)
    if ticker is None:
        n = len(state)
        save_state(state_dir, {})
        return n
    if ticker in state:
        del state[ticker]
        save_state(state_dir, state)
        return 1
    return 0
