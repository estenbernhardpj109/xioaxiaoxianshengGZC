# -*- coding: utf-8 -*-
"""
agent/nlu.py — 自然语言理解（意图识别 + 实体抽取）

采用“规则 + 模式”的确定性引擎，保证在**无外部 LLM**时也可稳定工作；
若配置了 LLM（见 llm.py），可由 LLM 增强抽取，但本引擎始终作为兜底与校验。
"""

import re
import time

# ---------------------------------------------------------------------------
# 实体抽取
# ---------------------------------------------------------------------------

_CNY_SYMBOL = "元|块|块钱|圆"
_MULT = {"万": 10000, "万块": 10000, "千": 1000, "千块": 1000, "百": 100, "百块": 100}


_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse_amount(text):
    """抽取金额与币种。支持 阿拉伯数字 / 中文数字(两千、五百、二十) + 万/千/百/元/块。
    优先匹配“带单位”的数字（避免把“90天”误判为金额），否则取最后一个数字。"""
    currency = "CNY"
    if re.search(r"\$|美元|美金", text):
        currency = "USD"
    elif re.search(r"港币|港元|HKD", text):
        currency = "HKD"
    elif re.search(r"欧元|EUR", text):
        currency = "EUR"

    # Currency-bearing numbers first. Dates, phone numbers and product terms are not amounts.
    m = re.search(r'(-?\d+(?:\.\d+)?)\s*(万|千|百|元|块钱|块|圆|美元|美金|港币|欧元)', text)
    if m:
        return round(float(m.group(1)) * _MULT.get(m.group(2),1),2), currency
    m = re.search(r'([零一二三四五六七八九两十百千万]+)(?:元|块钱|块|圆)',text)
    if m:
        total,section,number=0,0,0
        for ch in m.group(1):
            if ch in '十百千万':
                unit={'十':10,'百':100,'千':1000,'万':10000}[ch]
                if unit==10000:
                    section=(section+number)*unit; total+=section;section=0;number=0
                else:
                    section+=(number or 1)*unit;number=0
            else:
                number=_CN_NUM[ch]
        return float(total+section+number),currency
    # Bare numbers are accepted only as a complete clarification turn.
    if re.fullmatch(r'\s*\d+(?:\.\d+)?\s*',text):
        return float(text),currency
    return None,currency


def parse_payee(text):
    """抽取收款人：支持 转给/给X转/转账给/手机号/备注。"""
    # 手机号
    m = re.search(r"1[3-9]\d{9}", text)
    if m:
        return m.group(0)
    # 转给/汇给/付款给/打给 + 名称
    m = re.search(r"(?:转给|汇给|付款给|打给|转账给|转给)\s*([\u4e00-\u9fa5A-Za-z·]{1,6})", text)
    if m:
        return m.group(1).strip("转汇付打给")
    # 给 X 转/汇/付
    m = re.search(r"给\s*([\u4e00-\u9fa5A-Za-z·]{1,6})\s*(?:转|汇|付|打)", text)
    if m:
        return m.group(1).strip()
    # 转 X（X 为中文名，且后面紧跟金额或结尾）
    m = re.search(r"转\s*([\u4e00-\u9fa5]{2,4})(?:\s*\d|$)", text)
    if m:
        return m.group(1)
    # 送 X（生日/送礼场景）
    m = re.search(r"送\s*([\u4e00-\u9fa5]{2,4})", text)
    if m:
        return m.group(1)
    return None


def parse_date(text):
    """解析定时转账时间，返回 epoch 或 None。"""
    now = time.time()
    if "大后天" in text:
        base = now + 3 * 86400
    elif "明天" in text:
        base = now + 86400
    elif "后天" in text:
        base = now + 2 * 86400
    elif "大后天" in text:
        base = now + 3 * 86400
    elif "下周一" in text or "下周" in text:
        base = now + 7 * 86400
    else:
        base = now + 86400  # 默认明天

    # 指定“X点”
    hm = re.search(r"(\d{1,2})\s*点", text)
    hour = int(hm.group(1)) if hm else 9
    struct = time.localtime(base)
    target = time.mktime((struct.tm_year, struct.tm_mon, struct.tm_mday, hour, 0, 0, 0, 0, -1))
    return target


def parse_risk_pref(text):
    if re.search(r"保守|稳健|低风险|不想亏|保本", text):
        return "低"
    if re.search(r"激进|高风险|高收益|搏一搏|能承受", text):
        return "高"
    if re.search(r"中等|平衡|适中|稳健偏", text):
        return "中"
    return "中"


