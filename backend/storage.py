"""MySQL unit of work: state, projections, audit and request metrics commit together.

SQL credentials are only read from environment. Never fall back silently on DB errors.
The JSON backend remains an explicit isolated evaluation mode.
"""
import copy
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager

_store = None
_local = threading.local()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


DDL = [
    """CREATE TABLE IF NOT EXISTS zx_auth_identities (user_id VARCHAR(48) PRIMARY KEY, username VARCHAR(32) NOT NULL UNIQUE, display_name VARCHAR(80) NOT NULL, password_hash VARCHAR(200) NOT NULL, created_at DOUBLE NOT NULL) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_auth_sessions (token_hash CHAR(64) PRIMARY KEY, user_id VARCHAR(48) NOT NULL, is_demo BOOLEAN NOT NULL, created_at DOUBLE NOT NULL, last_seen DOUBLE NOT NULL, expires_at DOUBLE NOT NULL, INDEX ix_session_expiry(expires_at)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_auth_attempts (bucket CHAR(64) PRIMARY KEY, failures INT NOT NULL, reset_at DOUBLE NOT NULL) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_schema_version (version INT PRIMARY KEY, applied_at DOUBLE NOT NULL) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_bank_state (id INT PRIMARY KEY, version BIGINT NOT NULL, payload JSON NOT NULL, updated_at DOUBLE NOT NULL) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_users (user_id VARCHAR(48) PRIMARY KEY, name_mask VARCHAR(80), phone_mask VARCHAR(32), branch_id VARCHAR(16) NOT NULL) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_accounts (user_id VARCHAR(48) PRIMARY KEY, account_mask VARCHAR(40), balance DECIMAL(18,2) NOT NULL, currency VARCHAR(8) NOT NULL, branch_id VARCHAR(16) NOT NULL) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_transactions (txn_id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(48) NOT NULL, branch_id VARCHAR(16) NOT NULL, amount DECIMAL(18,2) NOT NULL, direction VARCHAR(8) NOT NULL, category VARCHAR(64), channel VARCHAR(64), event_ts DOUBLE NOT NULL, INDEX ix_tx_time(branch_id,event_ts)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_resources (kind VARCHAR(32) NOT NULL, resource_id VARCHAR(96) NOT NULL, owner_id VARCHAR(48), status VARCHAR(40), amount DECIMAL(18,2), payload JSON NOT NULL, PRIMARY KEY(kind,resource_id)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_audit_log (hash CHAR(64) PRIMARY KEY, event_ts DOUBLE NOT NULL, operation VARCHAR(64), risk VARCHAR(16), success BOOLEAN, payload LONGTEXT NOT NULL, sequence_id BIGINT NOT NULL AUTO_INCREMENT UNIQUE, INDEX ix_audit_time(event_ts)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_requests (request_id VARCHAR(64) PRIMARY KEY, session_alias VARCHAR(48), source VARCHAR(24) NOT NULL, scenario VARCHAR(32) NOT NULL, status VARCHAR(24) NOT NULL, duration_ms DOUBLE NOT NULL, event_ts DOUBLE NOT NULL, branch_id VARCHAR(16) NOT NULL, INDEX ix_req_time(branch_id,event_ts)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_devices (device_id VARCHAR(48) PRIMARY KEY, branch_id VARCHAR(16) NOT NULL, name VARCHAR(80) NOT NULL, kind VARCHAR(40) NOT NULL, source VARCHAR(24) NOT NULL, status VARCHAR(24) NOT NULL, last_seen DOUBLE NOT NULL, latency_ms DOUBLE, cpu_pct DOUBLE, memory_pct DOUBLE, INDEX ix_device_branch(branch_id,status)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_device_samples (id BIGINT AUTO_INCREMENT PRIMARY KEY, device_id VARCHAR(48) NOT NULL, event_ts DOUBLE NOT NULL, status VARCHAR(24), latency_ms DOUBLE, cpu_pct DOUBLE, memory_pct DOUBLE, INDEX ix_sample_time(event_ts,device_id)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_work_orders (ticket_id VARCHAR(48) PRIMARY KEY, source_key VARCHAR(128) UNIQUE NOT NULL, device_id VARCHAR(48), branch_id VARCHAR(16) NOT NULL, title VARCHAR(120) NOT NULL, priority VARCHAR(8) NOT NULL, status VARCHAR(24) NOT NULL, created_at DOUBLE NOT NULL, assigned_at DOUBLE, resolved_at DOUBLE, sla_minutes INT NOT NULL, assignee VARCHAR(40), resolution VARCHAR(200), version INT NOT NULL DEFAULT 1, INDEX ix_ticket_scope(branch_id,created_at,status)) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS zx_ops_events (event_id BIGINT AUTO_INCREMENT PRIMARY KEY, event_ts DOUBLE NOT NULL, event_type VARCHAR(40) NOT NULL, branch_id VARCHAR(16) NOT NULL, ref_id VARCHAR(64), actor VARCHAR(48), payload LONGTEXT NOT NULL, INDEX ix_ops_time(event_ts)) ENGINE=InnoDB""",
]


