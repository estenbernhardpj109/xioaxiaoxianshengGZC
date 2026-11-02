"""Chart-ready, joined sandbox data. All aggregates are derived, never invented."""
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from .bank.core import get_core
from .bank import world
from .agent.security import get_audit

TZ = timezone(timedelta(hours=8))


def dashboard():
    c = get_core()
    with c._lock:
        tx = c.get_transactions(limit=100000)
        monthly = defaultdict(lambda: {'income': 0, 'expense': 0})
        categories, channels = defaultdict(float), defaultdict(float)
        heat = defaultdict(float)
        for t in tx:
            dt = datetime.fromtimestamp(t['ts'], TZ)
            k = dt.strftime('%Y-%m')
            monthly[k]['income' if t['direction']=='in' else 'expense'] += t['amount']
            if t['direction'] == 'out':
                categories[t['category']] += t['amount']
                channels[t['channel']] += t['amount']
                heat[(dt.weekday(), dt.hour)] += t['amount']
        products = c.wealth_list()
        holdings = [{**h, 'name': next(p['name'] for p in products if p['product_id']==h['product_id'])} for h in c.get_holdings()]
        subs = c.subscription_list()
        for s in subs:
            s['monthly_equivalent'] = round(s['amount'] / (12 if s['cycle']=='年' else 1), 2)
            s['days_until_charge'] = max(0, (datetime.fromtimestamp(s['next_charge'],TZ).date() - datetime.now(TZ).date()).days)
            s['matched_debits'] = len([t for t in tx if t['counterparty']==s['merchant'] and t['channel']=='代扣'])
        audit = [a for a in get_audit().recent(10000) if a.get('owner_id','U1001')==c.current_user_id]
        return {'source': {'kind':'synthetic', 'seed':2026, 'as_of':'2026-10-31T12:00:00+08:00',
                            'scope':'当前登录用户；初始演示余额不与历史流水对账',
                            'join_keys':['user_id','product_id','merchant','reservation_id']},
                'observation':c.observe(), 'transactions':tx, 'transaction_count':len(tx),
                'monthly':[{'month':k, **{n:round(v,2) for n,v in monthly[k].items()}} for k in sorted(monthly)],
                'categories':[{'name':k,'value':round(v,2)} for k,v in sorted(categories.items(),key=lambda x:-x[1])],
                'channels':[{'name':k,'value':round(v,2)} for k,v in channels.items()],
                'heatmap':[[day,hour,round(v,2)] for (day,hour),v in heat.items()],
                'products':products,'holdings':holdings,'subscriptions':subs, 'cards':c.card_list(),
                'orders':c.order_list(),'tasks':c.get_scheduled_tasks(), 'anomalies':c.detect_anomaly(),
                'audit':audit,'risk_counts':dict(Counter(a.get('risk','LOW') for a in audit))}
