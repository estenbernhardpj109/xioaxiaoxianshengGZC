# -*- coding: utf-8 -*-
"""
agent/security.py — 安全与权限分级层

实现赛题《三、核心技术要求》中的：
1. 权限分级机制（低风险自动 / 中风险一次确认 / 高风险二次确认+交易密码）
2. 安全机制：防越权、Prompt 注入防护、操作留痕（审计）、异常拦截

设计原则：安全层在“工具执行前”拦截，核心层只执行被授权操作。
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# 1) 权限分级：操作 -> 基础风险等级
# ---------------------------------------------------------------------------
# 风险等级：LOW（低）/ MEDIUM（中）/ HIGH（高）
RISK_TABLE = {
    # 低风险：查询类、分析类，直接执行
    "query_balance": "LOW",
    "query_transactions": "LOW",
    "bill_analysis": "LOW",
    "detect_anomaly": "LOW",
    "monthly_report": "LOW",
    "wealth_list": "LOW",
    "wealth_recommend": "LOW",
    "risk_assess": "LOW",
    "get_holdings": "LOW",
    "card_list": "LOW",
    "subscription_list": "LOW",
    "order_list": "LOW",
    "query_order": "LOW",
    "query_refund": "LOW",
    "new_customer_guidance": "LOW",
    "get_scheduled_tasks": "LOW",
    # 中风险：一次确认
    "subscribe": "HIGH",
    "redeem": "HIGH",
    "adjust_limit": "MEDIUM",
    "toggle_lock": "MEDIUM",
    "reissue": "HIGH",
    "cancel_subscription": "MEDIUM",
    "card_apply": "MEDIUM",
    "schedule_transfer": "MEDIUM",
    # 高风险：二次确认 + 交易密码
    "transfer": "HIGH",          # 大额(>10000)触发高；小额归入中，见 assess_risk
    "report_loss": "HIGH",
    "birthday_gift_flow": "HIGH",
}

# 各等级对应的控制策略描述
CONTROL_STRATEGY = {
    "LOW": "直接执行，无需额外确认",
    "MEDIUM": "用户确认一次后执行",
    "HIGH": "需交易密码 + 一次性验证码（本地模拟身份因子）",
}


def base_risk(operation):
    return RISK_TABLE.get(operation, "MEDIUM")


def assess_risk(operation, params=None):
    """
    综合评估风险等级与控制策略。
    - 转账按金额细分：≤1000 中；1000<amt≤10000 中；>10000 高。
    - 返回 (level, strategy, reason)
    """
    params = params or {}
    level = base_risk(operation)
    reason = ""

    if operation == 'new_customer_guidance' and params.get('node'):
        level = 'MEDIUM'
    if operation in ("transfer", "birthday_gift_flow", "schedule_transfer", "reserve_budget"):
        from ..bank.core import get_core, FX_RATES
        core = get_core()
        amt = float(params.get("amount", 0) or 0) * FX_RATES.get(params.get('currency', 'CNY'), 1)
        used = core.daily_committed() if hasattr(core, 'daily_committed') else 0
        if round(used + amt, 2) > 1000:
            level = "HIGH"
            reason = f"今日累计承诺 ¥{used:.2f} + 本笔 ¥{amt:.2f} > 1000 元，需强验证"
        elif amt <= 1000:
            level = "MEDIUM"
            reason = f"小额转账 ¥{amt:.2f}（≤1000），需一次确认"
    if operation in ('place_gift_order', 'finalize_birthday'):
        level = 'LOW'  # Only consumes the previously confirmed reservation.
    if operation == 'toggle_lock' and params.get('lock') is False:
        level = 'HIGH'

    return level, CONTROL_STRATEGY[level], reason


# ---------------------------------------------------------------------------
# 2) Prompt 注入防护
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    r"忽略(上述|上面|之前|前面|以上).{0,10}指令",
    r"ignore (the |previous |above |all )?(instructions|prompt|rules)",
    r"忘记(上述|上面|之前).{0,10}指令",
    r"disregard (the |previous |above )?(instructions|prompt)",
    r"现在你是(一个|一名|新的)?(管理员|开发者|root|系统|银行后台)",
    r"system\s*prompt",
    r"作为(管理员|开发者|root|超级用户|银行内部)",
    r"绕过(安全|权限|确认|验证|风控)",
    r"不要(再)?(确认|验证|询问|让我确认)",
    r"直接(执行|转账|操作).{0,6}(不用|无需|别|不要).{0,6}(确认|密码|验证)",
    r"override",
    r"jailbreak",
    r"开发者模式",
    r"解除(限制|风控|冻结)",
]


def detect_injection(text):
    """检测是否包含 Prompt 注入/越权指令尝试。返回 (is_injection, matched, hint)。"""
    if not text:
        return False, None, ""
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return True, pat, f"检测到疑似指令注入片段：「{m.group(0)}」，已拦截并忽略该部分指令。"
    return False, None, ""


# ---------------------------------------------------------------------------
# 3) 防越权：操作主体必须为自己
# ---------------------------------------------------------------------------
def check_privilege(operation, caller_user_id, target_user_id=None):
    """
    校验操作是否越权。Agent 只能操作当前登录用户(caller)的资源。
    target_user_id 为被操作对象；若与 caller 不同且操作非“转账给他人”则视为越权。
    """
    # 允许“向他人转账/送礼”本身，但目标不能是 caller 自己由工具层再次校验
    allow_cross_user = {"transfer", "birthday_gift_flow", "schedule_transfer"}
    if target_user_id and target_user_id != caller_user_id and operation not in allow_cross_user:
        return False, f"拒绝越权操作：您无权操作用户 {target_user_id} 的「{operation}」"
    return True, ""


# ---------------------------------------------------------------------------
# 4) 异常交易拦截（时间/金额/频次）
# ---------------------------------------------------------------------------
class AnomalyGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._recent_transfers = []  # (ts, amount)

    def evaluate(self, operation, params=None):
        """
        返回 (blocked: bool, level: str, reasons: list)
        level: NORMAL / WARN / BLOCK
        """
        params = params or {}
        reasons = []
        level = "NORMAL"
        if operation in ("transfer", "birthday_gift_flow", "schedule_transfer", "reserve_budget"):
            amt = float(params.get("amount", 0) or 0)
            hour = time.localtime().tm_hour
            with self._lock:
                now = time.time()
                # 深夜大额
                if hour >= 0 and hour < 6 and amt >= 1000:
                    reasons.append(f"深夜({hour}:00)发起 ¥{amt:.2f} 转账，触发风控复核")
                    level = "WARN"
                # 超大额
                if amt > 50000:
                    reasons.append(f"单笔超过 ¥50000，超过单次限额，已拦截")
                    level = "BLOCK"
                from ..bank.core import get_core
                recent=[t for t in get_core()._data.get('commitments',[]) if now-t['ts']<600]
                if len(recent)>=5:
                    reasons.append('十分钟内已累计五次资金承诺，触发安全锁定')
                    level='BLOCK'
                # Evaluating a plan never counts as a transfer.
                self._recent_transfers = [t for t in self._recent_transfers if now - t[0] < 600]

                if len(self._recent_transfers) > 5 and level == "NORMAL":
                    reasons.append("短时间内频繁转账，触发反洗钱复核")
                    level = "WARN"
        return level == "BLOCK", level, reasons


_anomaly_guard = AnomalyGuard()


def get_anomaly_guard():
    return _anomaly_guard


# ---------------------------------------------------------------------------
# 5) 操作留痕（审计日志）
# ---------------------------------------------------------------------------
class AuditLog:
    def __init__(self, path=None):
        self._lock = threading.Lock()
        self._mem = []
        self.path = path or os.environ.get('BANK_AUDIT_FILE') or os.path.join(os.path.dirname(__file__), '..', 'audit.log')
        if self.path: os.makedirs(os.path.dirname(os.path.abspath(self.path)),exist_ok=True)
        if self.path and os.path.isfile(self.path):
            try:
                with open(self.path,encoding='utf-8') as f:
                    self._mem=[json.loads(line) for line in f if line.strip() and '"hash"' in line]
            except (ValueError,OSError):
                raise RuntimeError('审计文件无法读取，拒绝静默忽略')
        # 确保文件存在
        try:
            open(self.path, "a", encoding="utf-8").close()
        except Exception:
            self.path = None

    def record(self, entry):
        entry = dict(entry)
        entry.setdefault("ts", time.time())
        with self._lock:
            import hashlib
            prev = self._mem[-1]['hash'] if self._mem and 'hash' in self._mem[-1] else '0' * 64
            entry['prev_hash'] = prev
            entry['hash'] = hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            self._mem.append(entry)
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    pass

    def recent(self, limit=100):
        with self._lock:
            return list(reversed(self._mem[-limit:]))


_audit = AuditLog()


def get_audit():
    return _audit
