"""HTTP protocol tests against a fresh isolated process."""
import json, sys, subprocess, tempfile, time, urllib.request, urllib.error, http.cookiejar, os, socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as td:
    env=dict(os.environ,BANK_STATE_FILE=str(Path(td)/'bank.json'),BANK_AUDIT_FILE=str(Path(td)/'audit.jsonl'))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    proc=subprocess.Popen([sys.executable,str(ROOT/'backend/server.py'),'--port',str(port)],env=env,stdout=subprocess.DEVNULL)
    url=f'http://127.0.0.1:{port}';checks=[]
    def client():return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    a,b=client(),client()
    def req(c,path,body=None,csrf=None,extra=None):
        headers={'Content-Type':'application/json'}
        if csrf:headers['X-Bank-CSRF']=csrf
        headers.update(extra or {})
        q=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with c.open(q,timeout=5) as r:return r.status,json.loads(r.read()) if path.startswith('/api/') else r.read()
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
    try:
        for _ in range(50):
            try:_,h=req(a,'/api/health');break
            except OSError:time.sleep(.1)
        _,hb=req(b,'/api/health')
        def check(name,ok):checks.append({'name':name,'pass':bool(ok)})
        check('health and CSRF issued',h['status']=='ok' and bool(h['csrf']))
        check('no CSRF rejected',req(a,'/api/chat',{'text':'查余额'})[0]==403)
        code,r=req(a,'/api/chat',{'text':'给李婷转500元'},h['csrf']);sid=r['session_id'];p=r['pending_confirm']
        check('yellow waits for confirmation',code==200 and p['risk']=='MEDIUM')
        check('cross-browser session rejected',req(b,'/api/confirm',{'session_id':sid,'step_id':p['step_id'],'confirmed':True},hb['csrf'])[0]==403)
        check('string boolean rejected',req(a,'/api/confirm',{'session_id':sid,'step_id':p['step_id'],'confirmed':'false'},h['csrf'])[0]==400)
        code,r=req(a,'/api/confirm',{'session_id':sid,'step_id':p['step_id'],'confirmed':True},h['csrf'])
        check('confirmed transfer changes balance',code==200 and r['observation']['balance']==86032.5)
        check('confirmation replay rejected',req(a,'/api/confirm',{'session_id':sid,'step_id':p['step_id'],'confirmed':True},h['csrf'])[0]==400)
        check('cross-origin rejected',req(a,'/api/chat',{'text':'查余额'},h['csrf'],{'Origin':'https://evil.example'})[0]==403)
        check('path traversal rejected',req(a,'/static/%2e%2e/backend/server.py')[0]==404)
        check('malformed text rejected',req(a,'/api/chat',{'text':{}},h['csrf'])[0]==400)
        check('oversized body rejected',req(a,'/api/chat',{'text':'a'*17000},h['csrf'])[0]==413)
        check('tool schemas published',len(req(a,'/api/tools')[1]['schemas'])==30)
        check('dashboard joins and aggregates',req(a,'/api/dashboard')[1]['transaction_count']==361)
        check('state persisted',Path(env['BANK_STATE_FILE']).is_file())
        proc.terminate();proc.wait()
        proc=subprocess.Popen([sys.executable,str(ROOT/'backend/server.py'),'--port',str(port)],env=env,stdout=subprocess.DEVNULL)
        for _ in range(50):
            try:req(a,'/api/health');break
            except OSError:time.sleep(.1)
        check('restart restores balance',req(a,'/api/dashboard')[1]['observation']['balance']==86032.5)
    finally:proc.terminate();proc.wait()
    result={'suite':'http_integration','run':len(checks),'passed':sum(x['pass'] for x in checks),'checks':checks}
    (ROOT/'evidence/http_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));sys.exit(result['passed']!=result['run'])
