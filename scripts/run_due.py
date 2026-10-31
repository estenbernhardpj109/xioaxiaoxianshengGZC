"""Advance only an offline sandbox state, while the HTTP server is stopped."""
import argparse,os,sys,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser();p.add_argument('--at',required=True,help='YYYY-MM-DDTHH:MM:SS+08:00');args=p.parse_args()
os.environ.setdefault('BANK_STATE_FILE',str(ROOT/'.runtime/bank.json'))
os.environ.setdefault('BANK_AUDIT_FILE',str(ROOT/'.runtime/audit.jsonl'))
from backend.bank.core import get_core
from backend.bank import world
core=get_core();state=Path(os.environ['BANK_STATE_FILE'])
if state.exists():core._data=json.loads(state.read_text(encoding='utf-8'))
print(json.dumps(core.run_due(datetime.fromisoformat(args.at).timestamp()),ensure_ascii=False,indent=2))
