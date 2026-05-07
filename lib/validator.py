#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式语法校验：跑一次 runMultiFormula，看 errors[]，不写 state。"""

from datetime import date, timedelta
from typing import Dict, List

from . import quant_buddy

ASSETS_PLACEHOLDER = "{ASSETS}"


def _build_pool_args(assets: List[Dict]) -> str:
    return ", ".join(a["company"] for a in assets if a.get("company"))


def _materialize_formulas(formulas: List[Dict], assets: List[Dict]) -> List[str]:
    """把 job.signal.formulas 实例化成 quant-buddy 接受的字符串列表。"""
    pool_args = _build_pool_args(assets)
    out = []
    for f in formulas:
        name = (f.get("name") or "").strip()
        expr = f.get("expression", "")
        if ASSETS_PLACEHOLDER in expr:
            expr = expr.replace(ASSETS_PLACEHOLDER, pool_args)
        out.append(f"{name} = {expr}" if name else expr)
    return out


def validate(job: Dict, assets: List[Dict]) -> Dict:
    task_type = job.get("task_type", "signal_monitor")
    if task_type == "stock_picker":
        return _validate_stock_picker(job)
    return _validate_signal_monitor(job, assets)


def _validate_stock_picker(job: Dict) -> Dict:
    """stock_picker 只做 schema 校验，不调 quant-buddy。"""
    formulas = job.get("formulas") or []
    rh = job.get("result_handler") or {}
    errors = []
    if not formulas:
        errors.append("formulas 为空")
    if not rh.get("result_formula"):
        errors.append("result_handler.result_formula 未配置")
    if not rh.get("value_columns"):
        errors.append("result_handler.value_columns 未配置")
    if not rh.get("sort_by"):
        errors.append("result_handler.sort_by 未配置")
    if "limit" not in rh:
        errors.append("result_handler.limit 未配置")
    ok = len(errors) == 0
    return {"ok": ok, "reason": "OK" if ok else "; ".join(errors), "missing": [], "errors": errors}


def _validate_signal_monitor(job: Dict, assets: List[Dict]) -> Dict:
    sig = job.get("signal") or {}
    formulas_raw = sig.get("formulas") or []
    trigger_name = sig.get("trigger_formula") or "信号"
    display = sig.get("display_fields") or []

    if not formulas_raw:
        return {"ok": False, "reason": "signal.formulas 为空", "missing": [], "errors": []}
    if not assets:
        return {"ok": False, "reason": "资产池为空（无法替换 {ASSETS}）", "missing": [], "errors": []}

    api = quant_buddy.get_api()
    api.new_session()
    QuantAPI = quant_buddy.get_api_class()

    formulas = _materialize_formulas(formulas_raw, assets)
    today = date.today()
    begin = today - timedelta(days=5)
    try:
        resp = api.run_multi_formula(
            formulas=formulas,
            begin_date=int(begin.strftime("%Y%m%d")),
            include_description=False,
            use_minute_data=True,
        )
    except Exception as e:
        return {"ok": False, "reason": f"runMultiFormula 异常: {type(e).__name__}: {e}",
                "missing": [], "errors": []}

    errors = []
    if isinstance(resp, dict):
        for e in (resp.get("errors") or []):
            errors.append({"name": e.get("leftName"), "error": str(e.get("error", ""))[:300]})

    ids_map = QuantAPI.extract_obj_ids(resp)
    needed = [trigger_name] + list(display)
    missing = [k for k in dict.fromkeys(needed) if k not in ids_map]

    ok = not missing and not errors
    return {
        "ok": ok,
        "reason": "OK" if ok else "公式有错或缺名",
        "formulas_sent": formulas,
        "resolved_names": list(ids_map.keys()),
        "missing": missing,
        "errors": errors,
    }