def branch_for_user(uid):
    # A documented synthetic assignment, not a real bank's organisation mapping.
    return {'U1001': 'B01', 'U1002': 'B02', 'U1003': 'B03', 'U1004': 'B04'}.get(uid, 'B08')


class MySQLStore:
    def __init__(self):
        from mysql.connector.pooling import MySQLConnectionPool
        database = os.environ.get('MYSQL_DATABASE', 'root')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', database):
            raise ValueError('MYSQL_DATABASE must be an SQL identifier')
        self.database = database
        self.config = dict(host=os.environ.get('MYSQL_HOST', '127.0.0.1'),
                           port=int(os.environ.get('MYSQL_PORT', '3306')),
                           user=os.environ.get('MYSQL_USER', 'root'),
                           password=os.environ.get('MYSQL_PASSWORD', ''),
                           connection_timeout=5, charset='utf8mb4', use_unicode=True)
        if not self.config['password']:
            raise RuntimeError('请通过 MYSQL_PASSWORD 或启动脚本提供数据库密码')
        import mysql.connector
        cn = mysql.connector.connect(**self.config)
        try:
            cur = cn.cursor()
            cur.execute(f'CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            cur.close()
        finally:
            cn.close()
        self.pool = MySQLConnectionPool(pool_name='zx_' + str(os.getpid()), pool_size=5,
                                        pool_reset_session=True, database=database, **self.config)
        with self.transaction() as cur:
            for sql in DDL:
                cur.execute(sql)
            cur.execute("SHOW COLUMNS FROM zx_audit_log LIKE 'sequence_id'")
            if not cur.fetchall():
                cur.execute('ALTER TABLE zx_audit_log ADD COLUMN sequence_id BIGINT NOT NULL AUTO_INCREMENT UNIQUE')
            cur.execute("SHOW COLUMNS FROM zx_audit_log LIKE 'payload'")
            if cur.fetchone()['Type'].lower()=='json':
                cur.execute('ALTER TABLE zx_audit_log MODIFY payload LONGTEXT NOT NULL')
            cur.execute("SHOW COLUMNS FROM zx_ops_events LIKE 'payload'")
            if cur.fetchone()['Type'].lower()=='json':
                cur.execute('ALTER TABLE zx_ops_events MODIFY payload LONGTEXT NOT NULL')
            cur.execute('SELECT version FROM zx_schema_version WHERE version=4')
            self.legacy_migration_allowed=not bool(cur.fetchall())
            cur.execute('INSERT IGNORE INTO zx_schema_version VALUES (1,%s)', (time.time(),))
            cur.execute('INSERT IGNORE INTO zx_schema_version VALUES (2,%s)', (time.time(),))
            cur.execute('INSERT IGNORE INTO zx_schema_version VALUES (5,%s)', (time.time(),))
        self.version = None
        self.state_digest = None

    @contextmanager
    def transaction(self):
        cn = self.pool.get_connection()
        cur = cn.cursor(dictionary=True)
        try:
            cn.start_transaction()
            yield cur
            cn.commit()
        except Exception:
            cn.rollback()
            raise
        finally:
            cur.close()
            cn.close()

    def query(self, sql, args=()):
        with self.transaction() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def initialize(self, core, audit):
        rows = self.query('SELECT version,payload FROM zx_bank_state WHERE id=1')
        if rows:
            self.version = rows[0]['version']
            core._data = json.loads(rows[0]['payload'])
            core.current_user_id = core._data['current_user_id']
            self.state_digest = hashlib.sha256(json.dumps(core._data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        else:
            self.persist(core, [], [])
        rows = self.query('SELECT payload FROM zx_audit_log ORDER BY sequence_id')
        if rows:
            decoded=[json.loads(r['payload']) for r in rows]
            previous='0'*64;valid=True
            for item in decoded:
                raw=dict(item);given=raw.pop('hash',None)
                valid=valid and raw.get('prev_hash')==previous and hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False).encode()).hexdigest()==given
                previous=given
            if not valid:
                if not self.legacy_migration_allowed:
                    raise RuntimeError('数据库审计链校验失败，请保留原数据并人工恢复')
                # An explicit one-time migration boundary. Preserve every old byte/value;
                # never silently rehash past outcomes or pretend a broken chain is valid.
                legacy={'sql_records':[r['payload'] for r in rows],'retained_file_records':audit._mem,
                        'reason':'v1 binary JSON number normalization / pre-sequence ordering'}
                archive=dumps(legacy);digest=hashlib.sha256(archive.encode()).hexdigest()
                event={'operation':'audit_storage_migration','risk':'LOW','success':True,
                       'note':'旧审计原文已保留于zx_ops_events迁移归档，新链使用精确文本存储',
                       'legacy_records':len(decoded),'legacy_sha256':digest,'ts':time.time(),'prev_hash':'0'*64}
                event['hash']=hashlib.sha256(json.dumps(event,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                with self.transaction() as cur:
                    cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                                (time.time(),'audit_legacy_archive','B08','AUDIT-V4','SYSTEM',archive))
                    cur.execute('DELETE FROM zx_audit_log')
                    cur.execute('INSERT INTO zx_audit_log(hash,event_ts,operation,risk,success,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                                (event['hash'],event['ts'],event['operation'],event['risk'],True,dumps(event)))
                decoded=[event]
            audit._mem=decoded
        elif audit._mem:
            self.persist(core, audit._mem, [])
        with self.transaction() as cur:
            cur.execute('INSERT IGNORE INTO zx_schema_version VALUES (4,%s)',(time.time(),))
        self.legacy_migration_allowed=False

    def projections(self, cur, data):
        from .privacy import mask_name, mask_phone, mask_account
        for table in ('zx_users', 'zx_accounts', 'zx_transactions', 'zx_resources'):
            cur.execute('DELETE FROM ' + table)
        cur.executemany('INSERT INTO zx_users VALUES (%s,%s,%s,%s)',
                        [(u['user_id'], mask_name(u['name']), mask_phone(u.get('phone_full', u['phone'])), branch_for_user(u['user_id'])) for u in data['users'].values()])
        cur.executemany('INSERT INTO zx_accounts VALUES (%s,%s,%s,%s,%s)',
                        [(uid, mask_account(a['account_no']), a['balance'], a['currency'], branch_for_user(uid)) for uid, a in data['accounts'].items()])
        cur.executemany('INSERT INTO zx_transactions VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                        [(t['txn_id'], t['user_id'], branch_for_user(t['user_id']), t['amount'], t['direction'], t['category'], t['channel'], t['ts']) for t in data['transactions']])
        resources = []
        for kind, key, idkey in [('card','cards','card_id'), ('holding','holdings','product_id'), ('subscription','subscriptions','sub_id'), ('order','orders','order_id')]:
            for uid, values in data.get(key, {}).items():
                for item in values:
                    resources.append((kind, uid + ':' + item[idkey], uid, item.get('status','有效'), item.get('amount',0), dumps(item)))
        for key, values in [('task', data.get('scheduled_tasks', [])), ('reservation', list(data.get('reservations', {}).values())), ('product',data.get('wealth_products', []))]:
            for i, item in enumerate(values):
                resources.append((key, str(item.get('task_id',item.get('id',item.get('product_id',i)))), item.get('owner_id',data['current_user_id']), item.get('status','有效'), item.get('amount',0), dumps(item)))
        if resources:
            cur.executemany('INSERT INTO zx_resources VALUES (%s,%s,%s,%s,%s,%s)', resources)

    def persist(self, core, audit_entries, requests, identity=None):
        now = time.time()
        digest=hashlib.sha256(json.dumps(core._data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        changed=digest!=self.state_digest
        with self.transaction() as cur:
            if identity:
                cur.execute('INSERT INTO zx_auth_identities VALUES (%s,%s,%s,%s,%s)', identity)
                from .privacy import alias
                cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                            (now,'auth_registered','B08',alias(identity[0]),'IDENTITY',dumps({'result':'accepted','scope':'customer'})))
            if self.version is None:
                cur.execute('INSERT INTO zx_bank_state VALUES (1,1,%s,%s)', (dumps(core._data), now))
                new_version = 1
            elif changed:
                cur.execute('UPDATE zx_bank_state SET version=version+1,payload=%s,updated_at=%s WHERE id=1 AND version=%s', (dumps(core._data), now, self.version))
                if cur.rowcount != 1:
                    raise RuntimeError('数据库版本冲突，拒绝覆盖其他进程状态，请重启单实例服务')
                new_version = self.version + 1
            else:
                cur.execute('SELECT version FROM zx_bank_state WHERE id=1 FOR SHARE')
                current=cur.fetchone()
                if not current or current['version']!=self.version:
                    raise RuntimeError('数据库状态已被其他进程修改，请重启单实例服务')
                new_version=self.version
            if changed:self.projections(cur, core._data)
            for entry in audit_entries:
                cur.execute('INSERT IGNORE INTO zx_audit_log(hash,event_ts,operation,risk,success,payload) VALUES (%s,%s,%s,%s,%s,%s)', (entry['hash'], entry['ts'], entry.get('operation'),entry.get('risk'),entry.get('success',False),dumps(entry)))
                if entry.get('success') is False or entry.get('note') == '人工接管':
                    from .operations import create_ticket
                    create_ticket(cur, 'audit:' + entry['hash'], branch_for_user(core.current_user_id),
                                  '业务执行需要人工复核', 'P1' if entry.get('risk')=='HIGH' else 'P2', ref_id=entry.get('step_id'))
            for r in requests:
                cur.execute('INSERT INTO zx_requests VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',r)
        self.version = new_version
        self.state_digest = digest


def get_store():
    global _store
    if os.environ.get('BANK_STORAGE', 'json') != 'mysql':
        return None
    if _store is None:
        _store = MySQLStore()
    return _store


def save_core(core):
    store = get_store()
    if store is None:
        return False
    if not getattr(_local, 'active', False):
        store.persist(core, [], [])
    return True


@contextmanager
def unit_of_work(core, audit, manager=None):
    """Hold application snapshots until the SQL transaction has committed successfully."""
    store = get_store()
    if store is None:
        yield []
        return
    state, revision, audit_size = core.snapshot(), getattr(core, '_revision', 0), len(audit._mem)
    sessions = copy.deepcopy(manager._sessions) if manager else None
    pending_requests = []
    _local.active = True
    try:
        yield pending_requests
        store.persist(core, audit._mem[audit_size:], pending_requests)
    except Exception:
        core._data, core._revision = state, revision
        del audit._mem[audit_size:]
        if manager:
            manager._sessions = sessions
        raise
    finally:
        _local.active = False


def audit_to_mysql(entry):
    store = get_store()
    if store is None:
        return False
    if not getattr(_local, 'active', False):
        with store.transaction() as cur:
            cur.execute('INSERT INTO zx_audit_log(hash,event_ts,operation,risk,success,payload) VALUES (%s,%s,%s,%s,%s,%s)', (entry['hash'],entry['ts'],entry.get('operation'),entry.get('risk'),entry.get('success',False),dumps(entry)))
    return True
