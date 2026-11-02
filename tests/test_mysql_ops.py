"""Real MySQL integration checks in a uniquely named, disposable test schema.

MYSQL_PASSWORD must be supplied via environment. Never touches the root demo schema.
"""
import copy
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if not os.environ.get('MYSQL_PASSWORD'):
    raise SystemExit('Set MYSQL_PASSWORD before running MySQL integration tests')
database='zx_test_'+uuid.uuid4().hex[:12]
os.environ.update(BANK_STORAGE='mysql',MYSQL_DATABASE=database,OPS_SIMULATOR='0')
checks=[]


def check(name,value):
    checks.append({'name':name,'pass':bool(value)})
    if not value:raise AssertionError(name)


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


with tempfile.TemporaryDirectory() as td:
    os.environ['BANK_AUDIT_FILE']=str(Path(td)/'audit.jsonl')
    os.environ['BANK_STATE_FILE']=str(Path(td)/'state.json')
    from backend.storage import get_store,unit_of_work
    from backend.bank.core import get_core
    from backend.agent.security import get_audit
    from backend.agent.agent import get_agent
    from backend import operations
    store=get_store();core=get_core();audit=get_audit();agent=get_agent()
    proc=None
    try:
        store.initialize(core,audit);operations.seed(store);operations.collect(store)
        tables=store.query('SHOW TABLES')
        check('fifteen project tables created in isolated schema',len(tables)==15)
        base=core.get_account()['balance'];tx0=len(core._data['transactions']);audit0=len(audit._mem)
        try:
            with unit_of_work(core,audit,agent.mgr):
                core.transfer('李婷',25)
                audit.record({'operation':'forced_failure','risk':'HIGH','success':False})
                raise RuntimeError('injected before SQL commit')
        except RuntimeError:pass
        check('exception restores memory and SQL state',core.get_account()['balance']==base and float(store.query("SELECT balance FROM zx_accounts WHERE user_id='U1001'")[0]['balance'])==base and len(audit._mem)==audit0)
        with unit_of_work(core,audit,agent.mgr) as requests:
            result=agent.handle('给李婷转42元');requests.append(operations.request_record(result,12))
        pending=result['pending_confirm']
        with unit_of_work(core,audit,agent.mgr) as requests:
            result=agent.confirm(result['session_id'],pending['step_id'],True);requests.append(operations.request_record(result,10))
        check('confirmed action commits account transaction audit and request together',core.get_account()['balance']==base-42 and len(core._data['transactions'])==tx0+1 and store.query('SELECT COUNT(*) n FROM zx_requests WHERE source<>%s',('synthetic',))[0]['n']==2 and bool(store.query("SELECT * FROM zx_audit_log WHERE operation='transfer'")))
        before=copy.deepcopy(core._data);before_sessions=copy.deepcopy(agent.mgr._sessions)
        try:
            with patch.object(store,'projections',side_effect=RuntimeError('injected SQL write failure')):
                with unit_of_work(core,audit,agent.mgr):
                    agent.handle('查余额')
                    core.transfer('李婷',5)
        except RuntimeError:pass
        check('SQL write failure rolls back memory sessions audit and transaction',core._data==before and set(agent.mgr._sessions)==set(before_sessions) and float(store.query("SELECT balance FROM zx_accounts WHERE user_id='U1001'")[0]['balance'])==base-42)
        actual_version=store.version;store.version-=1
        try:
            with unit_of_work(core,audit):core.transfer('李婷',1)
        except RuntimeError:pass
        finally:store.version=actual_version
        check('optimistic state version refuses stale overwrite',core._data==before)
        try:
            with patch.object(store.pool,'get_connection',side_effect=OSError('database unavailable')):
                with unit_of_work(core,audit,agent.mgr):core.transfer('李婷',3)
        except OSError:pass
        check('connection outage fails closed without file fallback',core._data==before and not Path(os.environ['BANK_STATE_FILE']).exists())
        check('model source is correctly attributed',operations.request_record({'planner_source':'deepseek_tool_call','plan':[]},1)[2]=='llm' and operations.request_record({'planner_source':'knowledge_llm_router','plan':None},1)[2]=='llm')
        check('clarification is not falsely classified as bill analysis',operations.request_record({'plan':[]},1)[3]=='general')
        for op in ('same_time_one','same_time_two'):
            audit.record({'operation':op,'ts':time.time()//10*10,'risk':'LOW','success':True})
        audit.record({'operation':'precision_probe','ts':1700000000.1234567,'risk':'LOW','success':True,'params':{'integer_float':1000.0,'precise':1.0000000000000002}})
        precision=json.loads(store.query("SELECT payload FROM zx_audit_log WHERE operation='precision_probe'")[0]['payload'])
        check('audit preserves exact float serialization',type(precision['params']['integer_float']) is float and precision['params']['precise']==1.0000000000000002)
        arows=store.query('SELECT payload FROM zx_audit_log ORDER BY sequence_id')
        previous='0'*64;chain=True
        for row in arows:
            entry=json.loads(row['payload']);h=entry.pop('hash')
            valid_prev=entry['prev_hash']==previous
            valid_hash=hashlib.sha256(json.dumps(entry,sort_keys=True,ensure_ascii=False).encode()).hexdigest()==h
            if not valid_prev or not valid_hash:
                original=next((a for a in audit._mem if a['hash']==h),{})
                print('Audit diagnostic:',entry['operation'],'parent',valid_prev,'hash',valid_hash,'stored_ts',type(entry['ts']).__name__,'original_ts',type(original.get('ts')).__name__,file=sys.stderr)
            chain=chain and valid_prev and valid_hash;previous=h
        check('audit chain persists insertion order even at identical timestamps',chain)
        original_row=store.query('SELECT hash,payload FROM zx_audit_log ORDER BY sequence_id LIMIT 1')[0]
        with store.transaction() as cur:cur.execute('UPDATE zx_audit_log SET payload=%s WHERE hash=%s',('{}',original_row['hash']))
        tamper_rejected=False
        try:store.initialize(core,audit)
        except RuntimeError:tamper_rejected=True
        finally:
            with store.transaction() as cur:cur.execute('UPDATE zx_audit_log SET payload=%s WHERE hash=%s',(original_row['payload'],original_row['hash']))
        check('future audit corruption is rejected rather than silently archived',tamper_rejected and store.query("SELECT COUNT(*) n FROM zx_ops_events WHERE event_type='audit_legacy_archive'")[0]['n']==0)
        snap=operations.snapshot(store)
        check('employee branch and source totals reconcile',sum(e['requests'] for e in snap['employees'])==snap['kpis']['ai_requests']==sum(snap['request_sources'].values())==sum(b['requests'] for b in snap['branches']))
        check('transaction chart reconciles to KPI',sum(d['count'] for d in snap['trend'])==snap['kpis']['transactions'] and abs(sum(d['amount'] for d in snap['trend'])-snap['kpis']['turnover'])<.01)
        resolved=[t for t in snap['tickets'] if t['status']=='RESOLVED']
        expected=round(100*sum(not t['overdue'] for t in resolved)/len(resolved),1)
        check('SLA uses resolved-ticket denominator',snap['kpis']['sla_rate']==expected)
        branch=operations.snapshot(store,'B02','7d')
        check('branch filter applies consistently to queue devices and metrics',all(t['branch_id']=='B02' for t in branch['tickets']) and all(d['branch_id']=='B02' for d in branch['devices']) and len(branch['branches'])==1 and sum(e['requests'] for e in branch['employees'])==branch['kpis']['ai_requests'])
        raw=json.dumps(snap,ensure_ascii=False)
        check('operational DTO excludes raw identities and credentials',all(x not in raw for x in ['13900008830','张明','李婷','account_no','phone_full','MYSQL_PASSWORD','challenge_token','session_id']))
        ticket=next(t for t in snap['tickets'] if t['device_id'] and t['status']=='NEW')
        tid=ticket['ticket_id'];v=ticket['version']
        with store.transaction() as cur:
            repeated=operations.create_ticket(cur,'another-sample-same-fault',ticket['branch_id'],'重复心跳预警','P2',ticket['device_id'])
        assert repeated==tid,'An active device incident must not create duplicate work orders'
        operations.action(store,{'action':'assign','ticket_id':tid,'version':v})
        stale_rejected=False
        try:operations.action(store,{'action':'start','ticket_id':tid,'version':v})
        except RuntimeError:stale_rejected=True
        check('work-order optimistic lock rejects stale double action',stale_rejected)
        operations.action(store,{'action':'start','ticket_id':tid,'version':v+1})
        blocked=False
        try:operations.action(store,{'action':'resolve','ticket_id':tid,'version':v+2,'resolution':'设备已经检查并完成演练修复'})
        except ValueError:blocked=True
        check('cannot resolve device ticket without healthy heartbeat',blocked)
        operations.action(store,{'action':'recover_device','ticket_id':tid,'version':v+2})
        operations.action(store,{'action':'resolve','ticket_id':tid,'version':v+3,'resolution':'已复核李婷13900008830设备状态并完成修复'})
        row=store.query('SELECT * FROM zx_work_orders WHERE ticket_id=%s',(tid,))[0]
        check('device recovery resolves linked ticket with redacted comment',row['status']=='RESOLVED' and '13900008830' not in row['resolution'] and '李婷' not in row['resolution'])
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();url=f'http://127.0.0.1:{port}'
        pin=uuid.uuid4().hex
        env=dict(os.environ,OPS_MANAGER_PASSWORD=pin)
        def start():
            return subprocess.Popen([sys.executable,str(ROOT/'backend/server.py'),'--port',str(port),'--storage','mysql'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def req(c,path,body=None,csrf=None):
            headers={'Content-Type':'application/json'}
            if csrf:headers['X-Bank-CSRF']=csrf
            q=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with c.open(q,timeout=8) as r:return r.status,json.loads(r.read())
            except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
        a,b=client(),client()
        def ready():
            for _ in range(100):
                try:return req(a,'/api/health')[1]
                except OSError:time.sleep(.1)
            raise RuntimeError('test server did not start')
        proc=start();health=ready();csrf=health['csrf']
        check('HTTP actually runs MySQL backend',health['storage']=='mysql')
        check('anonymous ops read requires browser session',req(client(),'/api/ops/snapshot')[0]==401)
        csrf=req(a,'/api/auth/demo',{},csrf)[1]['csrf']
        check('leader cannot mutate operational data',req(a,'/api/ops/action',{'action':'simulator','enabled':True},csrf)[0]==403)
        check('client-supplied role does not grant access',req(a,'/api/ops/action',{'action':'simulator','enabled':True,'role':'manager'},csrf)[0]==403)
        check('invalid manager password refused',req(a,'/api/ops/login',{'password':'wrong'},csrf)[0]==403)
        check('manager login verified server-side',req(a,'/api/ops/login',{'password':pin},csrf)[0]==200)
        check('ops writes still require CSRF',req(a,'/api/ops/action',{'action':'simulator','enabled':True})[0]==403)
        h2=req(b,'/api/health')[1]
        h2['csrf']=req(b,'/api/auth/demo',{},h2['csrf'])[1]['csrf']
        mixed=client()
        jar_a=next(h.cookiejar for h in a.handlers if isinstance(h,urllib.request.HTTPCookieProcessor))
        jar_b=next(h.cookiejar for h in b.handlers if isinstance(h,urllib.request.HTTPCookieProcessor))
        jar_mixed=next(h.cookiejar for h in mixed.handlers if isinstance(h,urllib.request.HTTPCookieProcessor))
        for cookie in jar_a:
            if cookie.name=='bank_client':jar_mixed.set_cookie(copy.copy(cookie))
        for cookie in jar_b:
            if cookie.name=='bank_auth':jar_mixed.set_cookie(copy.copy(cookie))
        check('manager elevation cannot cross browser or authentication-session boundaries',req(b,'/api/ops/action',{'action':'simulator','enabled':True},h2['csrf'])[0]==403 and req(mixed,'/api/ops/action',{'action':'simulator','enabled':True},csrf)[0]==403)
        check('unknown filter and SQL injection input rejected',req(a,'/api/ops/snapshot?branch=B01%27%3BDELETE%20FROM%20zx_requests%3B--')[0]==400 and req(a,'/api/ops/snapshot?period=year')[0]==400)
        _,result=req(a,'/api/chat',{'text':'给李婷转10元'},csrf);pending=result['pending_confirm']
        status,result=req(a,'/api/confirm',{'session_id':result['session_id'],'step_id':pending['step_id'],'confirmed':True},csrf)
        check('MySQL HTTP action changes bank balance and database projection',status==200 and result['observation']['balance']==base-52 and float(store.query("SELECT balance FROM zx_accounts WHERE user_id='U1001'")[0]['balance'])==base-52)
        _,result=req(a,'/api/chat',{'text':'给李婷转20元'},csrf)
        req(a,'/api/chat',{'text':'人工接管','session_id':result['session_id']},csrf)
        linked=req(a,'/api/ops/snapshot')[1]['tickets']
        check('human handoff audit creates linked operational work order',any(t['business_reference'] and t['linked_operation']=='task_control' for t in linked))
        before_live=req(a,'/api/ops/snapshot')[1]
        req(a,'/api/ops/action',{'action':'simulator','enabled':True},csrf)
        time.sleep(5.5)
        after_live=req(a,'/api/ops/snapshot')[1]
        check('five-second collector persists changing operational data',after_live['kpis']['ai_requests']>before_live['kpis']['ai_requests'] and after_live['meta']['collector_last_seen']>before_live['meta']['collector_last_seen'])
        req(a,'/api/ops/action',{'action':'simulator','enabled':False},csrf)
        check('simulation does not mutate bank balances',float(store.query("SELECT balance FROM zx_accounts WHERE user_id='U1001'")[0]['balance'])==base-52)
        exported=req(a,'/api/ops/export?branch=B02&period=7d')[1]
        check('export is filtered and masked',exported['meta']['branch']=='B02' and 'phone_full' not in json.dumps(exported))
        proc.terminate();proc.wait();proc=start();health=ready()
        balance=req(a,'/api/dashboard')[1]['observation']['balance']
        check('service restart reloads MySQL state',balance==base-52)
        check('service restart clears manager elevation',req(a,'/api/ops/snapshot')[1]['role']=='leader')
    finally:
        if proc and proc.poll() is None:proc.terminate();proc.wait()
        import mysql.connector
        cfg={k:v for k,v in store.config.items()}
        cn=mysql.connector.connect(**cfg)
        try:
            # Only the uniquely generated schema belonging to this invocation is disposable.
            assert database.startswith('zx_test_') and len(database)==20
            cur=cn.cursor();cur.execute('DROP DATABASE `'+database+'`');cur.close()
        finally:cn.close()
        result={'suite':'mysql_operations_integration','date':'2026-11-02','isolated_test_schema':True,'run':len(checks),'passed':sum(c['pass'] for c in checks),'checks':checks}
        (ROOT/'evidence/mysql_ops_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
