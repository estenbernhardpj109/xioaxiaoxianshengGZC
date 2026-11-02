"""Digital environment adapter. No physical robot or real bank is connected."""
import copy
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from ..agent.validation import money


def daily_committed(self):
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).date()
    return round(sum(t['amount'] for t in self._data.get('commitments', [])
                     if t.get('owner_id','U1001')==self.current_user_id and datetime.fromtimestamp(t['ts'], tz).date() == today), 2)


def commit(self, amount, source):
    self._data.setdefault('commitments', []).append({'ts': time.time(), 'amount': amount, 'source': source, 'owner_id':self.current_user_id})


def observe(self):
    with self._lock:
        return {'environment': 'local_digital_bank_sandbox', 'data_kind': 'synthetic',
                'observed_at': time.time(), 'balance': self.get_account()['balance'],
                'held': sum(r['amount'] for r in self._data.get('reservations', {}).values() if r['status'] == 'held' and r.get('owner_id','U1001')==self.current_user_id),
                'daily_committed': self.daily_committed(),
                'cards': self.card_list(), 'holdings': self.get_holdings(), 'subscriptions': self.subscription_list(),
                'scheduled': self.get_scheduled_tasks(), 'orders': self.order_list()}


def snapshot(self):
    return copy.deepcopy(self._data)


def restore(self, state):
    with self._lock:
        self._data = copy.deepcopy(state)
        self.save()


def save(self):
    from ..storage import save_core
    if save_core(self):
        return
    target = os.environ.get('BANK_STATE_FILE')
    if target:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        temp = target + '.tmp'
        with open(temp, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, allow_nan=False)
        os.replace(temp, target)


def reserve_budget(self, to_key, amount, birthday_date):
    from datetime import date
    amount = money(amount)
    bday = date.fromisoformat(birthday_date)
    if bday <= datetime.now(timezone(timedelta(hours=8))).date():
        return {'ok': False, 'error': '生日日期须晚于今天'}
    users = self.find_user(to_key)
    if len(users) != 1 or users[0]['user_id'] == self.current_user_id:
        return {'ok': False, 'error': '收礼人必须唯一且为他人'}
    if amount < 300 or amount > self.get_account()['balance']:
        return {'ok': False, 'error': '模拟鲜花和蛋糕共需300元，预算不足或余额不足'}
    rid = 'R' + uuid.uuid4().hex[:12]
    self._data['accounts'][self.current_user_id]['balance'] = round(self.get_account()['balance'] - amount, 2)
    reservation = {'id': rid, 'owner_id':self.current_user_id, 'to': users[0]['name'], 'amount': amount, 'birthday_date': birthday_date,
                   'order_date': (bday - timedelta(days=2)).isoformat(), 'status': 'held'}
    self._data.setdefault('reservations', {})[rid] = reservation
    self.commit(amount, rid)
    return {'ok': True, **reservation}


def place_gift_order(self, reservation_id, kind):
    r = self._data.get('reservations', {}).get(reservation_id)
    if not r or r.get('owner_id','U1001')!=self.current_user_id or r['status'] != 'held' or kind not in ('鲜花', '蛋糕'):
        return {'ok': False, 'error': '有效资金预留或商品类型缺失'}
    orders = self._data['orders'].setdefault(self.current_user_id, [])
    if any(o.get('reservation_id') == reservation_id and o['product'] == kind for o in orders):
        return {'ok': False, 'error': '重复预订单'}
    oid = 'G' + uuid.uuid4().hex[:10]
    order = {'order_id': oid, 'product': kind, 'amount': 150, 'status': '已预约',
             'progress': '等待预约日期', 'refund_status': '无', 'ts': time.time(),
             'reservation_id': reservation_id, 'order_date': r['order_date'], 'delivery_date': r['birthday_date']}
    orders.append(order)
    return {'ok': True, **order}


def finalize_birthday(self, reservation_id):
    r = self._data.get('reservations', {}).get(reservation_id)
    orders = [o for o in self.order_list() if o.get('reservation_id') == reservation_id]
    if not r or r.get('owner_id','U1001')!=self.current_user_id or {o['product'] for o in orders} != {'鲜花', '蛋糕'}:
        return {'ok': False, 'error': '预约订单未齐备'}
    task = {'task_id': 'BG' + uuid.uuid4().hex[:10], 'owner_id':self.current_user_id, 'kind': 'birthday', 'reservation_id': reservation_id,
            'execute_ts': datetime.fromisoformat(r['order_date']).replace(tzinfo=timezone(timedelta(hours=8))).timestamp(),
            'status': '待执行', 'amount': 300, 'to_name': r['to']}
    self._data['scheduled_tasks'].append(task)
    return {'ok': True, 'task': task, 'reservation': r, 'orders': orders,
            'message': '预算已预留，已预约。仅在本地模拟时钟推进后下单，无真实商户交易'}


def run_due(self, now=None):
    """Operator controlled sandbox clock. Never exposed as an LLM tool."""
    if self.security_state().get('security_locked'):
        return [{'ok':False,'error':'安全锁定中，定时任务暂停'}]
    now = now or time.time()
    results = []
    with self._lock:
        for task in self._data['scheduled_tasks']:
            if task.get('owner_id','U1001')!=self.current_user_id or task['status'] != '待执行' or task['execute_ts'] > now:
                continue
            if task.get('kind') == 'birthday':
                r = self._data['reservations'][task['reservation_id']]
                r['status'] = 'spent'
                refund = r['amount'] - 300
                self._data['accounts'][self.current_user_id]['balance'] += refund
                for o in self._data['orders'][self.current_user_id]:
                    if o.get('reservation_id') == r['id']:
                        o.update(status='已下单', progress='模拟商户已接单')
                task['status'] = '已执行'
                results.append({'ok': True, 'task_id': task['task_id'], 'spent': 300, 'released': refund})
            else:
                result = self.transfer(task['to_name'], task['amount'], task['currency'], task.get('note', ''), '已授权定时转账')
                task.update(status='已执行' if result.get('ok') else '执行失败', result=result)
                results.append(result)
        if results:
            self._revision=getattr(self,'_revision',0)+1
            from ..agent.security import get_audit
            get_audit().record({'operation':'scheduled_execution','risk':'HIGH','success':all(r.get('ok') for r in results),'note':'定时任务沙箱执行','results':results})
        self.save()
    return results


class DigitalEnvironmentMixin:
    """Explicit environment adapter shared by the sandbox bank core."""
    daily_committed = daily_committed
    commit = commit
    observe = observe
    snapshot = snapshot
    restore = restore
    save = save
    reserve_budget = reserve_budget
    place_gift_order = place_gift_order
    finalize_birthday = finalize_birthday
    run_due = run_due
