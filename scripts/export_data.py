import sys,os,json,csv,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.bank.data import get_initial_data
from backend.telemetry import dashboard
out=ROOT/'data';out.mkdir(exist_ok=True)
data=get_initial_data()
for u in data['users'].values():u.pop('phone_full',None)
(out/'snapshot.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
for name,records in [('transactions',data['transactions']),('products',data['wealth_products']),('holdings',data['holdings']['U1001']),('cards',data['cards']['U1001']),('subscriptions',data['subscriptions']['U1001']),('orders',data['orders']['U1001'])]:
    fields=list(dict.fromkeys(k for row in records for k in row))
    with (out/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fields);w.writeheader();w.writerows(records)
baseline=dashboard();baseline['audit']=[];baseline['risk_counts']={}
(ROOT/'evidence/dashboard_baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2),encoding='utf-8')
print('Exported 360 synthetic transaction rows and six CSV tables')
