# -*- coding: utf-8 -*-
"""
agent/agent.py — 银行 AI 智能体（顶层编排）

串联：安全注入检测 → NLU 意图理解 → 澄清追问 → 任务规划 → 安全执行 → 多轮确认。
对外暴露 handle()（处理新消息）与 confirm()（处理确认回调）。
"""

import re
import time
import uuid
import secrets
import hashlib
import json
import copy

from . import security
from . import llm
from .context import get_session_manager
from .nlu import understand
from .planner import build_plan, _make_summary
from .executor import Executor
from .tools import TOOL_META, tool_schemas
from ..bank.core import get_core

# 演示用交易密码提示
DEMO_TXN_PASSWORD = "123456"


class BankingAgent:
    def __init__(self):
        self.mgr = get_session_manager()
        self.executor = Executor()

    # ------------------------------------------------------------------
    def handle(self, text, session_id=None, use_llm_plan=False):
        """处理一条用户消息，返回结构化响应。

        use_llm_plan=True 时启用主流「LLM 工具调用（tool-calling）」规划路径；
        默认（False）使用确定性意图路由 + LLM 实体增强，稳定可控、适合生产。
        """
        session = self.mgr.get_or_create(session_id)
        text = (text or "").strip()
        session.add("user", text)
        if session.locked or get_core()._data.get('security_locked'):
            return self._clarify(session, '连续验证失败，账户已安全锁定，请人工接管')
        if text in ('暂停','中断','继续','取消任务','回退','人工接管'):
            return self.control(session, text)
        if session.pending_plan and any(x['status'] in ('pending','await_confirm') for x in session.pending_plan):
            return self._clarify(session, '当前有待执行计划，请先确认、取消任务或人工接管')

        # 0) 安全：Prompt 注入防护
        is_inj, pat, hint = security.detect_injection(text)
        if is_inj:
            security.get_audit().record({'operation':'injection_block','risk':'HIGH','success':False,'note':'注入模式拦截'})
            session.add("agent", hint)
            return {
                "session_id": session.session_id,
                "reply": f"🛡️ 安全提示：{hint}\n我不会执行消息中嵌入的指令性内容。请直接用自然语言告诉我您想办理的银行业务（如“给李婷转500元”）。",
                "plan": None,
                "pending_confirm": None,
                "viz": None,
                "security_alert": True,
            }

        if any(w in text for w in ('每天','每月')):
            return self._clarify(session,'当前支持一次性定时转账，请明确单次日期；重复周期转账尚未实现')
        # 1) 多轮补全：若上一轮在等澄清
        if session.pending_intent:
            combined = (session.pending_intent.get("text", "") + " " + text).strip()
            intent = understand(combined)
            session.pending_intent = None
        else:
            intent = understand(text)
            # 可选 LLM 增强意图理解（无配置则 None，回退确定性引擎）
            llm_res = None  # Offline mode makes no network calls
            if llm_res:
                intent = self._merge_llm(intent, llm_res)

        if '生日' in text and any(w in text for w in ('鲜花','蛋糕','预留')):
            dates = re.findall(r'20\d{2}-\d{2}-\d{2}', text)
            amount_match = re.search(r'(\d+(?:\.\d+)?)\s*元', text)
            payees = [u for u in get_core().list_users() if u['name'] in text and u['user_id'] != get_core().current_user_id]
            if not dates or not amount_match or len(payees) != 1:
                return self._clarify(session, '请提供唯一收礼人、生日日期 YYYY-MM-DD 和预算，例如 李婷2026-11-10生日，预留1000元买鲜花蛋糕')
            intent.update(scenario='cross', candidates=['reserve_budget','place_gift_order','place_gift_order','finalize_birthday'], need_clarify=False)
            intent['entities'].update(to_key=payees[0]['name'], amount=float(amount_match.group(1)), birthday_date=dates[0])

        intent['_text']=text
        # 2) 规划
        #    默认：确定性意图路由（可靠、安全、可审计），LLM 仅增强实体/场景理解。
        #    可选：主流 LLM 工具调用（tool-calling）路径，use_llm_plan=True 时启用。
        plan = None
        planner_source = 'rule_fallback' if use_llm_plan else 'rule'
        summary = ""
        if use_llm_plan and llm.is_configured():
            lp = self._plan_via_llm(text, self._hist_text(session))
            if lp is not None:
                plan, ask_text = self._llm_to_plan(lp, intent)
                if plan is not None:planner_source='deepseek_tool_call'
                if ask_text:
                    return self._clarify(session, ask_text)

        # 3) 确定性澄清（仅在 LLM 未给出可执行计划时生效，避免误拦有效计划）
        if plan is None and intent.get("need_clarify"):
            session.pending_intent = {"text": text, "nlu": intent}
            session.add("agent", intent["clarify"])
            return {
                "session_id": session.session_id,
                "reply": f"🤖 {intent['clarify']}",
                "plan": None,
                "pending_confirm": None,
                "viz": None,
            }

        if plan is None:
            plan, summary = build_plan(intent, intent.get("entities", {}))
        if not plan:
            reply = "🤖 抱歉，我暂时没理解您的意图。您可以试试：“给李婷转500元”“分析一下我的账单”“推荐稳健理财”“挂失我的信用卡”。"
            session.add("agent", reply)
            return {"session_id": session.session_id, "reply": reply, "plan": None, "pending_confirm": None, "viz": None}

        if intent.get('entities',{}).get('birthday_date'):
            e = intent['entities']
            raw = [('reserve_budget',{'to_key':e['to_key'],'amount':e['amount'],'birthday_date':e['birthday_date']}),
                   ('place_gift_order',{'reservation_id':'$reserve','kind':'鲜花'}),
                   ('place_gift_order',{'reservation_id':'$reserve','kind':'蛋糕'}),
                   ('finalize_birthday',{'reservation_id':'$reserve'})]
            plan=[]
            for i,(op,params) in enumerate(raw):
                level,strategy,reason=security.assess_risk(op,params)
                plan.append({'step_id':['reserve','flowers','cake','join'][i],'operation':op,'params':params,
                             'description':TOOL_META[op]['desc'] + ' ' + json.dumps(params,ensure_ascii=False),
                             'depends_on':[[],['reserve'],['reserve'],['reserve','flowers','cake']][i],
                             'risk':level,'strategy':strategy,'risk_reason':reason,'status':'pending','result':None})
        else:
            previous=[]
            for step in plan:
                step['depends_on']=previous[-1:]
                previous.append(step['step_id'])
        # Unique IDs prevent old UI callbacks authorizing a new plan.
        prefix=uuid.uuid4().hex[:12]+'-'
        for step in plan:
            step['step_id']=prefix+step['step_id']
            step['depends_on']=[prefix+x for x in step['depends_on']]
            for k,v in step['params'].items():
                if isinstance(v,str) and v.startswith('$'):
                    step['params'][k]='$'+prefix+v[1:]

        # 3.5) 预校验与引用解析（收款人/卡片/订阅），失败时转澄清
        core = get_core()
        ents = intent.get("entities", {})
        for step in plan:
            op = step["operation"]
            p = step["params"]
            if op in ("transfer", "birthday_gift_flow", "schedule_transfer"):
                # 优先从文本中识别已知用户（覆盖“送她200”等代词/备注表述）
                if not p.get("to_key") or not core.find_user(p["to_key"]):
                    for u in core.list_users():
                        if u["name"] in text or (u.get("remark") and u["remark"] in text):
                            p["to_key"] = u["name"]
                            break
                if not p.get("to_key"):
                    return self._clarify(session, "请问要转给谁？（可说姓名、手机号或备注）")
                if not core.find_user(p["to_key"]):
                    return self._clarify(
                        session,
                        f"未找到收款人「{p['to_key']}」，请确认姓名/手机号/备注（可转给：李婷 / 王浩 / 陈晓）。",
                    )
                # 刷新步骤描述中的收款人（避免早期解析为 None 的展示问题）
                if step["description"] and "None" in step["description"]:
                    step["description"] = step["description"].replace("None", p["to_key"])
            if op in ("report_loss", "reissue", "adjust_limit", "toggle_lock"):
                if not p.get("card_id"):
                    ctype = ents.get("card_type")
                    cards = core.card_list()
                    m = [c for c in cards if ctype is None or c["type"] == ctype]
                    if len(m)==1:
                        p["card_id"] = m[0]["card_id"]
                    else:
                        return self._clarify(session, "请明确要操作哪张卡（信用卡/借记卡），不能自动选择多张候选中的一张。")
            if op == "cancel_subscription" and not p.get("sub_id"):
                subs = core.subscription_list()
                hint = ents.get("merchant_hint")
                m = [s for s in subs if s["merchant"] in text or (hint and hint in s["merchant"])]
                if m:
                    p["sub_id"] = m[0]["sub_id"]
                else:
                    return self._clarify(session, "未匹配到对应订阅，请确认订阅名称（如：腾讯视频会员）。")

        # 校验后重算摘要，确保收款人等引用已解析到位
        scenario = intent.get("scenario") or self._infer_scenario(plan)
        summary = _make_summary(scenario, plan)

        session.snapshot = core.snapshot()
        session.paused = False
        session.expected_revision = getattr(core,'_revision',0)
        result = self.executor.execute_plan(plan)
        session.expected_revision = getattr(core,'_revision',0)
        session.pending_plan = plan

        pending = result.get("pending")
        reply = self._compose_reply(summary, result)
        session.add("agent", reply)

        viz = self._build_viz(scenario, plan)

        return {
            "session_id": session.session_id,
            "reply": reply,
            "plan": self._plan_view(plan),
            "pending_confirm": self._pending_view(pending, session) if pending else None,
            "viz": viz,
            "security_alert": False,
            "planner_source": planner_source,
        }

    # ------------------------------------------------------------------
    def _clarify(self, session, msg):
        if msg.startswith(('请问','请明确','未找到收款人','未匹配到对应订阅')):
            previous=next((h['text'] for h in reversed(session.history) if h['role']=='user'),None)
            if previous:session.pending_intent={'text':previous}
        session.add("agent", msg)
        return {
            "session_id": session.session_id,
            "reply": f"🤖 {msg}",
            "plan": None,
            "pending_confirm": None,
            "viz": None,
        }

    # ------------------------------------------------------------------
    def confirm(self, session_id, step_id, confirmed, password=None, otp=None, challenge_token=None):
        session = self.mgr.get(session_id)
        if not session or not session.pending_plan:
            return {'error':'会话或计划不存在'}
        if session.locked or session.paused:
            return {'error':'计划已暂停或账户锁定，请人工接管'}
        core=get_core()
        if getattr(core,'_revision',0) != session.expected_revision:
            session.paused=True
            return {'error':'账户状态已变化，计划暂停，请人工接管或重新规划'}
        step=next((s for s in session.pending_plan if s['step_id']==step_id and s['status']=='await_confirm'),None)
        if not step:
            return {'error':'确认已过期、已消费或步骤不匹配'}
        if not confirmed:
            return self.control(session,'取消任务')
        risk,_,_=security.assess_risk(step['operation'],step['params'])
        high = risk=='HIGH' or step['risk']=='HIGH'
        if high:
            c=session.challenge
            digest=hashlib.sha256(json.dumps(step['params'],sort_keys=True).encode()).hexdigest()
            valid=c and c['step_id']==step_id and c['expires']>time.time() and c['digest']==digest and secrets.compare_digest(c['token'],challenge_token or '') and secrets.compare_digest(c['otp'],otp or '') and secrets.compare_digest(password or '',DEMO_TXN_PASSWORD)
            if not valid:
                core._data['auth_failures']=core._data.get('auth_failures',0)+1
                session.failures=core._data['auth_failures']
                session.locked=session.failures>=3
                core._data['security_locked']=session.locked
                core.save()
                security.get_audit().record({'operation':'strong_auth','risk':'HIGH','success':False,'note':'强验证失败','session_id':session_id})
                return {'session_id':session_id,'reply':'强验证失败'+('，连续三次失败已锁定，请人工接管' if session.locked else '，请检查验证码与密码'),
                        'plan':self._plan_view(session.pending_plan),'pending_confirm':None if session.locked else self._pending_view(step,session)}
        session.challenge=None
        core._data['auth_failures']=0
        result=self.executor.execute_plan(session.pending_plan,{step_id:{'strong_verified':high}})
        session.expected_revision=getattr(core,'_revision',0)
        reply=self._compose_reply('',result,continued=True)
        if any(s['status']=='failed' for s in session.pending_plan):
            self._rollback(session)
            reply+='\n沙箱任务已回退，未完成步骤停止。'
        session.add('agent',reply)
        return {'session_id':session_id,'reply':reply,'plan':self._plan_view(session.pending_plan),
                'pending_confirm':self._pending_view(result['pending'],session) if result.get('pending') else None,
                'viz':self._build_viz(self._infer_scenario(session.pending_plan),session.pending_plan),
                'observation':core.observe()}

    def _rollback(self, session):
        core=get_core()
        if session.snapshot is not None and getattr(core,'_revision',0)==getattr(session,'expected_revision',0):
            core.restore(session.snapshot)
            core._revision=getattr(core,'_revision',0)+1
            session.expected_revision=core._revision
            for s in session.pending_plan or []:
                if s['status']=='executed' and s['operation'] in __import__('backend.agent.executor',fromlist=['MUTATIONS']).MUTATIONS:
                    s['status']='rolled_back'
                elif s['status'] in ('pending','await_confirm'):
                    s['status']='cancelled'
            security.get_audit().record({'operation':'sandbox_rollback','risk':'HIGH','success':True,'note':'本地模拟状态恢复，审计保留'})
            return True
        return False

    def control(self, session, command):
        if not session.pending_plan:
            return self._clarify(session,'当前没有可控制的任务')
        if command in ('暂停','中断','人工接管'):
            session.paused=True
            session.challenge=None
            message='任务已暂停。'+('人工接管记录已创建，请操作人员在本地查看审计。未连接外部客服。' if command=='人工接管' else '可输入继续或取消任务')
        elif command=='继续':
            session.paused=False
            message='已恢复任务，请核对原计划并确认'
        else:
            ok=self._rollback(session)
            if not ok:
                session.paused=True
                message='状态已发生变化，无法自动回退，请人工接管'
            else:
                message='已取消任务并回退本地沙箱状态，审计记录保留'
        security.get_audit().record({'operation':'task_control','risk':'LOW','success':True,'note':command})
        pending=next((s for s in session.pending_plan if s['status']=='await_confirm'),None)
        return {'session_id':session.session_id,'reply':message,'plan':self._plan_view(session.pending_plan),
                'pending_confirm':self._pending_view(pending,session) if pending and not session.paused else None,
                'observation':get_core().observe()}

    # ------------------------------------------------------------------
    def _compose_reply(self, summary, result, continued=False):
        msgs = result.get("messages", [])
        prefix = "" if continued else (summary + "\n")
        body = "\n".join(msgs) if msgs else "（无执行消息）"
        note = ""
        if result.get("pending"):
            p = result["pending"]
            if p["risk"] == "HIGH":
                note = "\n该操作需要强验证：请核对详情，输入沙箱交易密码和一次性验证码。"
            else:
                note = "\n✋ 该操作属【中风险】，请确认是否继续。"
        return prefix + body + note

    def _plan_view(self, plan):
        return [{
            "step_id": s["step_id"],
            "operation": s["operation"],
            "description": s["description"],
            "risk": s["risk"],
            "status": s["status"],
            "result": s["result"],
            'params': s['params'], 'depends_on':s.get('depends_on',[]),
            'verified':s.get('observation',{}).get('verified'),
        } for s in plan]

    def _pending_view(self, step, session):
        if not step:
            return None
        if step['risk']=='HIGH':
            digest=hashlib.sha256(json.dumps(step['params'],sort_keys=True).encode()).hexdigest()
            c=session.challenge
            if not c or c['step_id']!=step['step_id'] or c['expires']<=time.time():
                c={'step_id':step['step_id'],'digest':digest,'token':secrets.token_urlsafe(24),
                   'otp':str(secrets.randbelow(900000)+100000),'expires':time.time()+180}
                session.challenge=c
        else:
            c={}
        return {
            'parameters':step['params'], 'challenge_token':c.get('token'),
            'mock_otp':c.get('otp'), 'expires':c.get('expires'), 'identity_mode':'sandbox_simulated_factor',
            "step_id": step["step_id"],
            "operation": step["operation"],
            "description": step["description"],
            "risk": step["risk"],
            "strategy": step["strategy"],
            "risk_reason": step["risk_reason"],
            "need_password": (step["risk"] == "HIGH"),
        }

    def _hist_text(self, session):
        return " | ".join(h["text"] for h in session.history[-6:])

    def _merge_llm(self, base, llm_res):
        # LLM 结果作为「保守补充」：确定性引擎始终为权威，避免 LLM 误判污染流程。
        # - 仅补充确定性引擎漏抽的实体；
        # - 仅在确定性引擎判定为 unknown 时，才采纳 LLM 的场景消歧；
        # - 绝不采纳 LLM 的 need_clarify（澄清由确定性引擎权威决定）。
        ent = llm_res.get("entities", {})
        for k, v in ent.items():
            if v not in (None, "", 0) and k not in base.get("entities", {}):
                base.setdefault("entities", {})[k] = v
        if llm_res.get("scenario") and base.get("scenario") == "unknown":
            base["scenario"] = llm_res["scenario"]
        return base

    # ------------------------------------------------------------------
    # LLM 工具调用规划（主流 Agent 范式）：LLM 选工具 → 转内部计划 → 执行
    def _plan_via_llm(self, text, history):
        if not llm.is_configured():
            return None
        schemas = tool_schemas()
        context = self._grounding_context()
        return llm.plan_with_tools(text, history, schemas, context=context)

    def _grounding_context(self):
        """为 LLM 规划提供只读的接地上下文（账户/卡片/订阅/近期收款人）。"""
        try:
            core = get_core()
            parts = []
            acct = core.get_account()
            if acct:
                parts.append(f"账户：{acct.get('account_id')} 余额约 {acct.get('balance')} 元")
            cards = core.card_list()
            if cards:
                parts.append("我的卡片：" + "，".join(
                    f"{c['card_id']}({c['type']},{c['status']})" for c in cards))
            subs = core.subscription_list()
            if subs:
                parts.append("我的订阅：" + "，".join(
                    f"{s['sub_id']}={s['merchant']}({s['status']})" for s in subs))
            users = core.list_users()
            if users:
                parts.append("可转账联系人：" + "、".join(
                    f"{u['name']}({u.get('remark','')})" for u in users if u['name'] != '我'))
            return "\n".join(parts)
        except Exception:
            return ""

    def _coerce_amount(self, v):
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
            if m:
                return float(m.group(0))
        return 0.0

    def _llm_to_plan(self, steps, intent):
        """将 LLM 的 tool_calls 转为内部计划步骤；含风险分级。返回 (plan, ask_text)。"""
        ask_text = None
        ops = []
        for s in steps or []:
            op = s.get("operation")
            params = s.get("params") or {}
            if op == "__ask__":
                ask_text = (params.get("text") or "请补充信息以便我为您办理。").strip()
                continue
            if op not in TOOL_META:
                return (None, '模型返回未知工具，已拒绝')
            from .executor import MUTATIONS
            from .validation import validate
            if op in MUTATIONS:
                if op not in intent.get('candidates',[]):
                    return (None, '模型写操作与用户明确意图不符，请重新描述')
                e=intent.get('entities',{})
                if 'amount' in params and params['amount'] != e.get('amount'):
                    return (None, '模型金额与用户输入不符，请重新描述')
                if op in ('transfer','schedule_transfer','birthday_gift_flow'):
                    actual=get_core().find_user(params.get('to_key',''))
                    explicit=[u for u in get_core().list_users() if u['name'] in intent.get('_text','') or u.get('remark','!') in intent.get('_text','') or u.get('phone_full','!') in intent.get('_text','')]
                    if len(actual)!=1 or not any(x['user_id']==actual[0]['user_id'] for x in explicit):
                        return (None,'模型收款人与明确授权不符，请重新描述')
                if op in ('subscribe','redeem'):
                    from .planner import _resolve_product
                    if params.get('product_id') != _resolve_product(e.get('product_hint')):
                        return (None,'模型产品与明确授权不符')
                if op in ('report_loss','toggle_lock','adjust_limit','reissue'):
                    matches=[x for x in get_core().card_list() if not e.get('card_type') or x['type']==e['card_type']]
                    if not any(x['card_id']==params.get('card_id') for x in matches):
                        return (None,'模型卡片与授权不符')
            try:
                validate(op,params)
            except ValueError as error:
                return (None,str(error))
            if params.get("amount") not in (None, ""):
                params["amount"] = self._coerce_amount(params["amount"])
            if params.get("new_limit") not in (None, ""):
                params["new_limit"] = self._coerce_amount(params["new_limit"])
            level, strategy, reason = security.assess_risk(op, params)
            ops.append({
                "step_id": f"LLM-{len(ops) + 1}",
                "operation": op,
                "params": params,
                "risk": level,
                "strategy": strategy,
                "risk_reason": reason,
                "description": TOOL_META[op]["desc"],
                "status": "pending",
                "result": None,
            })
        if ask_text:
            return (None, ask_text)
        if not ops:
            return (None, None)
        return (ops, None)

    def _infer_scenario(self, plan):
        scen = {}
        for s in plan or []:
            sc = TOOL_META.get(s.get("operation"), {}).get("scenario")
            if sc:
                scen[sc] = scen.get(sc, 0) + 1
        if not scen:
            return "unknown"
        return max(scen, key=scen.get)

    # ------------------------------------------------------------------
    # 结构化可视化数据：从执行结果抽取供前端绘图的 payload
    def _build_viz(self, scenario, plan):
        results = {s["operation"]: (s.get("result") or {}) for s in (plan or [])}

        def find(*ops):
            for op in ops:
                r = results.get(op)
                if r:
                    return r
            return None

        # 账户总览优先：只要本次执行了余额查询就绘制（余额查询语义上独立于账单）
        bal = find("query_balance")
        if bal:
            return {"type": "account", "balance": bal.get("balance"),
                    "currency": bal.get("currency"), "account_id": bal.get("account_id"),
                    "holder": bal.get("holder")}

        if scenario == "bill":
            ba = find("bill_analysis", "monthly_report")
            an = find("detect_anomaly")
            mr = find("monthly_report")
            viz = {"type": "bill"}
            if ba and ba.get("by_category"):
                viz["period"] = ba.get("period", "month")
                viz["total_expense"] = ba.get("total_expense", 0)
                viz["by_category"] = [{"name": k, "value": v} for k, v in ba["by_category"].items()]
                viz["txn_count"] = ba.get("txn_count", 0)
            if mr:
                viz["income"] = mr.get("income", 0)
                viz["expense"] = mr.get("expense", 0)
                viz["net"] = mr.get("net", 0)
            if an:
                viz["anomaly_count"] = an.get("anomaly_count", 0)
                viz["anomalies"] = [{
                    "counterparty": i.get("counterparty"),
                    "amount": i.get("amount"),
                    "reasons": i.get("reasons", []),
                } for i in an.get("items", [])]
            viz['period'] = find('bill_analysis').get('period','month') if find('bill_analysis') else 'month'
            viz['basis']='自然月' if viz['period']=='month' else '自然年'
            return viz if len(viz) > 1 else None

        if scenario == "wealth":
            wr = find("wealth_recommend")
            if wr:
                rec_ids = {p["product_id"] for p in wr.get("recommended", [])}
                products = [{
                    "name": p.get("name"),
                    "rate": float(p["expected_return"].strip("%")) if p["expected_return"].endswith("%") else None,
                    "risk": p.get("risk_level"),
                    "term": p.get("term"),
                    "min_amount": p.get("min_amount"),
                    "recommended": p.get("product_id") in rec_ids,
                } for p in wr.get("all", [])]
                return {"type": "wealth", "risk_pref": wr.get("risk_pref"), "products": products}

        if find('finalize_birthday'):
            return {'type':'cross',**find('finalize_birthday')}
        if scenario in ("transfer", "cross"):
            tr = find("transfer", "birthday_gift_flow", "schedule_transfer")
            if tr and tr.get("ok"):
                return {
                    "type": "transfer",
                    "to": tr.get("to"),
                    "amount": tr.get("amount_cny") or tr.get("amount"),
                    "currency": tr.get("currency", "CNY"),
                    "balance": tr.get("balance"),
                    "status": "已预约" if "task_id" in tr else "成功",
                }

        if scenario == "card":
            cl = find("card_list")
            cards = get_core().card_list()
            last_op = None
            for s in (plan or []):
                res = s.get("result") or {}
                if s["operation"] in ("card_apply", "adjust_limit", "toggle_lock", "report_loss", "reissue") \
                        and res.get("ok"):
                    last_op = {"op": s["operation"], "result": res}
                    break
            return {"type": "card", "cards": cards, "last_op": last_op}

        if scenario == "subscription":
            sl = find("subscription_list")
            subs = get_core().subscription_list()
            return {"type": "subscription", "subscriptions": subs}

        if scenario == "order":
            ol = find("order_list")
            orders = get_core().order_list()
            return {"type": "order", "orders": orders}

        if scenario == "new_customer":
            ng = find("new_customer_guidance")
            if ng:
                if ng.get("nodes"):
                    return {"type": "new_customer", "activity": ng.get("activity"),
                            "budget": ng.get("budget"), "nodes": ng.get("nodes"),
                            "granted": ng.get("granted", [])}
                return {"type": "new_customer", "node": ng.get("node"),
                        "granted_nodes": ng.get("granted_nodes", [])}

        # 默认：账户总览
        bal = find("query_balance")
        if bal:
            return {"type": "account", "balance": bal.get("balance"),
                    "currency": bal.get("currency"), "account_id": bal.get("account_id")}
        return None


_agent = BankingAgent()


def get_agent():
    return _agent
