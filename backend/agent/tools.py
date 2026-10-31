# -*- coding: utf-8 -*-
"""
agent/tools.py — 银行操作工具注册表

将 BankCore 的业务方法暴露为 Agent 可调用的“工具”，每个工具含：
- fn: 实际执行函数
- desc: 人类可读描述
- scenario: 所属场景
Agent 的规划器会把用户意图分解为对这些工具的调用。
"""

from ..bank.core import get_core
from ..bank import world  # controlled digital-environment adapter

# 工具元数据
TOOL_META = {
    "query_balance":        {"scenario": "bill",    "desc": "查询账户余额"},
    "query_transactions":   {"scenario": "bill",    "desc": "查询近期交易流水"},
    "bill_analysis":        {"scenario": "bill",    "desc": "消费分类统计与趋势分析"},
    "detect_anomaly":       {"scenario": "bill",    "desc": "异常交易识别"},
    "monthly_report":       {"scenario": "bill",    "desc": "月度收支报告"},
    "transfer":             {"scenario": "transfer", "desc": "向他人转账（姓名/手机号/备注）"},
    "schedule_transfer":    {"scenario": "transfer", "desc": "设定时转账"},
    "get_scheduled_tasks":  {"scenario": "transfer", "desc": "查看待执行定时任务"},
    "wealth_list":          {"scenario": "wealth",  "desc": "查看理财产品"},
    "wealth_recommend":     {"scenario": "wealth",  "desc": "按风险偏好推荐并对比产品"},
    "risk_assess":          {"scenario": "wealth",  "desc": "风险评估问卷"},
    "subscribe":            {"scenario": "wealth",  "desc": "一键申购理财产品"},
    "redeem":               {"scenario": "wealth",  "desc": "赎回理财产品"},
    "get_holdings":         {"scenario": "wealth",  "desc": "查看我的持仓"},
    "card_list":            {"scenario": "card",    "desc": "查看我的卡片"},
    "card_apply":           {"scenario": "card",    "desc": "申请新卡"},
    "adjust_limit":         {"scenario": "card",    "desc": "调整卡片限额"},
    "toggle_lock":          {"scenario": "card",    "desc": "交易限制/解锁（冻结/解冻）"},
    "report_loss":          {"scenario": "card",    "desc": "卡片挂失"},
    "reissue":              {"scenario": "card",    "desc": "卡片补发"},
    "subscription_list":    {"scenario": "subscription", "desc": "查看订阅/代扣"},
    "cancel_subscription":  {"scenario": "subscription", "desc": "取消订阅/代扣"},
    "order_list":           {"scenario": "order",   "desc": "查看历史订单"},
    "query_order":          {"scenario": "order",   "desc": "查询订单进度"},
    "query_refund":         {"scenario": "order",   "desc": "查询退款进度"},
    "new_customer_guidance": {"scenario": "new_customer", "desc": "新客人生阶段引导与礼包"},
    "birthday_gift_flow":   {"scenario": "cross",   "desc": "跨场景联动：生日礼金自动转账"},
}


def _get_params(params, *keys):
    return {k: params.get(k) for k in keys}


