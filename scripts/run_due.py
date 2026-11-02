"""Advance only an offline sandbox state, while the HTTP server is stopped."""
import argparse,os,sys,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('--at',required=True,help='YYYY-MM-DDTHH:MM:SS+08:00');p.add_argument('--storage',choices=['mysql','json'],default=os.environ.get('BANK_STORAGE','mysql'));args=p.parse_args()
os.environ['BANK_STORAGE']=args.storage
os.environ.setdefault('BANK_STATE_FILE',str(ROOT/'.runtime/bank.json'))
os.environ.setdefault('BANK_AUDIT_FILE',str(ROOT/'.runtime/audit.jsonl'))
from backend.bank.core import get_core
from backend.bank import world
from backend.storage import get_store,unit_of_work
from backend.agent.security import get_audit
from backend.server import user_scope
core=get_core();state=Path(os.environ['BANK_STATE_FILE'])
if state.exists():core._data=json.loads(state.read_text(encoding='utf-8'))
store=get_store()
if store:store.initialize(core,get_audit())
result=[]
for owner in {t.get('owner_id','U1001') for t in core._data['scheduled_tasks']}:
    with core._lock,user_scope(core,owner),unit_of_work(core,get_audit()):
        result.extend(core.run_due(datetime.fromisoformat(args.at).timestamp()))
print(json.dumps(result,ensure_ascii=False,indent=2))