def parse_product(text):
    m = re.search(r"(W00\d)", text)
    if m:
        return m.group(1)
    for name in ["稳盈", "增利", "权益", "货币基金"]:
        if name in text:
            return name
    return None


# ---------------------------------------------------------------------------
# 场景识别
# ---------------------------------------------------------------------------

SCENARIO_KEYWORDS = {
    "transfer": ["转账", "转给", "汇款", "汇给", "付款", "打钱", "转帐", "跨境", "外汇", "换汇", "美金", "美元", "港币", "欧元"],
    "bill": ["账单", "消费", "花了多少", "分析", "异常", "月度", "年度", "报告", "预算", "流水", "支出", "开销", "花销", "余额", "还剩", "有多少钱", "查钱"],
    "wealth": ["理财", "基金", "推荐", "买入", "申购", "赎回", "风险", "收益", "产品", "稳健", "投资", "持仓"],
    "card": ["卡", "额度", "挂失", "冻结", "解锁", "补卡", "申请卡", "信用卡", "借记卡", "限额", "解限"],
    "subscription": ["订阅", "代扣", "会员", "自动扣", "续费", "取消订阅", "扣费"],
    "order": ["订单", "快递", "物流", "退款", "买的东西", "买的商品", "发货", "到货"],
    "new_customer": ["新客", "活动", "礼包", "人生阶段", "千元", "迎新", "开户礼", "领取", "节点"],
    "cross": ["生日", "送礼", "过节", "祝福", "纪念日", "结婚", "随礼", "礼金"],
}


def recognize_scenario(text):
    scores = {}
    for sc, kws in SCENARIO_KEYWORDS.items():
        s = sum(1 for k in kws if k in text)
        if s:
            scores[sc] = s
    # 转账场景精识别：避免“这个月转了多少”被误判；要求“转+给/转+数字/汇款”等强信号
    if _is_transfer(text):
        scores["transfer"] = scores.get("transfer", 0) + 3
    if not scores:
        return "unknown", 0.0
    best = max(scores, key=scores.get)
    return best, min(1.0, 0.5 + 0.15 * scores[best])


def _is_transfer(text):
    """转账强信号判定。"""
    if any(k in text for k in ["转账", "转帐", "转给", "汇款", "汇给", "付款", "打钱", "跨境", "换汇", "外汇"]):
        return True
    if "转" in text and ("给" in text or re.search(r"转\s*\d", text) or "汇" in text):
        return True
    return False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def understand(text, history=None):
    """
    返回意图理解结果：
    {
      scenario, intent(候选主操作), entities, candidates,
      need_clarify, clarify, confidence
    }
    """
    text = (text or "").strip()
    scenario, conf = recognize_scenario(text)
    entities = {}
    if '年' in text or '年度' in text:
        entities['period']='year'
    oid=re.search(r'\bO\d+\b',text)
    if oid: entities['order_id']=oid.group(0)
    cid=re.search(r'\bC[0-9A-Za-z]+\b',text)
    if cid: entities['card_id']=cid.group(0)
    note=re.search(r'备注[为是：: ]*(.+?)(?:，|$)',text)
    if note: entities['note']=note.group(1)
    answers=re.findall(r'(?<![A-Za-z])[ABC](?![A-Za-z])',text)
    if len(answers)==3: entities['answers']={str(i+1):v for i,v in enumerate(answers)}

    amount, currency = parse_amount(text)
    if amount is not None:
        entities["amount"] = amount
        entities["currency"] = currency
    payee = parse_payee(text)
    if payee:
        entities["to_key"] = payee
    if "定时" in text or "每天" in text or "每月" in text or "明天" in text or "后天" in text or "下周" in text:
        entities["schedule"] = True
        entities["execute_ts"] = parse_date(text)
    entities["risk_pref"] = parse_risk_pref(text)
    prod = parse_product(text)
    if prod:
        entities["product_hint"] = prod

    # 卡片类型与解冻意图
    if "信用卡" in text:
        entities["card_type"] = "信用卡"
    elif "借记卡" in text:
        entities["card_type"] = "借记卡"
    if any(k in text for k in ["解冻", "解锁", "解限", "解除冻结", "取消冻结"]):
        entities["unlock"] = True
    # 订阅商户（取消时结合银行数据模糊匹配）
    entities["merchant_hint"] = None
    # 新客礼包节点
    mnode = re.search(r"领取?\s*([\u4e00-\u9fa5]{2,6})", text)
    if mnode:
        entities["node_hint"] = mnode.group(1)

    # 候选操作（供 planner 选择）
    candidates = _candidates(scenario, text, entities)

    need_clarify = False
    clarify = None
    if scenario == "transfer":
        if "to_key" not in entities and not _has_card_or_self(text):
            need_clarify = True
            clarify = "请问要转给谁？（可说姓名、手机号或备注）"
        elif "amount" not in entities:
            need_clarify = True
            clarify = "请问转账金额是多少？"
    elif scenario == "wealth" and any(k in text for k in ["申购", "买入", "买", "赎回"]) and ("product_hint" not in entities or ("amount" not in entities and "赎回" not in text)):
        need_clarify = True
        clarify = "请问要操作的理财产品和金额？（例如：申购稳盈90天 10000 元）"
    elif scenario == "card" and any(k in text for k in ["调额", "限额", "调整额度"]) and "card_id" not in entities and "card_type" not in entities:
        need_clarify = True
        clarify = "请问要调整哪张卡？（可说卡号后四位或卡片类型）"
    elif scenario == "order" and ("订单" in text or "退款" in text) and "order_id" not in entities and "查询" in text:
        # 引导列出订单
        candidates = ["order_list"]

    return {
        "scenario": scenario,
        "intent": candidates[0] if candidates else "unknown",
        "entities": entities,
        "candidates": candidates,
        "need_clarify": need_clarify,
        "clarify": clarify,
        "confidence": conf,
    }


