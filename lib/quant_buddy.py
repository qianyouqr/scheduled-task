#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动态发现 quant-buddy-skill 并加载 QuantAPI。"""

import os
import sys

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_FILE = os.path.join(SKILL_ROOT, ".cache", ".core_root")


def find_quant_buddy() -> str:
    if os.path.exists(_CACHE_FILE):
        try:
            cached = open(_CACHE_FILE, "r", encoding="utf-8").read().strip()
            if cached and os.path.isfile(os.path.join(cached, "scripts", "quant_api.py")):
                return cached
        except Exception:
            pass
    candidates = [
        os.path.join(os.path.dirname(SKILL_ROOT), "quant-buddy-skill"),
        os.path.expanduser("~/.claude/skills/quant-buddy-skill"),
        os.path.expanduser("~/.openclaw/skills/quant-buddy-skill"),
        os.path.expanduser("~/.codex/skills/quant-buddy-skill"),
    ]
    for plat in (".claude", ".openclaw", ".codex", ".github"):
        candidates.append(os.path.join(os.getcwd(), plat, "skills", "quant-buddy-skill"))
    for p in candidates:
        if os.path.isfile(os.path.join(p, "scripts", "quant_api.py")):
            try:
                os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
                with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                    f.write(p)
            except Exception:
                pass
            return p
    raise RuntimeError(
        "找不到 quant-buddy-skill。请先安装，或在 .cache/.core_root 写入其绝对路径。\n"
        "已搜索：\n" + "\n".join(f"  {p}" for p in candidates)
    )


_api = None


def get_api():
    global _api
    if _api is not None:
        return _api
    qb_dir = find_quant_buddy()
    sp = os.path.join(qb_dir, "scripts")
    if sp not in sys.path:
        sys.path.insert(0, sp)
    from quant_api import QuantAPI  # type: ignore
    _api = QuantAPI(skill_root=qb_dir)
    return _api


def get_api_class():
    qb_dir = find_quant_buddy()
    sp = os.path.join(qb_dir, "scripts")
    if sp not in sys.path:
        sys.path.insert(0, sp)
    from quant_api import QuantAPI  # type: ignore
    return QuantAPI
