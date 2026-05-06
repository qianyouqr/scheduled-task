#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企微群机器人 markdown 推送（stdlib only）。"""

import json
import urllib.error
import urllib.request
from typing import Dict, List, Optional

_MAX_BYTES = 4096


def _truncate_utf8(text: str, limit: int = _MAX_BYTES) -> str:
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text
    suffix = "\n\n…(已截断)".encode("utf-8")
    keep = max(0, limit - len(suffix))
    truncated = raw[:keep]
    while truncated:
        try:
            return truncated.decode("utf-8") + "\n\n…(已截断)"
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def push_markdown(
    webhook: str,
    content: str,
    mentioned_list: Optional[List[str]] = None,
    mentioned_mobile_list: Optional[List[str]] = None,
    timeout: int = 10,
) -> Dict:
    if not webhook or "YOUR_KEY_HERE" in webhook:
        return {"ok": False, "errcode": -1, "errmsg": "webhook 未配置"}
    md: Dict = {"content": _truncate_utf8(content)}
    if mentioned_list:
        md["mentioned_list"] = mentioned_list
    if mentioned_mobile_list:
        md["mentioned_mobile_list"] = mentioned_mobile_list
    body = json.dumps({"msgtype": "markdown", "markdown": md}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return {"ok": False, "errcode": -2, "errmsg": f"网络异常: {e}"}
    except Exception as e:
        return {"ok": False, "errcode": -3, "errmsg": f"未知错误: {e}"}
    try:
        data = json.loads(raw)
    except Exception:
        return {"ok": False, "errcode": -4, "errmsg": f"非 JSON: {raw[:200]}"}
    return {"ok": int(data.get("errcode", -1)) == 0,
            "errcode": int(data.get("errcode", -1)),
            "errmsg": data.get("errmsg", "")}
