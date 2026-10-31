"""Policy-controlled DAG executor with observations, receipts and sandbox rollback."""
import time
import copy
from . import security
from .tools import call_tool
from .validation import validate
from ..bank.core import get_core

DEMO_TXN_PASSWORD = "123456"
TERMINAL = {"executed", "failed", "cancelled", "skipped", "rolled_back"}
MUTATIONS = {'transfer','schedule_transfer','subscribe','redeem','card_apply','adjust_limit',
             'toggle_lock','report_loss','reissue','cancel_subscription','new_customer_guidance',
             'birthday_gift_flow','reserve_budget','place_gift_order','finalize_birthday'}

class Executor:
    def __init__(self):
        self.core = get_core()
        self.anomaly = security.get_anomaly_guard()
        self.audit = security.get_audit()

    def execute_plan(self, plan, confirmed=None):
        confirmed = confirmed or {}
        messages, pending = [], None
        ids=[s['step_id'] for s in plan]
        if len(set(ids))!=len(ids) or any(any(dep not in ids[:i] for dep in s.get('depends_on',[])) for i,s in enumerate(plan)):
            return {'steps':plan,'pending':None,'messages':['无效或有环任务图，拒绝执行'],'done':True}
        with self.core._lock:
            for step in plan:
                if step['status'] in TERMINAL:
                    continue
                op, params = step['operation'], copy.deepcopy(step.get('params', {}))
                deps = [s for s in plan if s['step_id'] in step.get('depends_on', [])]
                if any(s['status'] in TERMINAL - {'executed'} for s in deps):
                    step.update(status='skipped', result={'ok':False,'error':'前置任务未成功'})
                    continue
                if any(s['status'] != 'executed' for s in deps):
                    break
                # Resolve only explicit prior-step outputs, never arbitrary object paths.
                for key, value in list(params.items()):
                    if isinstance(value, str) and value.startswith('$'):
                        ref = next((s for s in deps if s['step_id'] == value[1:]), None)
                        if not ref or not ref.get('result', {}).get('id'):
                            params[key] = None
                        else:
                            params[key] = ref['result']['id']
                try:
                    validate(op, params)
                    if op in ('place_gift_order','finalize_birthday') and not deps:
                        raise ValueError('预约动作必须依赖本计划的预算授权')
                except (ValueError, TypeError) as e:
                    self.fail(step, str(e), messages)
                    continue
                risk, strategy, reason = security.assess_risk(op, params)
                # Confirmation evaluation may raise risk but never lowers displayed risk.
                if step.get('risk') == 'HIGH':
                    risk = 'HIGH'
                step.update(risk=risk, strategy=security.CONTROL_STRATEGY[risk], risk_reason=reason or step.get('risk_reason',''))
                blocked, _, reasons = self.anomaly.evaluate(op, params)
                if blocked:
                    self.core._data['security_locked']=True
                    self.core.save()
                    self.fail(step, '风控锁定：' + '；'.join(reasons), messages)
                    continue
                if risk in ('MEDIUM','HIGH'):
                    conf = confirmed.get(step['step_id'])
                    if not conf or (risk == 'HIGH' and not conf.get('strong_verified')):
                        step.update(status='await_confirm', confirm_required=True, need_password=risk=='HIGH')
                        pending = step
                        break
                before = self.core.observe()
                state = self.core.snapshot()
                result, _ = call_tool(op, params)
                if result.get('ok') is False:
                    self.fail(step, result.get('error','执行失败'), messages)
                    continue
                after = self.core.observe()
                # Postcondition verification grounds claimed success in environment state.
                verified = True
                if op in ('transfer','birthday_gift_flow'):
                    verified = round(before['balance'] - after['balance'], 2) == result.get('amount_cny')
                if op == 'reserve_budget':
                    verified = round(after['held'] - before['held'], 2) == params['amount']
                if op == 'cancel_subscription':
                    verified = any(x['sub_id']==params['sub_id'] and x['status']=='已取消' for x in after['subscriptions'])
                if op in ('subscribe','redeem'):
                    before_sum=sum(h['amount'] for h in before['holdings'])
                    after_sum=sum(h['amount'] for h in after['holdings'])
                    verified=(round(after_sum-before_sum,2)==params['amount']) if op=='subscribe' else (round(after['balance']-before['balance'],2)==result['redeemed'])
                if op in ('report_loss','toggle_lock','adjust_limit'):
                    card=next((c for c in after['cards'] if c['card_id']==params['card_id']),None)
                    field='daily_limit' if op=='adjust_limit' else 'status'
                    expected=params['new_limit'] if op=='adjust_limit' else ('挂失' if op=='report_loss' else '冻结' if params['lock'] else '正常')
                    verified=card is not None and card.get(field)==expected
                if op=='card_apply':verified=any(c['card_id']==result['card_id'] for c in after['cards'])
                if op=='reissue':verified=any(c['card_id']==result['new_card'] for c in after['cards'])
                if op=='schedule_transfer':verified=any(t['task_id']==result['task_id'] for t in after['scheduled'])
                if op=='place_gift_order':verified=any(o['order_id']==result['order_id'] for o in after['orders'])
                if op=='finalize_birthday':verified=any(t['task_id']==result['task']['task_id'] for t in after['scheduled'])
                if not verified:
                    self.core.restore(state)
                    self.fail(step, '后置条件核验失败，已回退本地沙箱', messages)
                    continue
                step.update(status='executed', result=result,
                            observation={'before':before,'after':after,'verified':True})
                if op in MUTATIONS:
                    self.core._revision = getattr(self.core,'_revision',0) + 1
                self.core.save()
                self._audit_log(self.core.current_user_id, op, params, risk, True, 'verified', step)
                messages.append(self._humanize(op, params, result))
        return {'steps':plan,'pending':pending,'messages':messages,'done':all(s['status'] in TERMINAL for s in plan)}

    def fail(self, step, error, messages):
        step.update(status='failed', result={'ok':False,'error':error})
        self._audit_log(self.core.current_user_id, step['operation'], step.get('params',{}),step.get('risk','HIGH'),False,error,step)
        messages.append('安全执行失败：'+error)

    def _audit_log(self, user, op, params, risk, ok, note, step=None):
        import hashlib, json, re
        safe = {k: ('***' if k in ('to_key','note') else v) for k,v in params.items() if k not in ('password','otp')}
        self.audit.record({'user':user,'operation':op,'params':safe,'risk':risk,'success':ok,'note':note,
                           'step_id':(step or {}).get('step_id'), 'depends_on':(step or {}).get('depends_on',[]),
                           'verified':bool((step or {}).get('observation',{}).get('verified'))})

    def _humanize(self, op, params, result):
        """把工具结果转成自然语言回复。"""
        if op == "transfer":
            return f"✅ 已向「{result.get('to')}」转账 {result.get('amount_cny')} 元，余额 {result.get('balance')} 元。"
        if op == "schedule_transfer":
            return f"✅ 已创建定时转账（任务号 {result.get('task_id')}），将于约定时间执行。"
        if op == "bill_analysis":
            cats = result.get("by_category", {})
            top = list(cats.items())[:3]
            top_txt = "、".join(f"{k} {v}元" for k, v in top)
            return f"📊 近{('月' if result.get('period')=='month' else '年')}消费合计 {result.get('total_expense')} 元，主要类别：{top_txt}。"
        if op == "detect_anomaly":
            if result.get("anomaly_count"):
                items = "；".join(f"{i['counterparty']} {i['amount']}元（{'，'.join(i['reasons'])}）" for i in result["items"])
                return f"🔍 发现 {result['anomaly_count']} 笔异常交易：{items}。建议核实或冻结卡片。"
            return "🔍 未发现明显异常交易。"
        if op == "monthly_report":
            return f"📈 本月收入 {result.get('income')} 元，支出 {result.get('expense')} 元，结余 {result.get('net')} 元。"
        if op == "wealth_recommend":
            rec = result.get("recommended", [])
            txt = "、".join(f"{p['name']}（{p['risk_level']}风险，预期{p['expected_return']}）" for p in rec)
            return f"💡 根据您的风险偏好，推荐：{txt}。"
        if op == "risk_assess":
            return f"🧮 风险评估得分 {result.get('score')}，等级「{result.get('level')}」：{result.get('suggest')}。"
        if op == "subscribe":
            return f"✅ 已申购「{result.get('product')}」{result.get('amount')} 元，账户余额 {result.get('balance')} 元。"
        if op == "redeem":
            return f"✅ 已赎回 {result.get('redeemed')} 元，到账后余额 {result.get('balance')} 元。"
        if op == "card_apply":
            return f"✅ 已申请{result.get('type')}（卡号 {result.get('card_id')}）。"
        if op == "adjust_limit":
            return f"✅ 卡片 {result.get('card_id')} 限额已调整为 {result.get('daily_limit')} 元。"
        if op == "toggle_lock":
            return f"✅ 卡片 {result.get('card_id')} 已{('冻结' if result.get('status')=='冻结' else '解冻')}。"
        if op == "report_loss":
            return f"🛡️ 卡片 {result.get('card_id')} 已挂失，旧卡失效，请尽快补卡。"
        if op == "reissue":
            return f"✅ 已补发新卡 {result.get('new_card')}，旧卡 {result.get('old_card')} 失效。"
        if op == "cancel_subscription":
            return f"✅ 已取消「{result.get('merchant')}」的订阅/代扣。"
        if op == "subscription_list":
            subs = result.get("subscriptions", [])
            txt = "、".join(f"{s['merchant']}（{s['amount']}元/{s['cycle']}，{s['status']}）" for s in subs)
            return f"📋 当前订阅/代扣：{txt}。"
        if op == "order_list":
            orders = result.get("orders", [])
            txt = "；".join(f"{o['order_id']} {o['product']}（{o['status']}）" for o in orders)
            return f"📦 您的订单：{txt}。"
        if op == "query_order":
            return f"📦 订单 {result.get('order_id')}：{result.get('product')}，状态「{result.get('status')}」，进度：{result.get('progress')}。"
        if op == "query_refund":
            return f"💰 订单 {result.get('order_id')} 退款状态：{result.get('refund_status')}（{result.get('progress')}）。"
        if op == "new_customer_guidance":
            if result.get("ok") and result.get("node"):
                return f"🎁 已为您领取「{result.get('node')}」节点礼包（额度 {result.get('granted_budget')} 元），已领取：{','.join(result.get('granted_nodes', []))}。"
            return f"🌟 新客活动「{result.get('activity')}」：人生阶段礼包共 {result.get('budget')} 元，可选节点：{','.join(result.get('nodes', []))}。说“领取XX节点”即可。"
        if op == "birthday_gift_flow":
            return f"🎂 跨场景联动完成：已在沙箱向「{result.get('to')}」送上礼金 {result.get('amount_cny')} 元。"
        if op == "query_balance":
            return f"💰 您的账户余额：{result.get('balance')} 元（{result.get('type')}，账号 {result.get('account_no')}）。"
        if op == "query_transactions":
            txns = result.get("transactions", [])[:5]
            txt = "；".join(f"{time.strftime('%m-%d', time.localtime(t['ts']))} {t['counterparty']} {t['amount']}元" for t in txns)
            return f"🧾 近期交易（前5笔）：{txt}。"
        if op == "get_holdings":
            holds = result.get("holdings", [])
            if not holds:
                return "📭 您当前暂无理财持仓。"
            txt = "、".join(f"{h['product_id']} {h['amount']}元" for h in holds)
            return f"📊 当前持仓：{txt}。"
        if op == "card_list":
            cards = result.get("cards", [])
            txt = "、".join(f"{c['card_id']}({c['type']},{c['status']})" for c in cards)
            return f"💳 您的卡片：{txt}。"
        return f"✅ {op} 完成。"