def call_tool(operation, params=None):
    """执行单个工具。返回 (result_dict, scenario)。"""
    core = get_core()
    params = params or {}
    meta = TOOL_META.get(operation, {})
    scenario = meta.get("scenario", "unknown")

    handlers = {
        'reserve_budget': lambda: core.reserve_budget(**params),
        'place_gift_order': lambda: core.place_gift_order(**params),
        'finalize_birthday': lambda: core.finalize_birthday(**params),
        "query_balance": lambda: core.get_account(),
        "query_transactions": lambda: {"transactions": core.get_transactions()},
        "bill_analysis": lambda: core.bill_analysis(params.get("period", "month")),
        "detect_anomaly": lambda: core.detect_anomaly(),
        "monthly_report": lambda: core.monthly_report(),
        "transfer": lambda: core.transfer(
            params.get("to_key"), float(params.get("amount", 0)),
            params.get("currency", "CNY"), params.get("note", "")),
        "schedule_transfer": lambda: core.schedule_transfer(
            params.get("to_key"), float(params.get("amount", 0)),
            params.get("execute_ts", 0), params.get("currency", "CNY"), params.get("note", "")),
        "get_scheduled_tasks": lambda: {"tasks": core.get_scheduled_tasks()},
        "wealth_list": lambda: {"products": core.wealth_list()},
        "wealth_recommend": lambda: core.wealth_recommend(params.get("risk_pref", "中")),
        "risk_assess": lambda: core.risk_assess(params.get("answers")),
        "subscribe": lambda: core.subscribe(params.get("product_id"), float(params.get("amount", 0))),
        "redeem": lambda: core.redeem(params.get("product_id"), float(params.get("amount", 0))),
        "get_holdings": lambda: {"holdings": core.get_holdings()},
        "card_list": lambda: {"cards": core.card_list()},
        "card_apply": lambda: core.card_apply(params.get("card_type", "借记卡")),
        "adjust_limit": lambda: core.adjust_limit(params.get("card_id"), float(params.get("new_limit", 0))),
        "toggle_lock": lambda: core.toggle_lock(params.get("card_id"), params.get("lock", True)),
        "report_loss": lambda: core.report_loss(params.get("card_id")),
        "reissue": lambda: core.reissue(params.get("card_id")),
        "subscription_list": lambda: {"subscriptions": core.subscription_list()},
        "cancel_subscription": lambda: core.cancel_subscription(params.get("sub_id")),
        "order_list": lambda: {"orders": core.order_list()},
        "query_order": lambda: core.query_order(params.get("order_id")),
        "query_refund": lambda: core.query_refund(params.get("order_id")),
        "new_customer_guidance": lambda: core.new_customer_guidance(params.get("node")),
        "birthday_gift_flow": lambda: core.birthday_gift_flow(params.get("to_key"), float(params.get("amount", 200))),
    }

    fn = handlers.get(operation)
    if not fn:
        return {"ok": False, "error": f"未知工具：{operation}"}, scenario
    try:
        result = fn()
        return result, scenario
    except Exception as e:  # 工具内异常不应击穿 Agent
        return {"ok": False, "error": f"工具执行异常：{e}"}, scenario


def list_tools():
    return [{"operation": k, **v} for k, v in TOOL_META.items()]