def _has_card_or_self(text):
    return "卡" in text


def _candidates(scenario, text, entities):
    """根据场景与关键词，确定要调用的操作集合。"""
    if scenario == "transfer":
        if entities.get("schedule"):
            return ["schedule_transfer"]
        if "生日" in text or "送礼" in text or "祝福" in text:
            return ["birthday_gift_flow"] if "amount" in entities or "to_key" in entities else ["transfer"]
        return ["transfer"]
    if scenario == "bill":
        cands = []
        if '年度' in text or '本年' in text or '今年' in text:
            return ['bill_analysis']
        if any(k in text for k in ["余额", "还剩", "有多少钱", "查钱"]):
            cands.append("query_balance")
        if any(k in text for k in ["异常", "风险", "可疑", "奇怪"]):
            cands.append("detect_anomaly")
        if any(k in text for k in ["月度", "月报", "每月", "这个月", "本月"]):
            cands.append("monthly_report")
        if any(k in text for k in ["分析", "分类", "统计", "消费", "花", "支出", "开销"]):
            cands.append("bill_analysis")
        if not cands:
            cands = ["bill_analysis"] if "账单" in text else ["query_balance"]
        return cands
    if scenario == "wealth":
        cands = []
        if "风险" in text or "评估" in text:
            cands.append("risk_assess")
        if any(k in text for k in ["推荐", "对比", "买什么", "配置", "建议"]):
            cands.append("wealth_recommend")
        if any(k in text for k in ["申购", "买入", "买"]) and entities.get("product_hint"):
            cands.append("subscribe")
        if "赎回" in text:
            cands.append("redeem")
        if not cands:
            cands = ["wealth_recommend"]
        return cands
    if scenario == "card":
        cands = []
        if "挂失" in text:
            cands.append("report_loss")
        if "补" in text:
            cands.append("reissue")
        if any(k in text for k in ["限额", "调额", "额度"]):
            cands.append("adjust_limit")
        if any(k in text for k in ["冻", "锁", "限制交易", "解限", "解冻", "解锁"]):
            cands.append("toggle_lock")
        if "申请" in text or "办" in text:
            cands.append("card_apply")
        if not cands:
            cands = ["card_list"]
        return cands
    if scenario == "subscription":
        if "取消" in text:
            return ["cancel_subscription"]
        return ["subscription_list"]
    if scenario == "order":
        if "退款" in text:
            return ["query_refund"]
        if "进度" in text or "物流" in text or "快递" in text:
            return ["query_order"]
        return ["order_list"]
    if scenario == "new_customer":
        return ["new_customer_guidance"]
    if scenario == "cross":
        # 生日送礼 -> 跨场景联动
        return ["birthday_gift_flow"]
    return []
