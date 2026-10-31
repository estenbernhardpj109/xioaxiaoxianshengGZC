# -*- coding: utf-8 -*-
"""
agent/planner.py — 任务规划器

将 NLU 的意图与实体，分解为可执行的“子任务步骤”（plan）。
每个步骤对应一个工具调用，并预先标注风险等级（用于前端展示与确认）。
支持复杂任务拆分（例如：跨场景联动 = 识别需求 → 锁定资金 → 执行转账）。
"""

import time

from . import security
from .tools import TOOL_META
from ..bank.core import get_core


def _fmt_money(amount, currency="CNY"):
    sym = {"CNY": "¥", "USD": "$", "HKD": "HK$", "EUR": "€"}.get(currency, "¥")
    return f"{sym}{amount:.2f}"


def _resolve_product(hint):
    """把产品名/关键词解析为 product_id。"""
    if not hint:
        return None
    core = get_core()
    for p in core.wealth_list():
        if p["product_id"] == hint or hint in p["name"]:
            return p["product_id"]
    return None


def build_plan(intent, entities):
    """
    intent: nlu 结果；entities: 已补全的实体。
    返回 (plan: list[step], summary: str)
    """
    scenario = intent.get("scenario")
    candidates = intent.get("candidates", [])
    plan = []
    sid = 0

    for op in candidates:
        sid += 1
        params = {}
        desc = TOOL_META.get(op, {}).get("desc", op)

        if op == "transfer" or op == "birthday_gift_flow":
            params["to_key"] = entities.get("to_key")
            params["amount"] = entities.get("amount")
            params["currency"] = entities.get("currency", "CNY")
            params["note"] = entities.get("note", "")
            if op == "birthday_gift_flow":
                desc = f"跨场景联动：识别生日需求 → 锁定资金 → 自动向「{params['to_key']}」送上{_fmt_money(params['amount'], params['currency'])}礼金"
            else:
                desc = f"向「{params.get('to_key')}」转账 {_fmt_money(params['amount'], params['currency'])}"
        elif op == "schedule_transfer":
            params["to_key"] = entities.get("to_key")
            params["amount"] = entities.get("amount")
            params["currency"] = entities.get("currency", "CNY")
            params["execute_ts"] = entities.get("execute_ts", time.time() + 86400)
            desc = f"设定时转账：向「{params['to_key']}」于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(params['execute_ts']))} 转账 {_fmt_money(params['amount'], params['currency'])}"
        elif op == "bill_analysis":
            params["period"] = entities.get("period", "month")
            desc = "消费分类统计与趋势分析"
        elif op == "monthly_report":
            desc = "生成月度收支报告"
        elif op == "detect_anomaly":
            desc = "扫描异常交易"
        elif op == "wealth_recommend":
            params["risk_pref"] = entities.get("risk_pref", "中")
            desc = f"按「{params['risk_pref']}」风险偏好推荐并对比理财产品"
        elif op == "risk_assess":
            if entities.get("answers"): params["answers"]=entities["answers"]
            desc = "风险评估问卷评分"
        elif op == "subscribe":
            pid = _resolve_product(entities.get("product_hint"))
            params["product_id"] = pid
            params["amount"] = entities.get("amount")
            desc = f"申购理财产品（{pid or entities.get('product_hint')}）{_fmt_money(params['amount'] or 0)}"
        elif op == "redeem":
            pid = _resolve_product(entities.get("product_hint"))
            params["product_id"] = pid
            params["amount"] = entities.get("amount") or 999999  # 默认全额
            desc = f"赎回理财产品（{pid or entities.get('product_hint')}）"
        elif op == "card_apply":
            params["card_type"] = entities.get("card_type", "借记卡")
            desc = f"申请{params['card_type']}"
        elif op == "adjust_limit":
            params["card_id"] = entities.get("card_id")
            params["new_limit"] = entities.get("amount") or entities.get("new_limit")
            desc = f"调整卡片限额为 {_fmt_money(params['new_limit'] or 0)}"
        elif op == "toggle_lock":
            params["card_id"] = entities.get("card_id")
            params["lock"] = not entities.get("unlock", False)
            desc = "冻结卡片交易（设置交易限制）" if params["lock"] else "解冻卡片交易"
        elif op == "report_loss":
            params["card_id"] = entities.get("card_id")
            desc = "卡片挂失"
        elif op == "reissue":
            params["card_id"] = entities.get("card_id")
            desc = "卡片补发"
        elif op == "cancel_subscription":
            params["sub_id"] = entities.get("sub_id")
            desc = "取消订阅/代扣"
        elif op == "new_customer_guidance":
            params["node"] = entities.get("node") or entities.get("node_hint")
            desc = "新客人生阶段引导与礼包领取"
        elif op in ("query_order", "query_refund"):
            params["order_id"] = entities.get("order_id")
            desc = "查询订单/退款进度"

        level, strategy, reason = security.assess_risk(op, params)
        plan.append({
            "step_id": f"s{sid}",
            "operation": op,
            "params": params,
            "description": desc,
            "risk": level,
            "strategy": strategy,
            "risk_reason": reason,
            "status": "pending",   # pending / executed / confirmed / failed
            "result": None,
        })

    summary = _make_summary(scenario, plan)
    return plan, summary


def _make_summary(scenario, plan):
    names = {
        "transfer": "智能转账", "bill": "账单分析", "wealth": "理财操作",
        "card": "卡片管理", "subscription": "订阅/代扣管理", "order": "订单代理查询",
        "new_customer": "新客服务引导", "cross": "跨场景联动", "unknown": "通用查询",
    }
    title = names.get(scenario, "银行服务")
    steps_txt = "；".join(p["description"] for p in plan)
    return f"【{title}】我为您规划了 {len(plan)} 个步骤：{steps_txt}。"