# ---------------------------------------------------------------------------
# 函数调用（Function Calling）Schema —— 主流 Agent 工具调用范式
# 供 LLM（DeepSeek / OpenAI）以 tool_calls 方式选择银行工具并填参。
# ---------------------------------------------------------------------------
# 每个工具：properties 为 JSON Schema 参数；required 为必填项。
TOOL_PARAM_SCHEMAS = {
    "query_balance": (
        "查询当前账户余额", {}, []),
    "query_transactions": (
        "查询近期交易流水", {}, []),
    "bill_analysis": (
        "消费分类统计与趋势分析",
        {"period": {"type": "string", "description": "统计周期：month(本月) 或 year(本年)", "enum": ["month", "year"]}},
        []),
    "detect_anomaly": (
        "异常交易识别（深夜大额/高频/可疑商户）", {}, []),
    "monthly_report": (
        "生成月度收支报告（收入/支出/净额）", {}, []),
    "transfer": (
        "向他人转账。to_key 为收款人姓名/手机号/备注；amount 为金额（元）。",
        {
            "to_key": {"type": "string", "description": "收款人姓名、手机号或备注，如：李婷"},
            "amount": {"type": "number", "description": "转账金额，单位元，如 500"},
            "currency": {"type": "string", "description": "币种，默认 CNY", "enum": ["CNY", "USD", "HKD", "EUR"]},
            "note": {"type": "string", "description": "转账备注（可选）"},
        }, ["to_key", "amount"]),
    "schedule_transfer": (
        "设定时转账（在未来某个时间执行）。",
        {
            "to_key": {"type": "string", "description": "收款人姓名/手机号/备注"},
            "amount": {"type": "number", "description": "转账金额（元）"},
            "execute_ts": {"type": "number", "description": "计划执行时间（Unix 时间戳，秒）"},
            "currency": {"type": "string", "description": "币种，默认 CNY", "enum": ["CNY", "USD", "HKD", "EUR"]},
        }, ["to_key", "amount", "execute_ts"]),
    "get_scheduled_tasks": (
        "查看待执行的定时转账任务", {}, []),
    "wealth_list": (
        "查看全部理财产品", {}, []),
    "wealth_recommend": (
        "按风险偏好推荐并对比理财产品。",
        {"risk_pref": {"type": "string", "description": "风险偏好：低/中/高", "enum": ["低", "中", "高"]}},
        []),
    "risk_assess": (
        "风险评估问卷（给出风险承受等级）", {"answers":{"type":"object"}}, []),
    "subscribe": (
        "申购指定理财产品。",
        {
            "product_id": {"type": "string", "description": "理财产品 ID，如 WEALTH001"},
            "amount": {"type": "number", "description": "申购金额（元）"},
        }, ["product_id", "amount"]),
    "redeem": (
        "赎回理财产品。",
        {
            "product_id": {"type": "string", "description": "理财产品 ID"},
            "amount": {"type": "number", "description": "赎回金额（元）"},
        }, ["product_id", "amount"]),
    "get_holdings": (
        "查看我的理财持仓", {}, []),
    "card_list": (
        "查看我的银行卡列表", {}, []),
    "card_apply": (
        "申请新卡。",
        {"card_type": {"type": "string", "description": "卡片类型：信用卡/借记卡", "enum": ["信用卡", "借记卡"]}},
        []),
    "adjust_limit": (
        "调整卡片单日限额。",
        {
            "card_id": {"type": "string", "description": "卡片 ID"},
            "new_limit": {"type": "number", "description": "新的单日限额（元）"},
        }, ["card_id", "new_limit"]),
    "toggle_lock": (
        "冻结/解冻卡片交易。",
        {
            "card_id": {"type": "string", "description": "卡片 ID"},
            "lock": {"type": "boolean", "description": "true=冻结，false=解冻"},
        }, ["card_id", "lock"]),
    "report_loss": (
        "卡片挂失。",
        {"card_id": {"type": "string", "description": "卡片 ID"}}, ["card_id"]),
    "reissue": (
        "卡片补发。",
        {"card_id": {"type": "string", "description": "卡片 ID"}}, ["card_id"]),
    "subscription_list": (
        "查看订阅/代扣列表", {}, []),
    "cancel_subscription": (
        "取消订阅/代扣。",
        {"sub_id": {"type": "string", "description": "订阅 ID"}}, ["sub_id"]),
    "order_list": (
        "查看历史订单", {}, []),
    "query_order": (
        "查询订单进度。",
        {"order_id": {"type": "string", "description": "订单 ID"}}, ["order_id"]),
    "query_refund": (
        "查询退款进度。",
        {"order_id": {"type": "string", "description": "订单 ID"}}, ["order_id"]),
    "new_customer_guidance": (
        "新客人生阶段引导与礼包领取。",
        {"node": {"type": "string", "description": "人生阶段节点，如：毕业启航 / 职场新人"}}, []),
    "birthday_gift_flow": (
        "跨场景联动：识别生日需求后自动送出生日礼金（转账）。",
        {
            "to_key": {"type": "string", "description": "收礼人姓名/备注"},
            "amount": {"type": "number", "description": "礼金金额（元），默认 200"},
        }, ["to_key"]),
}


def tool_schemas():
    """返回 OpenAI 函数调用格式的 tools 列表（主流 Agent 工具调用）。"""
    out = []
    for op, (desc, props, required) in TOOL_PARAM_SCHEMAS.items():
        meta = TOOL_META.get(op, {})
        out.append({
            "type": "function",
            "function": {
                "name": op,
                "description": desc or meta.get("desc", ""),
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        })
    return out


for _op, _desc, _props, _required in [
    ('reserve_budget', '为生日鲜花蛋糕预留预算', {'to_key': {'type':'string'}, 'amount': {'type':'number'}, 'birthday_date': {'type':'string'}}, ['to_key','amount','birthday_date']),
    ('place_gift_order', '使用已授权预留创建模拟预约', {'reservation_id': {'type':'string'}, 'kind': {'type':'string', 'enum':['鲜花','蛋糕']}}, ['reservation_id','kind']),
    ('finalize_birthday', '合并预约并创建沙箱定时任务', {'reservation_id': {'type':'string'}}, ['reservation_id']),
]:
    TOOL_META[_op] = {'scenario':'cross', 'desc':_desc}
    TOOL_PARAM_SCHEMAS[_op] = (_desc, _props, _required)
