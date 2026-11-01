"""Loopback-only evaluation service with browser-owned sessions and bounded JSON."""
import argparse, json, os, sys, threading, secrets, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, unquote, parse_qs
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
from backend.storage import get_store, unit_of_work
from backend import operations
agent=get_agent()
CLIENTS={}
LOCK=threading.RLock()
MANAGER_CODE = None

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def headers_common(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
    def client(self,create=False):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            key=cookie['bank_client'].value if 'bank_client' in cookie else ''
        except Exception: key=''
        if key in CLIENTS:return key
        if create:
            key=secrets.token_urlsafe(32)
            CLIENTS[key]={'csrf':secrets.token_urlsafe(24),'sessions':set()}
            return key
        return None
    def send(self,obj,status=200,cookie=None):
        raw=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.headers_common()
        if cookie:self.send_header('Set-Cookie',f'bank_client={cookie}; HttpOnly; SameSite=Strict; Path=/')
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def local(self):
        host=self.headers.get('Host','')
        return host in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def do_OPTIONS(self): self.send({'error':'cross-origin disabled'},403)
    def do_GET(self):
        if not self.local():return self.send({'error':'invalid host'},403)
        path=urlsplit(self.path).path
        if path=='/api/health':
            key=self.client(True)
            return self.send({'status':'ok','version':'3.0.0','storage':os.environ.get('BANK_STORAGE','json'),'llm_enabled':llm.is_configured(),'llm_provider':llm.provider_name(),
                              'csrf':CLIENTS[key]['csrf'],'environment':'synthetic sandbox'},cookie=key)
        if path=='/api/tools':return self.send({'tools':list_tools(),'schemas':tool_schemas()})
        if path.startswith('/api/'):
            if not self.client():return self.send({'error':'session required'},401)
            if path in ('/api/ops/snapshot','/api/ops/export'):
                try:
                    store=get_store()
                    if not store:return self.send({'error':'运营大屏需要MySQL模式，请使用启动_MYSQL.ps1'},503)
                    qs=parse_qs(urlsplit(self.path).query)
                    result=operations.snapshot(store,qs.get('branch',['all'])[0],qs.get('period',['7d'])[0])
                    result['role']=CLIENTS[self.client()].get('role','leader')
                    if path.endswith('/export'):
                        raw=json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')
                        self.send_response(200);self.headers_common();self.send_header('Content-Type','application/json; charset=utf-8')
                        self.send_header('Content-Disposition','attachment; filename="operations-masked.json"');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                        return
                    return self.send(result)
                except ValueError:return self.send({'error':'筛选参数无效'},400)
                except Exception:return self.send({'error':'数据库暂不可用，已停止刷新，请检查服务连接'},503)
            with LOCK:
                if path=='/api/audit':return self.send({'audit':get_audit().recent(500)})
                if path=='/api/dashboard':return self.send(dashboard())
            return self.send({'error':'not found'},404)
        rel='index.html' if path=='/' else 'operations.html' if path in ('/ops','/ops/') else unquote(path.removeprefix('/static/')).lstrip('/')
        base=ROOT/'frontend';full=(base/rel).resolve()
        if not full.is_relative_to(base) or not full.is_file():return self.send({'error':'not found'},404)
        types={'.html':'text/html; charset=utf-8','.js':'application/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png'}
        raw=full.read_bytes();key=self.client(True)
        self.send_response(200);self.headers_common();self.send_header('Set-Cookie',f'bank_client={key}; HttpOnly; SameSite=Strict; Path=/')
        self.send_header('Content-Type',types.get(full.suffix,'application/octet-stream'));self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        if not self.local():return self.send({'error':'invalid host'},403)
        key=self.client()
        if not key or self.headers.get('X-Bank-CSRF')!=CLIENTS[key]['csrf']:return self.send({'error':'CSRF or session rejected'},403)
        origin=self.headers.get('Origin')
        if origin and origin != 'http://'+self.headers.get('Host'):return self.send({'error':'origin rejected'},403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send({'error':'JSON required'},415)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>16384:return self.send({'error':'request size rejected'},413)
            body=json.loads(self.rfile.read(n).decode(),parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            if not isinstance(body,dict):raise ValueError()
        except Exception:return self.send({'error':'invalid JSON'},400)
        if urlsplit(self.path).path.startswith('/api/ops/'):
            with LOCK:
                if self.path=='/api/ops/login':
                    client=CLIENTS[key]
                    if client.get('locked_until',0)>time.time():return self.send({'error':'尝试次数过多，请60秒后重试'},429)
                    password=body.get('password')
                    if not isinstance(password,str) or not MANAGER_CODE or not secrets.compare_digest(password,MANAGER_CODE):
                        client['failures']=client.get('failures',0)+1
                        if client['failures']>=5:client.update(locked_until=time.time()+60,failures=0)
                        return self.send({'error':'运营管理口令不正确'},403)
                    client.update(role='manager',failures=0)
                    return self.send({'ok':True,'role':'manager'})
                if self.path=='/api/ops/logout':
                    CLIENTS[key]['role']='leader'
                    return self.send({'ok':True,'role':'leader'})
                if CLIENTS[key].get('role')!='manager':return self.send({'error':'此操作需要银行经理权限'},403)
                if self.path=='/api/ops/action':
                    try:
                        store=get_store()
                        if not store:return self.send({'error':'需要MySQL模式'},503)
                        return self.send(operations.action(store,body))
                    except ValueError as e:return self.send({'error':str(e)},400)
                    except RuntimeError as e:return self.send({'error':str(e)},409)
                    except Exception:return self.send({'error':'数据库操作失败，未提交变更'},503)
                return self.send({'error':'not found'},404)
        sid=body.get('session_id')
        if sid and sid not in CLIENTS[key]['sessions']:return self.send({'error':'session ownership rejected'},403)
        with LOCK:
            try:
                start=time.perf_counter()
                if self.path not in ('/api/chat','/api/confirm'):return self.send({'error':'not found'},404)
                if self.path=='/api/chat':
                    text=body.get('text')
                    if not isinstance(text,str) or not text.strip() or len(text)>2000:return self.send({'error':'text invalid'},400)
                    if not sid and len(CLIENTS[key]['sessions'])>=50:return self.send({'error':'session limit'},429)
                elif type(body.get('confirmed')) is not bool:return self.send({'error':'confirmed must be boolean'},400)
                core=get_core()
                with core._lock,unit_of_work(core,get_audit(),agent.mgr) as request_rows:
                    if self.path=='/api/chat':
                        out=agent.handle(text,sid,use_llm_plan=body.get('mode')=='llm')
                    elif self.path=='/api/confirm':
                        out=agent.confirm(sid,body.get('step_id'),body['confirmed'],body.get('password'),body.get('otp'),body.get('challenge_token'))
                        session=agent.mgr.get(sid)
                        out.setdefault('planner_source',getattr(session,'planner_source','rule'))
                    else:return self.send({'error':'not found'},404)
                    request_rows.append(operations.request_record(out,(time.perf_counter()-start)*1000))
                if out.get('session_id'):CLIENTS[key]['sessions'].add(out['session_id'])
                out['observation']=get_core().observe()
                self.send(out,400 if 'error' in out else 200)
            except Exception:
                self.send({'error':'服务异常，未确认的操作不会执行，请查看本地审计'},500)

def main():
    global MANAGER_CODE
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--storage',choices=['mysql','json'],default=os.environ.get('BANK_STORAGE','mysql'))
    parser.add_argument('--api-key-stdin',action='store_true',help='read an API key from one stdin line; never persist it')
    args=parser.parse_args()
    os.environ['BANK_STORAGE']=args.storage
    if args.api_key_stdin:
        os.environ['LLM_API_KEY']=sys.stdin.readline().strip()
    core=get_core();state=Path(os.environ['BANK_STATE_FILE'])
    if state.exists():
        core._data=json.loads(state.read_text(encoding='utf-8'))
    store=get_store()
    if store:
        store.initialize(core,get_audit())
        operations.seed(store)
        operations.collect(store)
    code_file=ROOT/'.runtime'/'ops_manager_code.txt'
    code_file.parent.mkdir(parents=True,exist_ok=True)
    MANAGER_CODE=os.environ.get('OPS_MANAGER_PASSWORD') or (code_file.read_text().strip() if code_file.exists() else secrets.token_urlsafe(12))
    if not os.environ.get('OPS_MANAGER_PASSWORD'):code_file.write_text(MANAGER_CODE,encoding='utf-8')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    def tick():
        import time
        while True:
            time.sleep(5)
            with LOCK:
                try:
                    if any(t['status']=='待执行' and t['execute_ts']<=time.time() for t in core.get_scheduled_tasks()):
                        with core._lock,unit_of_work(core,get_audit()):core.run_due()
                    if store:operations.collect(store)
                except Exception:
                    print('运营采集或定时任务未提交，请检查数据库；不会切换到文件存储',flush=True)
    threading.Thread(target=tick,daemon=True).start()
    print(f'Sandbox at http://127.0.0.1:{args.port}',flush=True)
    print(f'Operations at http://127.0.0.1:{args.port}/ops ; manager code: .runtime/ops_manager_code.txt (or environment)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()
if __name__=='__main__':main()
