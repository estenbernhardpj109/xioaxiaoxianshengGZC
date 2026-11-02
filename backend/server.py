"""Loopback evaluation service: authenticated customers, digital concierge and ops."""
import argparse
import json
import os
import sys
import threading
import secrets
import hashlib
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, unquote, parse_qs, quote
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('BANK_STATE_FILE',str(ROOT/'.runtime'/'bank.json'))
os.environ.setdefault('BANK_AUDIT_FILE',str(ROOT/'.runtime'/'audit.jsonl'))
from backend.agent.agent import get_agent
from backend.agent.tools import list_tools, tool_schemas
from backend.agent.security import get_audit
from backend.agent import llm
from backend.bank.core import get_core
from backend.telemetry import dashboard
from backend.storage import get_store, unit_of_work, branch_for_user
from backend import operations, auth, digital_assistant
from backend.privacy import browser_payload

agent=get_agent()
CLIENTS={}
LOCK=threading.RLock()
MANAGER_CODE=None


@contextmanager
def user_scope(core, uid):
    """Switch the single-instance core only while holding LOCK/core._lock."""
    previous=core.current_user_id
    core.current_user_id=uid
    try:yield
    finally:core.current_user_id=previous


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def headers_common(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'; object-src 'none'")

    def cookie_value(self,name):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            return cookie[name].value if name in cookie else ''
        except Exception:return ''

    def client(self,create=False):
        key=self.cookie_value('bank_client')
        if key in CLIENTS:
            CLIENTS[key]['seen']=time.time()
            return key
        if create:
            return self.new_client()
        return None

    def new_client(self,person=None,token=None):
        key=secrets.token_urlsafe(32)
        CLIENTS[key]={'csrf':secrets.token_urlsafe(24),'sessions':set(),'seen':time.time(),
            'principal':person['user_id'] if person else None,
            'auth_binding':hashlib.sha256(token.encode()).hexdigest() if token else None}
        if len(CLIENTS)>2000:
            for old in sorted(CLIENTS,key=lambda k:CLIENTS[k].get('seen',0))[:500]:
                if old!=key:CLIENTS.pop(old,None)
        return key

    def bound_client(self,person):
        key=self.client(True)
        if person:
            token=self.cookie_value('bank_auth');binding=hashlib.sha256(token.encode()).hexdigest()
            data=CLIENTS[key]
            if data.get('principal') and (data['principal']!=person['user_id'] or data.get('auth_binding')!=binding):
                key=self.new_client()
            CLIENTS[key].update(principal=person['user_id'],auth_binding=binding)
        return key

    def owns_client(self,key,person):
        data=CLIENTS.get(key,{})
        return data.get('principal')==person['user_id'] and data.get('auth_binding')==hashlib.sha256(self.cookie_value('bank_auth').encode()).hexdigest()

    def cookie_header(self,name,value,expired=False):
        return f'{name}={value}; HttpOnly; SameSite=Strict; Path=/' + ('; Max-Age=0' if expired else '')

    def send(self,obj,status=200,cookie=None,auth_cookie=None,expire=False):
        raw=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.headers_common()
        if cookie:self.send_header('Set-Cookie',self.cookie_header('bank_client',cookie))
        if auth_cookie:self.send_header('Set-Cookie',self.cookie_header('bank_auth',auth_cookie))
        if expire:
            for name in ('bank_auth','bank_client'):self.send_header('Set-Cookie',self.cookie_header(name,'',True))
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    def redirect(self,target):
        self.send_response(303);self.headers_common()
        self.send_header('Location',target);self.send_header('Content-Length','0');self.end_headers()

    def local(self):
        return self.headers.get('Host','') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def person(self):return auth.current(self.cookie_value('bank_auth'))

    def do_OPTIONS(self):self.send({'error':'cross-origin disabled'},403)

    def do_GET(self):
        if not self.local():return self.send({'error':'invalid host'},403)
        path=urlsplit(self.path).path
        if path=='/api/health':
            try:
                with LOCK:person=self.person()
            except Exception:person=None
            key=self.bound_client(person)
            return self.send({'status':'ok','version':'4.0.0','storage':os.environ.get('BANK_STORAGE','json'),
                'llm_enabled':llm.is_configured(),'llm_provider':llm.provider_name(),
                'csrf':CLIENTS[key]['csrf'],'environment':'synthetic sandbox'},cookie=key)
        if path=='/api/tools':return self.send({'tools':list_tools(),'schemas':tool_schemas()})
        pages={'/login':'login.html','/register':'register.html','/app':'index.html','/assistant':'assistant.html','/ops':'operations.html','/ops/':'operations.html'}
        if path.startswith('/static/') and not path.lower().endswith('.html'):
            return self.static(unquote(path[len('/static/'):]))
        try:
            with LOCK:person=self.person()
        except Exception:return self.send({'error':'数据库暂不可用，请稍后重试'},503)
        if path=='/api/auth/me':
            key=self.bound_client(person)
            return self.send({'authenticated':bool(person),'user':auth.public_user(person) if person else None,
                'csrf':CLIENTS[key]['csrf'],'llm_enabled':llm.is_configured(),
                'storage':os.environ.get('BANK_STORAGE','json')},cookie=key)
        if path=='/':return self.redirect('/app' if person else '/login?next=%2Fapp')
        if path in pages:
            if path in ('/login','/register'):
                if person:return self.redirect(auth.safe_next(parse_qs(urlsplit(self.path).query).get('next',['/app'])[0],person))
            elif not person:return self.redirect('/login?next='+quote('/ops' if path=='/ops/' else path,safe=''))
            elif path.startswith('/ops') and not person['can_view_ops']:return self.redirect('/app')
            return self.static(pages[path],person=person)
        if not path.startswith('/api/'):return self.send({'error':'not found'},404)
        if not person:return self.send({'error':'请登录后继续','code':'AUTH_REQUIRED'},401)
        key=self.client()
        if not key:return self.send({'error':'请刷新页面以恢复浏览器会话'},401)
        if not self.owns_client(key,person):return self.send({'error':'浏览器会话与登录身份不一致，请刷新页面'},403)
        if path in ('/api/ops/snapshot','/api/ops/export'):
            if not person['can_view_ops']:return self.send({'error':'当前身份无运营访问权限'},403)
            try:
                store=get_store()
                if not store:return self.send({'error':'运营大屏需要MySQL模式，请使用启动_MYSQL.ps1'},503)
                qs=parse_qs(urlsplit(self.path).query)
                result=operations.snapshot(store,qs.get('branch',['all'])[0],qs.get('period',['7d'])[0])
                result['role']=CLIENTS[key].get('role','leader')
                if path.endswith('/export'):
                    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2).encode()
                    self.send_response(200);self.headers_common()
                    self.send_header('Content-Type','application/json; charset=utf-8')
                    self.send_header('Content-Disposition','attachment; filename="operations-masked.json"')
                    self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                    return
                return self.send(result)
            except ValueError:return self.send({'error':'筛选参数无效'},400)
            except Exception:return self.send({'error':'数据库暂不可用，已停止刷新，请检查服务连接'},503)
        with LOCK:
            core=get_core()
            with core._lock,user_scope(core,person['user_id']):
                if path=='/api/audit':
                    rows=[a for a in get_audit().recent(10000) if a.get('owner_id','U1001')==person['user_id']]
                    return self.send(browser_payload({'audit':rows[-500:]}))
                if path=='/api/dashboard':return self.send(browser_payload(dashboard()))
                if path=='/api/conversation':
                    sid=CLIENTS[key].get('active_session')
                    session=agent.mgr.get(sid) if sid and sid in CLIENTS[key]['sessions'] else None
                    if not session:return self.send({'session_id':None,'history':[],'observation':core.observe()})
                    pending=next((s for s in session.pending_plan or [] if s['status']=='await_confirm'),None)
                    out={'session_id':sid,'history':session.history[-30:],
                        'plan':agent._plan_view(session.pending_plan) if session.pending_plan else None,
                        'pending_confirm':agent._pending_view(pending,session) if pending and not session.paused else None,
                        'observation':core.observe()}
                    consult=getattr(session,'last_consultation',None)
                    if consult and session.history and session.history[-1]['text']==consult['reply']:
                        out.update({k:v for k,v in consult.items() if k!='reply'})
                        out['restored_reply']=consult['reply']
                    return self.send(browser_payload(digital_assistant.decorate(out)))
        return self.send({'error':'not found'},404)

    def static(self,rel,person=None):
        base=(ROOT/'frontend').resolve();full=(base/rel).resolve()
        if not full.is_relative_to(base) or not full.is_file():return self.send({'error':'not found'},404)
        types={'.html':'text/html; charset=utf-8','.js':'application/javascript; charset=utf-8',
            '.css':'text/css; charset=utf-8','.png':'image/png','.svg':'image/svg+xml','.jpg':'image/jpeg'}
        raw=full.read_bytes();key=self.bound_client(person)
        self.send_response(200);self.headers_common()
        self.send_header('Set-Cookie',self.cookie_header('bank_client',key))
        self.send_header('Content-Type',types.get(full.suffix,'application/octet-stream'))
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    def do_POST(self):
        if not self.local():return self.send({'error':'invalid host'},403)
        key=self.client()
        if not key or self.headers.get('X-Bank-CSRF')!=CLIENTS[key]['csrf']:return self.send({'error':'CSRF or session rejected'},403)
        origin=self.headers.get('Origin')
        if origin and origin!='http://'+self.headers.get('Host'):return self.send({'error':'origin rejected'},403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send({'error':'JSON required'},415)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>16384:return self.send({'error':'request size rejected'},413)
            body=json.loads(self.rfile.read(n).decode(),parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
            if not isinstance(body,dict):raise ValueError()
        except Exception:return self.send({'error':'invalid JSON'},400)
        path=urlsplit(self.path).path
        if path.startswith('/api/auth/'):return self.auth_action(path,body,key)
        with LOCK:
            try:person=self.person()
            except Exception:return self.send({'error':'数据库暂不可用，未提交任何业务操作'},503)
            if not person:return self.send({'error':'请登录后继续','code':'AUTH_REQUIRED'},401)
            if not self.owns_client(key,person):return self.send({'error':'浏览器会话与登录身份不一致，请刷新页面'},403)
            if path.startswith('/api/ops/'):
                if not person['can_view_ops']:return self.send({'error':'当前身份无运营访问权限'},403)
                return self.ops_action(path,body,key)
            sid=body.get('session_id')
            if sid is not None and not isinstance(sid,str):return self.send({'error':'session_id invalid'},400)
            if sid and sid not in CLIENTS[key]['sessions']:return self.send({'error':'session ownership rejected'},403)
            if path=='/api/conversation/new':
                old=agent.mgr.get(CLIENTS[key].get('active_session'))
                if old and any(s['status'] in ('pending','await_confirm') for s in old.pending_plan or []):
                    return self.send({'error':'请先完成、取消当前任务或申请人工接管'},409)
                CLIENTS[key].pop('active_session',None)
                return self.send({'ok':True})
            try:
                start=time.perf_counter()
                if path not in ('/api/chat','/api/confirm','/api/assistant/chat'):return self.send({'error':'not found'},404)
                if path in ('/api/chat','/api/assistant/chat'):
                    text=body.get('text')
                    if not isinstance(text,str) or not text.strip() or len(text)>2000:return self.send({'error':'text invalid'},400)
                    if not sid and len(CLIENTS[key]['sessions'])>=50:return self.send({'error':'session limit'},429)
                elif type(body.get('confirmed')) is not bool:return self.send({'error':'confirmed must be boolean'},400)
                core=get_core()
                with core._lock,user_scope(core,person['user_id']),unit_of_work(core,get_audit(),agent.mgr) as request_rows:
                    if path=='/api/chat':out=agent.handle(text,sid,use_llm_plan=body.get('mode')=='llm')
                    elif path=='/api/assistant/chat':out=digital_assistant.handle(text,sid,use_llm=body.get('mode')=='llm')
                    else:
                        out=agent.confirm(sid,body.get('step_id'),body['confirmed'],body.get('password'),body.get('otp'),body.get('challenge_token'))
                        session=agent.mgr.get(sid)
                        out.setdefault('planner_source',getattr(session,'planner_source','rule'))
                    request_rows.append(operations.request_record(out,(time.perf_counter()-start)*1000,branch_for_user(person['user_id'])))
                    out['observation']=core.observe()
                if out.get('session_id'):
                    CLIENTS[key]['sessions'].add(out['session_id'])
                    CLIENTS[key]['active_session']=out['session_id']
                self.send(browser_payload(digital_assistant.decorate(out)),400 if 'error' in out else 200)
            except Exception:self.send({'error':'服务异常，未确认的操作不会执行，请查看本地审计'},500)

    def auth_action(self,path,body,key):
        with LOCK:
            try:
                if path=='/api/auth/register':
                    core=get_core()
                    with core._lock:return self.send(auth.register(core,body,self.client_address[0]),201)
                if path=='/api/auth/logout':
                    person=self.person()
                    if person and not self.owns_client(key,person):return self.send({'error':'浏览器会话与登录身份不一致，请刷新页面'},403)
                    auth.logout(self.cookie_value('bank_auth'));CLIENTS.pop(key,None)
                    return self.send({'ok':True,'next':'/login'},expire=True)
                if path not in ('/api/auth/login','/api/auth/demo'):return self.send({'error':'not found'},404)
                token=auth.demo_login(self.client_address[0]) if path.endswith('/demo') else auth.login(body,self.client_address[0])
                auth.logout(self.cookie_value('bank_auth'))
                person=auth.current(token)
                CLIENTS.pop(key,None);new_key=self.new_client(person,token)
                return self.send({'ok':True,'user':auth.public_user(person),'csrf':CLIENTS[new_key]['csrf'],
                    'next':auth.safe_next(body.get('next'),person)},cookie=new_key,auth_cookie=token)
            except auth.AuthError as e:return self.send({'error':str(e)},e.status)
            except Exception:return self.send({'error':'身份服务暂不可用，未提交变更，请检查数据库连接'},503)

    def ops_action(self,path,body,key):
        if path=='/api/ops/login':
            client=CLIENTS[key]
            if client.get('locked_until',0)>time.time():return self.send({'error':'尝试次数过多，请60秒后重试'},429)
            password=body.get('password')
            if not isinstance(password,str) or not MANAGER_CODE or not secrets.compare_digest(password,MANAGER_CODE):
                client['failures']=client.get('failures',0)+1
                if client['failures']>=5:client.update(locked_until=time.time()+60,failures=0)
                return self.send({'error':'运营管理口令不正确'},403)
            client.update(role='manager',failures=0)
            return self.send({'ok':True,'role':'manager'})
        if path=='/api/ops/logout':
            CLIENTS[key]['role']='leader';return self.send({'ok':True,'role':'leader'})
        if CLIENTS[key].get('role')!='manager':return self.send({'error':'此操作需要银行经理权限'},403)
        if path=='/api/ops/action':
            try:
                store=get_store()
                if not store:return self.send({'error':'需要MySQL模式'},503)
                return self.send(operations.action(store,body))
            except ValueError as e:return self.send({'error':str(e)},400)
            except RuntimeError as e:return self.send({'error':str(e)},409)
            except Exception:return self.send({'error':'数据库操作失败，未提交变更'},503)
        return self.send({'error':'not found'},404)


def main():
    global MANAGER_CODE
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--storage',choices=['mysql','json'],default=os.environ.get('BANK_STORAGE','mysql'))
    parser.add_argument('--api-key-stdin',action='store_true',help='read an API key from one stdin line; never persist it')
    args=parser.parse_args();os.environ['BANK_STORAGE']=args.storage
    if args.api_key_stdin:os.environ['LLM_API_KEY']=sys.stdin.readline().strip()
    core=get_core();state=Path(os.environ['BANK_STATE_FILE'])
    if state.exists():core._data=json.loads(state.read_text(encoding='utf-8'))
    store=get_store()
    if store:
        store.initialize(core,get_audit());operations.seed(store);operations.collect(store)
    code_file=ROOT/'.runtime'/'ops_manager_code.txt';code_file.parent.mkdir(parents=True,exist_ok=True)
    MANAGER_CODE=os.environ.get('OPS_MANAGER_PASSWORD') or (code_file.read_text().strip() if code_file.exists() else secrets.token_urlsafe(12))
    if not os.environ.get('OPS_MANAGER_PASSWORD'):code_file.write_text(MANAGER_CODE,encoding='utf-8')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    def tick():
        while True:
            time.sleep(5)
            with LOCK:
                try:
                    owners={t.get('owner_id','U1001') for t in core._data['scheduled_tasks'] if t['status']=='待执行' and t['execute_ts']<=time.time()}
                    for owner in owners:
                        with core._lock,user_scope(core,owner),unit_of_work(core,get_audit()):core.run_due()
                    if store:operations.collect(store)
                except Exception:print('运营采集或定时任务未提交，请检查数据库；不会切换到文件存储',flush=True)
    threading.Thread(target=tick,daemon=True).start()
    print(f'Sandbox v4 at http://127.0.0.1:{args.port}/login',flush=True)
    print('Digital concierge: /assistant ; Operations: /ops ; manager code: .runtime/ops_manager_code.txt (or environment)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()


if __name__=='__main__':main()
