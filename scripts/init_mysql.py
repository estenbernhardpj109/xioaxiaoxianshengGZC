"""Initialise only project-owned zx_* tables; import existing sandbox state once."""
import getpass
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['BANK_STORAGE']='mysql'
os.environ.setdefault('BANK_AUDIT_FILE',str(ROOT/'.runtime'/'audit.jsonl'))
if not os.environ.get('MYSQL_PASSWORD'):
    os.environ['MYSQL_PASSWORD']=getpass.getpass('MySQL password: ')
from backend.storage import get_store,DDL
from backend.bank.core import get_core
from backend.agent.security import get_audit
from backend.operations import seed,collect
core=get_core();state=ROOT/'.runtime'/'bank.json'
if state.exists():core._data=json.loads(state.read_text(encoding='utf-8'))
store=get_store();store.initialize(core,get_audit());seed(store);collect(store)
schema=ROOT/'database'/'schema.sql';schema.parent.mkdir(exist_ok=True)
schema.write_text('-- MySQL 8 / InnoDB / utf8mb4. No credentials. Only project-owned tables.\n\n'+';\n\n'.join(DDL)+';\n',encoding='utf-8')
print(json.dumps({'storage':'mysql','database':store.database,'tables':len(DDL),'schema_version':store.query('SELECT MAX(version) version FROM zx_schema_version')[0]['version'],'state_version':store.version},ensure_ascii=False))
