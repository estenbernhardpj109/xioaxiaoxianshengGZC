"""Source-backed bank operations. Simulated branches and devices are explicit fixtures.

Actual application requests and measured local service health use separate source tags.
No action in this module can execute a financial transfer or bypass the Agent policy.
"""
import hashlib
import math
import os
import random
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from .privacy import alias, redact
from .storage import dumps, get_store

TZ = timezone(timedelta(hours=8))
BRANCHES = [('B01','深圳营业部'),('B02','南山科技园'),('B03','福田中心'),('B04','前海合作区'),
            ('B05','罗湖口岸'),('B06','宝安新安'),('B07','龙华民治'),('B08','线上服务中心')]
SCENARIOS = {'transfer':'转账助手','bill':'账单分析师','wealth':'理财顾问','card':'卡片管家',
             'subscription':'订阅管家','cross':'生活服务助手'}
REPORT_SCENARIOS = SCENARIOS | {'general':'通用助理'}
STATUS = {'NEW':'待分派','ASSIGNED':'已分派','IN_PROGRESS':'处理中','RESOLVED':'已解决'}
STARTED = time.time()
SIMULATOR = os.environ.get('OPS_SIMULATOR','0') == '1'
RECOVERED = {}
TICK = 0


def create_ticket(cur, source_key, branch_id, title, priority='P2', device_id=None, ref_id=None):
    if device_id:
        cur.execute("SELECT ticket_id FROM zx_work_orders WHERE device_id=%s AND status<>'RESOLVED' ORDER BY created_at LIMIT 1 FOR UPDATE",(device_id,))
        existing=cur.fetchone()
        if existing:return existing['ticket_id']
    ticket = 'WO-' + hashlib.sha256(source_key.encode()).hexdigest()[:10].upper()
    now = time.time()
    cur.execute('INSERT IGNORE INTO zx_work_orders (ticket_id,source_key,device_id,branch_id,title,priority,status,created_at,sla_minutes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (ticket, source_key, device_id, branch_id, title, priority, 'NEW', now, 30 if priority=='P1' else 120))
    if cur.rowcount:
        cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                    (now,'ticket_created',branch_id,ticket,'SYSTEM',dumps({'title':title,'linked_ref':ref_id,'device_id':device_id})))
    return ticket


