# -*- coding: utf-8 -*-
"""
bank/core.py — 模拟银行核心（Mock Core Banking）

职责：提供纯粹的银行业务逻辑与数据存储（内存态）。
安全/权限控制不在此层，由 agent/security.py 在调用前拦截，本层只负责“被授权后的执行”。
所有金额单位：元（CNY）；外汇兑换仅做演示汇率换算。
"""

import threading
import time
import uuid
from ..agent.validation import money

from .data import get_initial_data
from .world import DigitalEnvironmentMixin

# 演示用汇率（虚构，仅用于跨境/外汇场景演示）
FX_RATES = {
    "USD": 7.18,
    "HKD": 0.92,
    "EUR": 7.75,
    "CNY": 1.0,
}


class BankCore(DigitalEnvironmentMixin):
    """线程安全的模拟银行核心。"""

    def __init__(self):
        self._data = get_initial_data()
        self._lock = threading.RLock()
        self.current_user_id = self._data["current_user_id"]

    # ---------- 基础查询 ----------
    def get_current_user(self):
        with self._lock:
            return dict(self._data["users"][self.current_user_id])

    def security_state(self):
        if self.current_user_id == 'U1001':
            return self._data
        return self._data.setdefault('user_security', {}).setdefault(self.current_user_id, {})

    def get_account(self, user_id=None):
        with self._lock:
            uid = user_id or self.current_user_id
            acct = self._data["accounts"].get(uid)
            return dict(acct) if acct else None

    def list_users(self):
        with self._lock:
            return [dict(u) for uid,u in self._data["users"].items()
                    if uid in (self.current_user_id,'U1002','U1003','U1004')]

    def find_user(self, key):
        """按 姓名 / 手机号(全号或脱敏) / 备注 模糊匹配用户，返回匹配列表。"""
        with self._lock:
            key = str(key).strip()
            res = []
            for u in self.list_users():
                hay = [u["name"], u["phone"], u.get("phone_full", ""), u.get("remark", "")]
                if any(key in h for h in hay):
                    res.append(dict(u))
            return res

    def get_transactions(self, user_id=None, limit=50):
        with self._lock:
            uid = user_id or self.current_user_id
            txns = [t for t in self._data["transactions"] if t["user_id"] == uid]
            txns.sort(key=lambda x: x["ts"], reverse=True)
            return txns[:limit]

    # ---------- 智能转账 ----------
    def transfer(self, to_key, amount, currency="CNY", note="", channel="Agent转账"):
        """执行转账：to_key 可为姓名/手机号/备注；支持外币换算到 CNY 扣款。"""
        with self._lock:
            amount = money(amount)
            if currency not in FX_RATES:
                return {'ok': False, 'error':'不支持的币种'}
            uid = self.current_user_id
            matches = self.find_user(to_key)
            if not matches:
                return {"ok": False, "error": f"未找到收款人：{to_key}"}
            if len(matches) > 1:
                return {"ok": False, "error": "匹配到多个收款人，请补充信息", "candidates": matches}
            payee = matches[0]
            if payee["user_id"] == uid:
                return {"ok": False, "error": "不能向自己转账"}

            acct = self._data["accounts"][uid]
            # 外币：换算为 CNY 扣款
            cny_amount = round(amount * FX_RATES.get(currency, 1.0), 2)
            debit=next((c for c in self._data['cards'].get(uid,[]) if c['type']=='借记卡'),None)
            if debit and (debit['status']!='正常' or debit.get('used_today',0)+cny_amount>debit['daily_limit']):
                return {'ok':False,'error':'借记卡状态受限或超过当日卡限额'}
            if acct["balance"] < cny_amount:
                return {"ok": False, "error": f"余额不足（需 ¥{cny_amount:.2f}，当前 ¥{acct['balance']:.2f}）"}

            acct["balance"] = round(acct["balance"] - cny_amount, 2)
            if debit:debit['used_today']=round(debit.get('used_today',0)+cny_amount,2)
            self._data["accounts"][payee["user_id"]]["balance"] = round(
                self._data["accounts"][payee["user_id"]]["balance"] + cny_amount, 2
            )
            txn = {
                "txn_id": "T" + uuid.uuid4().hex[:8],
                "user_id": uid,
                "counterparty": payee["name"],
                "amount": cny_amount,
                "direction": "out",
                "category": "转账",
                "channel": channel,
                "ts": time.time(),
                "note": note,
            }
            self._data["transactions"].append(txn)
            if hasattr(self, 'commit') and channel != '已授权定时转账':
                self.commit(cny_amount, txn['txn_id'])
            return {
                "ok": True,
                "txn_id": txn["txn_id"],
                "to": payee["name"],
                "amount_cny": cny_amount,
                "amount_orig": amount,
                "currency": currency,
                "balance": acct["balance"],
            }

    def schedule_transfer(self, to_key, amount, execute_ts, currency="CNY", note=""):
        with self._lock:
            amount = money(amount)
            if currency not in FX_RATES or execute_ts <= time.time():
                return {'ok': False, 'error':'币种或执行时间无效'}
            matches = self.find_user(to_key)
            if not matches:
                return {"ok": False, "error": f"未找到收款人：{to_key}"}
            if len(matches) != 1 or matches[0]['user_id'] == self.current_user_id:
                return {'ok': False, 'error':'收款人不唯一或为本人'}
            payee = matches[0]
            task = {
                "task_id": "ST" + uuid.uuid4().hex[:6],
                "owner_id": self.current_user_id,
                "to_user": payee["user_id"],
                "to_name": payee["name"],
                "amount": amount,
                "currency": currency,
                "execute_ts": execute_ts,
                "note": note,
                "created_ts": time.time(),
                "status": "待执行",
            }
            self._data["scheduled_tasks"].append(task)
            self.commit(round(amount * FX_RATES[currency], 2), task['task_id'])
            return {"ok": True, **task}

    def get_scheduled_tasks(self):
        with self._lock:
            return [dict(t) for t in self._data["scheduled_tasks"] if t.get('owner_id','U1001')==self.current_user_id]

    # ---------- 账单分析 ----------
    def bill_analysis(self, period="month"):
        """消费分类统计 + 趋势。period: month / year。"""
        with self._lock:
            uid = self.current_user_id
            txns = [t for t in self._data["transactions"] if t["user_id"] == uid and t["direction"] == "out"]
            now = time.time()
            from datetime import datetime, timezone, timedelta
            dt=datetime.fromtimestamp(now,timezone(timedelta(hours=8)))
            lo=dt.replace(month=1 if period=='year' else dt.month, day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
            window = [t for t in txns if t["ts"] >= lo]
            cats = {}
            total = 0.0
            for t in window:
                cats[t["category"]] = cats.get(t["category"], 0) + t["amount"]
                total += t["amount"]
            cats = dict(sorted(cats.items(), key=lambda x: x[1], reverse=True))
            return {
                "period": period,
                "total_expense": round(total, 2),
                "by_category": {k: round(v, 2) for k, v in cats.items()},
                "txn_count": len(window),
            }

    def detect_anomaly(self):
        """异常交易识别：深夜大额、非常用商户、超高频等。"""
        with self._lock:
            uid = self.current_user_id
            anomalies = []
            for t in self._data["transactions"]:
                if t["user_id"] != uid or t["direction"] != "out":
                    continue
                hour = time.localtime(t["ts"]).tm_hour
                reasons = []
                if hour >= 0 and hour < 6 and t["amount"] >= 1000:
                    reasons.append(f"深夜({hour}:00)大额 ¥{t['amount']:.2f}")
                if t["category"] == "疑似异常":
                    reasons.append("商户/类别标记为疑似异常")
                if t["amount"] >= 4000:
                    reasons.append(f"单笔金额偏高 ¥{t['amount']:.2f}")
                if reasons:
                    anomalies.append({
                        "txn_id": t["txn_id"],
                        "counterparty": t["counterparty"],
                        "amount": t["amount"],
                        "ts": t["ts"],
                        "reasons": reasons,
                    })
            return {"anomaly_count": len(anomalies), "items": anomalies}

    def monthly_report(self):
        with self._lock:
            uid = self.current_user_id
            analysis = self.bill_analysis("month")
            txns = [t for t in self._data["transactions"] if t["user_id"] == uid]
            from datetime import datetime, timezone, timedelta
            dt=datetime.now(timezone(timedelta(hours=8)))
            lo=dt.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
            txns=[t for t in txns if t['ts']>=lo]
            income = sum(t["amount"] for t in txns if t["direction"] == "in")
            expense = sum(t["amount"] for t in txns if t["direction"] == "out")
            return {
                "income": round(income, 2),
                "expense": round(expense, 2),
                "net": round(income - expense, 2),
                "expense_breakdown": analysis,
            }

    # ---------- 理财操作 ----------
    def wealth_list(self):
        with self._lock:
            return [dict(p) for p in self._data["wealth_products"]]

    def wealth_recommend(self, risk_pref="中"):
        """按风险偏好推荐并对比产品。"""
        with self._lock:
            prods = self._data["wealth_products"]
            # 风险偏好映射
            order = {"低": 0, "中": 1, "高": 2}
            ranked = sorted(prods, key=lambda p: abs(order.get(p["risk_level"], 1) - order.get(risk_pref, 1)))
            return {
                "risk_pref": risk_pref,
                "recommended": [dict(p) for p in ranked if order[p["risk_level"]] <= order.get(risk_pref,0)][:2],
                "all": [dict(p) for p in prods],
            }

    def risk_assess(self, answers=None):
        """简化风险评估问卷评分。"""
        questions=['投资期限 A不足3月 B一年 C三年以上','可承受亏损 A零 B10% C30%','投资经验 A无 B一年 C三年以上']
        if not answers or set(answers)!= {'1','2','3'} or any(x not in ('A','B','C') for x in answers.values()):
            return {'status':'need_answers','score':None,'level':'待评估','suggest':'请回复 风险评估 A B A，问卷仅作模拟','questions':questions}
        score=sum({'A':0,'B':1,'C':2}[x] for x in answers.values())
        level='高' if score>=5 else '中' if score>=3 else '低'
        self._data['risk_profile']={'score':score,'level':level}
        return {'score':score,'level':level,'suggest':f'模拟问卷建议{level}风险及以下产品，不构成真实适当性认定'}

    def subscribe(self, product_id, amount):
        with self._lock:
            amount = money(amount)
            uid = self.current_user_id
            prod = next((p for p in self._data["wealth_products"] if p["product_id"] == product_id), None)
            if not prod:
                return {"ok": False, "error": "产品不存在"}
            profile=self._data.get('risk_profile',{'level':'低'})
            if {'低':0,'中':1,'高':2}[prod['risk_level']] > {'低':0,'中':1,'高':2}[profile['level']]:
                return {'ok':False,'error':'产品风险高于模拟风险档案，请先完成问卷'}
            if amount < prod["min_amount"]:
                return {"ok": False, "error": f"起购金额 ¥{prod['min_amount']}"}
            acct = self._data["accounts"][uid]
            if acct["balance"] < amount:
                return {"ok": False, "error": "余额不足"}
            acct["balance"] = round(acct["balance"] - amount, 2)
            self._data.setdefault("holdings", {}).setdefault(uid, []).append(
                {"product_id": product_id, "amount": amount, "buy_ts": time.time()}
            )
            return {"ok": True, "product": prod["name"], "amount": amount, "balance": acct["balance"]}

    def redeem(self, product_id, amount):
        with self._lock:
            amount = money(amount)
            uid = self.current_user_id
            holds = self._data.get("holdings", {}).get(uid, [])
            hold = next((h for h in holds if h["product_id"] == product_id), None)
            if not hold:
                return {"ok": False, "error": "无该持仓"}
            prod=next(p for p in self._data['wealth_products'] if p['product_id']==product_id)
            if prod['term'].endswith('天') and time.time()-hold['buy_ts'] < int(prod['term'][:-1])*86400:
                return {'ok':False,'error':'封闭期产品未到期，不能提前赎回'}
            amt = min(amount, hold["amount"])
            hold["amount"] = round(hold["amount"] - amt, 2)
            self._data["accounts"][uid]["balance"] = round(
                self._data["accounts"][uid]["balance"] + amt, 2
            )
            return {"ok": True, "product_id": product_id, "redeemed": amt, "balance": self._data["accounts"][uid]["balance"]}

    def get_holdings(self):
        with self._lock:
            uid = self.current_user_id
            return [dict(h) for h in self._data.get("holdings", {}).get(uid, [])]

    # ---------- 卡片管理 ----------
    def card_list(self):
        with self._lock:
            uid = self.current_user_id
            return [dict(c) for c in self._data["cards"].get(uid, [])]

    def card_apply(self, card_type="借记卡"):
        with self._lock:
            uid = self.current_user_id
            cid = "C" + uuid.uuid4().hex[:4]
            card = {"card_id": cid, "type": card_type, "status": "正常", "daily_limit": 50000, "used_today": 0.0}
            if card_type == "信用卡":
                card["credit_limit"] = 20000
                card["used_limit"] = 0.0
                card["available"] = 20000
            self._data["cards"].setdefault(uid, []).append(card)
            return {"ok": True, **card}

    def adjust_limit(self, card_id, new_limit):
        with self._lock:
            new_limit = money(new_limit)
            if new_limit > 50000:return {'ok':False,'error':'演示日限额上限50000元'}
            uid = self.current_user_id
            for c in self._data["cards"].get(uid, []):
                if c["card_id"] == card_id:
                    c["daily_limit"] = new_limit
                    return {"ok": True, "card_id": card_id, "daily_limit": new_limit}
            return {"ok": False, "error": "卡片不存在"}

    def toggle_lock(self, card_id, lock=True):
        with self._lock:
            uid = self.current_user_id
            for c in self._data["cards"].get(uid, []):
                if c["card_id"] == card_id:
                    if not lock and c['status'] not in ('正常', '冻结'):
                        return {'ok':False, 'error':'挂失或失效卡片不能解冻'}
                    c["status"] = "冻结" if lock else "正常"
                    return {"ok": True, "card_id": card_id, "status": c["status"]}
            return {"ok": False, "error": "卡片不存在"}

    def report_loss(self, card_id):
        with self._lock:
            uid = self.current_user_id
            for c in self._data["cards"].get(uid, []):
                if c["card_id"] == card_id:
                    c["status"] = "挂失"
                    return {"ok": True, "card_id": card_id, "status": "挂失"}
            return {"ok": False, "error": "卡片不存在"}

    def reissue(self, card_id):
        with self._lock:
            uid = self.current_user_id
            for c in self._data["cards"].get(uid, []):
                if c["card_id"] == card_id:
                    new_id = "C" + uuid.uuid4().hex[:4]
                    c["status"] = "已补卡-旧卡失效"
                    self._data['cards'][uid].append({'card_id':new_id,'type':c['type'],'status':'正常','daily_limit':50000,'used_today':0})
                    return {"ok": True, "old_card": card_id, "new_card": new_id}
            return {"ok": False, "error": "卡片不存在"}

    # ---------- 订阅 / 代扣管理 ----------
    def subscription_list(self):
        with self._lock:
            uid = self.current_user_id
            return [dict(s) for s in self._data["subscriptions"].get(uid, [])]

    def cancel_subscription(self, sub_id):
        with self._lock:
            uid = self.current_user_id
            for s in self._data["subscriptions"].get(uid, []):
                if s["sub_id"] == sub_id:
                    s["status"] = "已取消"
                    return {"ok": True, "sub_id": sub_id, "merchant": s["merchant"]}
            return {"ok": False, "error": "订阅不存在"}

    # ---------- 订单代理查询 ----------
    def order_list(self):
        with self._lock:
            uid = self.current_user_id
            return [dict(o) for o in self._data["orders"].get(uid, [])]

    def query_order(self, order_id):
        with self._lock:
            uid = self.current_user_id
            for o in self._data["orders"].get(uid, []):
                if o["order_id"] == order_id:
                    return {"ok": True, **o}
            return {"ok": False, "error": "订单不存在"}

    def query_refund(self, order_id):
        with self._lock:
            uid = self.current_user_id
            for o in self._data["orders"].get(uid, []):
                if o["order_id"] == order_id:
                    return {"ok": True, "order_id": order_id, "refund_status": o["refund_status"], "progress": o["progress"]}
            return {"ok": False, "error": "订单不存在"}

    # ---------- 新客服务引导 ----------
    def new_customer_guidance(self, node=None):
        with self._lock:
            uid = self.current_user_id
            act = self._data["new_customer_activity"]
            granted = act["granted"].get(uid, [])
            if node:
                match = node if node in act["nodes"] else next(
                    (n for n in act["nodes"] if n in node or node in n), None
                )
                if not match:
                    return {"ok": False, "error": "节点不存在"}
                if match in granted:
                    return {"ok": False, "error": f"节点「{match}」已领取"}
                granted.append(match)
                act["granted"][uid] = granted
                return {"ok": True, "node": match, "granted_budget": act["budget"], "granted_nodes": granted}
            return {"ok": True, "activity": act["name"], "budget": act["budget"], "nodes": act["nodes"], "granted": granted}

    # ---------- 跨场景联动 ----------
    def birthday_gift_flow(self, to_key, amount=200):
        """跨场景联动示例：识别生日需求 → 锁定资金 → 自动完成转账。"""
        with self._lock:
            uid = self.current_user_id
            matches = self.find_user(to_key)
            if not matches:
                return {"ok": False, "error": f"未找到收款人：{to_key}"}
            payee = matches[0]
            # 1) 锁定资金（预扣）
            acct = self._data["accounts"][uid]
            if acct["balance"] < amount:
                return {"ok": False, "error": "余额不足，无法锁定生日礼金"}
            # 2) 执行转账
            return self.transfer(to_key, amount, note="生日快乐🎂")


# 全局单例（演示用；生产应改为按会话隔离）
_core = BankCore()


def get_core():
    return _core