def seed(store):
    if store.query('SELECT COUNT(*) AS n FROM zx_devices')[0]['n']:
        # Correct only this project's known fixture rows, once. Never rewrites real requests.
        if not store.query('SELECT version FROM zx_schema_version WHERE version=3'):
            with store.transaction() as cur:
                for i in range(420):
                    rid=f'FIXTURE-{i:05d}'
                    bid=BRANCHES[int(hashlib.sha256((rid+'branch').encode()).hexdigest()[:8],16)%8][0]
                    sc=list(SCENARIOS)[int(hashlib.sha256((rid+'scenario').encode()).hexdigest()[:8],16)%6]
                    cur.execute("UPDATE zx_requests SET branch_id=%s,scenario=%s WHERE request_id=%s AND source='synthetic'",(bid,sc,rid))
                cur.execute('INSERT INTO zx_schema_version VALUES (3,%s)',(time.time(),))
        return
    rng = random.Random(20261101)
    now = time.time()
    kinds = ['ATM自助终端','智能柜台','网络网关','自助打印','视频终端','边缘节点']
    with store.transaction() as cur:
        for bi,(bid,_) in enumerate(BRANCHES):
            for di,kind in enumerate(kinds):
                did = f'DEV-{bi+1:02d}{di+1:02d}'
                status = 'warning' if (bi+di)%13==0 else 'online'
                cur.execute('INSERT INTO zx_devices VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (did,bid,kind+' '+str(di+1).zfill(2),kind,'synthetic',status,now,20+bi*3,12+di*7,28+bi*2))
                if status != 'online':
                    create_ticket(cur,'device:'+did+':initial',bid,'设备运行指标需要复核','P2',did)
        for i in range(420):
            ts = now-rng.uniform(60,7*86400)
            sc = list(SCENARIOS)[int(hashlib.sha256((f'FIXTURE-{i:05d}'+'scenario').encode()).hexdigest()[:8],16)%6]
            status = rng.choices(['success','await_confirm','failed'],[90,7,3])[0]
            cur.execute('INSERT INTO zx_requests VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                        (f'FIXTURE-{i:05d}',f'客户演练{i%40:03d}','synthetic',sc,status,rng.uniform(110,1400),ts,BRANCHES[int(hashlib.sha256((f'FIXTURE-{i:05d}'+'branch').encode()).hexdigest()[:8],16)%8][0]))
        for i in range(64):
            created = now-rng.uniform(900,7*86400)
            status = rng.choices(list(STATUS),[12,10,15,63])[0]
            priority = 'P1' if i%7==0 else 'P2'
            assigned = created+180 if status!='NEW' else None
            resolved = created+rng.uniform(600,4000) if status=='RESOLVED' else None
            if resolved and resolved>now:resolved=now-30
            cur.execute('INSERT INTO zx_work_orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (f'WO-H{i:04d}',f'fixture:{i}',None,BRANCHES[i%8][0],['业务回执核对','设备巡检复核','AI任务人工接管','渠道响应检查'][i%4],priority,status,created,assigned,resolved,30 if priority=='P1' else 120,'MGR-01' if assigned else None,'模拟处理记录' if resolved else None,1))
        cur.execute('INSERT IGNORE INTO zx_schema_version VALUES (3,%s)',(now,))


def scenario_from_response(out):
    ops = {s.get('operation') for s in (out.get('plan') or [])}
    if ops & {'reserve_budget','place_gift_order','finalize_birthday'}:return 'cross'
    if ops & {'transfer','schedule_transfer','query_balance','query_transactions'}:return 'transfer'
    if ops & {'bill_analysis','detect_anomaly','monthly_report'}:return 'bill'
    if ops & {'wealth_recommend','wealth_list','risk_assess','subscribe','redeem','get_holdings'}:return 'wealth'
    if ops & {'card_list','card_apply','adjust_limit','toggle_lock','report_loss','reissue'}:return 'card'
    if ops & {'subscription_list','cancel_subscription'}:return 'subscription'
    return 'general'


def request_record(out, duration_ms, branch_id='B01'):
    failed = 'error' in out or bool(out.get('verification_failed')) or any(s.get('status')=='failed' for s in (out.get('plan') or []))
    state = 'failed' if failed else 'await_confirm' if out.get('pending_confirm') else 'success'
    source = out.get('planner_source','rule')
    if source in ('deepseek_tool_call','knowledge_llm_router'):source='llm'
    if source not in ('llm','rule','rule_fallback'):source='rule'
    return ('REQ-'+uuid.uuid4().hex,alias(out.get('session_id','unknown')),source,
            scenario_from_response(out),state,round(duration_ms,2),time.time(),branch_id)


def collect(store):
    """One measured heartbeat; simulation is backend-only and persisted before display."""
    global TICK
    TICK += 1
    now = time.time()
    start = time.perf_counter()
    store.query('SELECT 1')
    db_ms = (time.perf_counter()-start)*1000
    import psutil
    cpu, mem = psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    with store.transaction() as cur:
        for did,name,lat in [('SERVICE-API','银行业务服务',None),('SERVICE-DB','MySQL数据服务',db_ms)]:
            cur.execute('INSERT INTO zx_devices VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=VALUES(status),last_seen=VALUES(last_seen),latency_ms=VALUES(latency_ms),cpu_pct=VALUES(cpu_pct),memory_pct=VALUES(memory_pct)',
                        (did,'B08',name,'服务节点','measured','online',now,round(lat,2) if lat is not None else None,cpu,mem))
        cur.execute("SELECT * FROM zx_devices WHERE source='synthetic'")
        devices = cur.fetchall()
        for i,d in enumerate(devices):
            # Deterministic synthetic signal, explicitly tagged as synthetic in SQL and UI.
            latency = round(24+abs(math.sin(TICK/6+i))*36,2)
            status = d['status']
            if SIMULATOR and TICK%12==0 and i==(TICK//12)%len(devices) and RECOVERED.get(d['device_id'],0)<now:
                status='warning'
                create_ticket(cur,'device:'+d['device_id']+':'+str(int(now)),d['branch_id'],'设备响应超过演练阈值','P2',d['device_id'])
            if RECOVERED.get(d['device_id'],0)>now:status='online'
            cur.execute('UPDATE zx_devices SET status=%s,last_seen=%s,latency_ms=%s,cpu_pct=%s,memory_pct=%s WHERE device_id=%s',
                        (status,now,latency,round(22+14*abs(math.sin(TICK/9+i)),1),round(34+12*abs(math.cos(TICK/11+i)),1),d['device_id']))
        if SIMULATOR:
            i=int(hashlib.sha256(f'{TICK}-scenario'.encode()).hexdigest()[:8],16)%6
            bid=BRANCHES[int(hashlib.sha256(f'{TICK}-branch'.encode()).hexdigest()[:8],16)%8][0]
            r=('SIM-'+uuid.uuid4().hex,'演练会话','synthetic',list(SCENARIOS)[i],'success',round(180+abs(math.sin(TICK))*650,2),now,bid)
            cur.execute('INSERT INTO zx_requests VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',r)
            if TICK%5==0:create_ticket(cur,'sim:'+str(TICK)+':'+str(int(STARTED)),bid,'演练任务需要人工复核','P2')
        cur.execute('INSERT INTO zx_device_samples(device_id,event_ts,status,latency_ms,cpu_pct,memory_pct) SELECT device_id,%s,status,latency_ms,cpu_pct,memory_pct FROM zx_devices',(now,))
        # Bounded monitoring retention. Business state, transactions and audit are untouched.
        cur.execute('DELETE FROM zx_device_samples WHERE event_ts<%s',(now-86400,))


def scope(branch, period):
    if branch not in {'all'}|{b[0] for b in BRANCHES}:raise ValueError('未知网点')
    if period not in ('today','7d','30d'):raise ValueError('未知时间范围')
    now = datetime.now(TZ)
    days = {'today':0,'7d':6,'30d':29}[period]
    lo = (now.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days)).timestamp()
    return lo,time.time()


def snapshot(store, branch='all', period='7d'):
    lo,now=scope(branch,period)
    where=' AND branch_id=%s' if branch!='all' else ''
    args=(lo,now,branch) if branch!='all' else (lo,now)
    with store.transaction() as cur:
        def rows(sql,params=()):
            cur.execute(sql,params)
            return cur.fetchall()
        tx=rows('SELECT * FROM zx_transactions WHERE event_ts BETWEEN %s AND %s'+where,args)
        requests=rows('SELECT * FROM zx_requests WHERE event_ts BETWEEN %s AND %s'+where,args)
        tickets=rows("SELECT w.*,a.operation AS linked_operation FROM zx_work_orders w LEFT JOIN zx_audit_log a ON w.source_key=CONCAT('audit:',a.hash) WHERE w.created_at BETWEEN %s AND %s"+where,args)
        devices=rows('SELECT * FROM zx_devices'+(' WHERE branch_id=%s' if branch!='all' else ''),(branch,) if branch!='all' else ())
        events=rows('SELECT event_ts,event_type,branch_id,ref_id,actor,payload FROM zx_ops_events'+(' WHERE branch_id=%s' if branch!='all' else '')+' ORDER BY event_id DESC LIMIT 12',(branch,) if branch!='all' else ())
        recent=rows('SELECT COUNT(*) n,MAX(event_ts) ts FROM zx_device_samples')
        version=rows('SELECT version,updated_at FROM zx_bank_state WHERE id=1')[0]
    for d in devices:
        if now-d['last_seen']>20:d['status']='stale'
    # Explicit DTO allowlists: no core snapshot, account number, IP, phone, prompts, or audit params.
    ticket_dto=[]
    for t in tickets:
        age=((t['resolved_at'] or now)-t['created_at'])/60
        ticket_dto.append({k:t[k] for k in ('ticket_id','device_id','branch_id','title','priority','status','created_at','assigned_at','resolved_at','sla_minutes','assignee','version')} |
                          {'overdue':age>t['sla_minutes'],'elapsed_minutes':round(age,1),'source':'synthetic' if t['source_key'].startswith(('fixture:','sim:','device:')) else 'application',
                           'linked_operation':t.get('linked_operation'),'business_reference':'AUD-'+t['source_key'][6:18].upper() if t['source_key'].startswith('audit:') else None})
    device_dto=[{k:d[k] for k in ('device_id','branch_id','name','kind','source','status','last_seen','latency_ms','cpu_pct','memory_pct')} for d in devices]
    completed=[t for t in ticket_dto if t['status']=='RESOLVED']
    waiting=[t for t in ticket_dto if t['status']!='RESOLVED']
    success=[r for r in requests if r['status']=='success']
    durations=sorted(r['duration_ms'] for r in requests)
    p95=round(durations[max(0,math.ceil(len(durations)*.95)-1)],1) if durations else None
    # Same branch/period for all historical evidence; device state remains a current snapshot.
    daily=defaultdict(lambda:{'count':0,'amount':0.0})
    for t in tx:
        key=datetime.fromtimestamp(t['event_ts'],TZ).strftime('%m-%d')
        daily[key]['count']+=1;daily[key]['amount']+=float(t['amount'])
    trend=[]
    for i in range(int((datetime.fromtimestamp(now,TZ).date()-datetime.fromtimestamp(lo,TZ).date()).days)+1):
        key=(datetime.fromtimestamp(lo,TZ)+timedelta(days=i)).strftime('%m-%d')
        trend.append({'day':key,'count':daily[key]['count'],'amount':round(daily[key]['amount'],2)})
    branch_rows=[]
    for bid,name in BRANCHES:
        if branch!='all' and branch!=bid:continue
        br=[r for r in requests if r['branch_id']==bid]
        branch_rows.append({'id':bid,'name':name,'requests':len(br),'transactions':sum(t['branch_id']==bid for t in tx),'backlog':sum(t['branch_id']==bid for t in waiting),'success_rate':round(100*sum(r['status']=='success' for r in br)/len(br),1) if br else None})
    employees=[]
    for sc,label in REPORT_SCENARIOS.items():
        rr=[r for r in requests if r['scenario']==sc]
        employees.append({'id':sc,'name':label,'requests':len(rr),'success':sum(r['status']=='success' for r in rr),'pending':sum(r['status']=='await_confirm' for r in rr),'failed':sum(r['status']=='failed' for r in rr),'success_rate':round(100*sum(r['status']=='success' for r in rr)/len(rr),1) if rr else None})
    source_names={'synthetic':'演练请求','rule':'规则规划','rule_fallback':'规则回退','llm':'模型规划'}
    state_names={'success':'已完成','await_confirm':'待确认','failed':'需人工复核'}
    flow_counts=Counter()
    for r in requests:
        employee=REPORT_SCENARIOS.get(r['scenario'],'通用助理')
        flow_counts[(source_names.get(r['source'],'规则规划'),employee)]+=1
        flow_counts[(employee,state_names.get(r['status'],'需人工复核'))]+=1
    buckets=[]
    hour=int(now//3600)*3600
    for i in range(23,-1,-1):
        ts=hour-i*3600;rr=[r for r in requests if ts<=r['event_ts']<ts+3600]
        buckets.append({'hour':datetime.fromtimestamp(ts,TZ).strftime('%H:%M'),'requests':len(rr),'latency_ms':round(sum(r['duration_ms'] for r in rr)/len(rr),1) if rr else None})
    result={'meta':{'generated_at':now,'database':'MySQL','state_version':version['version'],'state_updated_at':version['updated_at'],'refresh_seconds':5,'branch':branch,'period':period,'from':lo,'to':now,'timezone':'Asia/Shanghai','source_kind':'synthetic banking data + measured local service health','simulator':SIMULATOR,'collector_last_seen':recent[0]['ts'],'privacy':'server DTO allowlist; no customer identifiers or secrets'},
            'kpis':{'transactions':len(tx),'turnover':round(sum(float(t['amount']) for t in tx),2),'backlog':len(waiting),'overdue':sum(t['overdue'] for t in waiting),'sla_rate':round(100*sum(not t['overdue'] for t in completed)/len(completed),1) if completed else None,'resolved':len(completed),'mean_resolution_minutes':round(sum(t['elapsed_minutes'] for t in completed)/len(completed),1) if completed else None,'devices_online':sum(d['status']=='online' for d in devices),'devices_total':len(devices),'device_rate':round(100*sum(d['status']=='online' for d in devices)/len(devices),1) if devices else None,'ai_requests':len(requests),'ai_success_rate':round(100*len(success)/len(requests),1) if requests else None,'p95_ms':p95,'actual_requests':sum(r['source']!='synthetic' for r in requests)},
            'trend':trend,'branches':branch_rows,'employees':employees,'hourly':buckets,
            'flow_links':[{'source':a,'target':b,'value':n} for (a,b),n in flow_counts.items()],
            'ticket_statuses':dict(Counter(t['status'] for t in tickets)),
            'tickets':sorted(ticket_dto,key=lambda t:(t['status']=='RESOLVED',not t['overdue'],t['priority'],t['created_at'])),
            'devices':device_dto,'device_statuses':dict(Counter(d['status'] for d in devices)),
            'request_sources':dict(Counter(r['source'] for r in requests)),
            'events':[{k:e[k] for k in ('event_ts','event_type','branch_id','ref_id','actor')} for e in events],
            'definitions':{'turnover':'所选期间全部入账与出账流水金额之和，不是净收入或利润','sla_rate':'所选期间创建且已解决、处理总时长不超过该工单SLA的数量 / 该期间创建且已解决数量；未解决单独列超时','device_rate':'当前快照状态正常且20秒内有心跳的设备数 / 当前所选网点设备总数','ai_success_rate':'成功请求数 / 全部请求数（含待确认、失败）；统计交互请求而非唯一客户或唯一业务任务','p95_ms':'所选请求时长升序排列的第ceil(0.95*N)项；包含演练请求时长，非真实模型推理基准','ticket_scope':'时间筛选按工单创建时间，处理时间是创建到解决的总时长；SLA为本项目演练策略P1 30分钟、P2 120分钟','branch_mapping':'用户U1001至U1004分别归B01至B04，其他归线上；为演示组织映射'},
            'lineage':{'banking':['zx_transactions','zx_accounts','zx_resources'],'tickets':['zx_work_orders','zx_ops_events'],'devices':['zx_devices','zx_device_samples'],'ai':['zx_requests'],'consistency':'single MySQL repeatable-read transaction','history':'420条固定种子演练请求、64条演练工单；数字员工统计包含演练与实际应用请求，来源分开计数'}}
    return normalize(result)


def normalize(obj):
    if isinstance(obj,Decimal):return float(obj)
    if isinstance(obj,dict):return {k:normalize(v) for k,v in obj.items()}
    if isinstance(obj,list):return [normalize(v) for v in obj]
    return obj


def action(store, body, actor='MGR-01'):
    """Version-checked work order state machine. Manager role is enforced by HTTP layer."""
    global SIMULATOR
    op=body.get('action')
    if op=='simulator':
        if type(body.get('enabled')) is not bool:raise ValueError('enabled必须为布尔值')
        with store.transaction() as cur:
            cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',(time.time(),'simulator_changed','B08',None,actor,dumps({'enabled':body['enabled']})))
        SIMULATOR=body['enabled']
        return {'ok':True,'message':'演练事件流已'+('开启' if SIMULATOR else '暂停')}
    ticket_id=body.get('ticket_id');version=body.get('version')
    if not isinstance(ticket_id,str) or len(ticket_id)>48 or type(version) is not int:raise ValueError('工单或版本无效')
    now=time.time();recovery_id=None
    with store.transaction() as cur:
        cur.execute('SELECT * FROM zx_work_orders WHERE ticket_id=%s FOR UPDATE',(ticket_id,));ticket=cur.fetchone()
        if not ticket:raise ValueError('工单不存在')
        if ticket['version']!=version:raise RuntimeError('工单已被其他操作更新，请刷新后重试')
        allowed={'assign':('NEW','ASSIGNED'),'start':('ASSIGNED','IN_PROGRESS'),'resolve':('IN_PROGRESS','RESOLVED')}
        if op=='recover_device':
            if not ticket['device_id'] or ticket['status']=='RESOLVED':raise ValueError('当前工单没有待恢复的设备')
            cur.execute('SELECT source FROM zx_devices WHERE device_id=%s FOR UPDATE',(ticket['device_id'],));device=cur.fetchone()
            if not device or device['source']!='synthetic':raise ValueError('只允许恢复演练设备，真实设备不可由此操作')
            cur.execute("UPDATE zx_devices SET status='online',last_seen=%s WHERE device_id=%s",(now,ticket['device_id']))
            cur.execute('UPDATE zx_work_orders SET version=version+1 WHERE ticket_id=%s',(ticket_id,))
            recovery_id=ticket['device_id']
        else:
            if op not in allowed or ticket['status']!=allowed[op][0]:raise ValueError('不允许的工单状态变更')
            resolution=None
            if op=='resolve':
                note=body.get('resolution','')
                if not isinstance(note,str) or not 6<=len(note.strip())<=200:raise ValueError('请输入6至200字处理说明')
                from .bank.core import get_core
                resolution=redact(note)
                for u in get_core().list_users():resolution=resolution.replace(u['name'],'[客户]')
                if ticket['device_id']:
                    cur.execute('SELECT status,last_seen FROM zx_devices WHERE device_id=%s',(ticket['device_id'],));d=cur.fetchone()
                    if not d or d['status']!='online' or now-d['last_seen']>20:raise ValueError('设备尚未恢复正常心跳，请先处理设备')
            cur.execute('UPDATE zx_work_orders SET status=%s,assignee=%s,assigned_at=COALESCE(assigned_at,%s),resolved_at=%s,resolution=%s,version=version+1 WHERE ticket_id=%s AND version=%s',
                        (allowed[op][1],actor,now,now if op=='resolve' else None,resolution,ticket_id,version))
        cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                    (now,op,ticket['branch_id'],ticket_id,actor,dumps({'previous_status':ticket['status'],'device_id':ticket['device_id']})))
    if recovery_id:RECOVERED[recovery_id]=now+300
    return {'ok':True,'message':'操作完成，设备、工单和审计已关联更新'}
