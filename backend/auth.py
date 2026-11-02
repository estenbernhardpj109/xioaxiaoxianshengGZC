"""Local sandbox identity. MySQL credentials, opaque sessions and account binding.

Registration is not bank account opening or KYC. No phone/ID is collected.
The explicitly selected shared demonstration identity is separate from customers.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time

from .storage import get_store, dumps
from .privacy import alias

IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 28800
_demo_sessions = {}


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=131072,
                           r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32)
    return 'scrypt$131072$8$1$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(value).decode()


def password_matches(password, encoded):
    try:
        _, n, r, p, salt, expected = encoded.split('$')
        if (n, r, p) != ('131072', '8', '1'):
            return False
        actual = password_hash(password, base64.b64decode(salt)).split('$')[-1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_registration(body):
    username, display, password = (body.get(k) for k in ('username', 'display_name', 'password'))
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{2,31}', username):
        raise AuthError('用户名需以字母开头，使用3–32位字母、数字或下划线')
    if not isinstance(display, str) or not re.fullmatch(r'[\w\u4e00-\u9fff ·-]{2,20}', display.strip()):
        raise AuthError('昵称需为2–20位中文、字母或数字，请勿填写真实身份信息')
    if not isinstance(password, str) or not 12 <= len(password) <= 72:
        raise AuthError('登录密码长度需为12–72位')
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise AuthError('登录密码需同时包含字母与数字，可使用空格或符号')
    if password != body.get('confirm_password'):
        raise AuthError('两次输入的密码不一致')
    if body.get('accepted') is not True:
        raise AuthError('请阅读并同意沙箱使用说明与隐私说明')
    return username.lower(), display.strip(), password


def limit_attempt(store, category, key, maximum, period, consume=True):
    """Persisted, browser-independent limits. No raw username/IP in the limit table."""
    digest = hashlib.sha256((category + ':' + key).encode()).hexdigest()
    now = time.time()
    with store.transaction() as cur:
        cur.execute('INSERT IGNORE INTO zx_auth_attempts VALUES (%s,0,%s)', (digest, now + period))
        cur.execute('SELECT failures,reset_at FROM zx_auth_attempts WHERE bucket=%s FOR UPDATE', (digest,))
        row = cur.fetchone()
        count = row['failures'] if row['reset_at'] > now else 0
        reset = row['reset_at'] if row['reset_at'] > now else now + period
        blocked = count >= maximum
        if consume and not blocked:
            cur.execute('UPDATE zx_auth_attempts SET failures=%s,reset_at=%s WHERE bucket=%s', (count + 1, reset, digest))
            if category=='login-user':
                cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                            (now,'auth_rejected','B08',digest[:16],'IDENTITY',dumps({'result':'rejected'})))
    if blocked:
        raise AuthError('尝试过于频繁，请稍后再试', 429)


def register(core, body, origin='local'):
    store = get_store()
    if store is None:
        raise AuthError('用户注册需要MySQL存储模式；文件模式仅支持明确选择演示体验', 503)
    username, display, password = validate_registration(body)
    limit_attempt(store, 'register', origin, 6, 60)
    encoded = password_hash(password)
    uid = 'U' + secrets.token_hex(12)
    identity = (uid, username, display, encoded, time.time())
    # The bank account and login identity commit in the same InnoDB transaction.
    before, revision = core.snapshot(), getattr(core, '_revision', 0)
    try:
        core._data['users'][uid] = {'user_id': uid, 'name': display, 'phone': '', 'remark': '注册模拟账户'}
        core._data['accounts'][uid] = {'user_id': uid, 'account_id': 'A' + uid[1:],
            'account_no': '6226' + str(secrets.randbelow(10**12)).zfill(12), 'balance': 0.0, 'currency': 'CNY'}
        for name in ('cards', 'holdings', 'subscriptions', 'orders'):
            core._data[name][uid] = []
        core._revision = revision + 1
        store.persist(core, [], [], identity=identity)
    except Exception as e:
        core._data, core._revision = before, revision
        # Unique username failures have no financial effect; other SQL errors fail closed.
        if getattr(e, 'errno', None) == 1062:
            raise AuthError('此用户名暂不可用，请更换用户名或直接登录', 409) from None
        raise
    return {'ok': True, 'message': '注册成功，请登录。已创建独立的零余额模拟账户。', 'username': username}


def _new_session(uid, demo=False):
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    store = get_store()
    if store:
        with store.transaction() as cur:
            cur.execute('DELETE FROM zx_auth_sessions WHERE expires_at<=%s OR last_seen<%s', (now, now - IDLE_SECONDS))
            cur.execute('INSERT INTO zx_auth_sessions VALUES (%s,%s,%s,%s,%s,%s)',
                        (digest, uid, bool(demo), now, now, now + ABSOLUTE_SECONDS))
            cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                        (now,'auth_login','B08',alias(uid),'IDENTITY',dumps({'result':'accepted','scope':'demo' if demo else 'customer'})))
    else:
        _demo_sessions[digest] = {'user_id': uid, 'is_demo': True, 'created_at': now,
                                  'last_seen': now, 'expires_at': now + ABSOLUTE_SECONDS}
    return token


def login(body, origin='local'):
    store = get_store()
    if store is None:
        raise AuthError('用户登录需要MySQL模式；可选择演示体验', 503)
    username, password = body.get('username'), body.get('password')
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 72:
        raise AuthError('用户名或密码不正确', 401)
    username = username.lower().strip()
    limit_attempt(store, 'login-origin', origin, 40, 60)
    limit_attempt(store, 'login-user', username, 5, 60, consume=False)
    rows = store.query('SELECT user_id,password_hash FROM zx_auth_identities WHERE username=%s', (username,))
    # Perform the same expensive KDF for unknown usernames to reduce enumeration.
    valid = password_matches(password, rows[0]['password_hash']) if rows else hmac.compare_digest(password_hash(password), '')
    if not valid:
        limit_attempt(store, 'login-user', username, 5, 60)
        raise AuthError('用户名或密码不正确', 401)
    bucket = hashlib.sha256(('login-user:' + username).encode()).hexdigest()
    with store.transaction() as cur:
        cur.execute('DELETE FROM zx_auth_attempts WHERE bucket=%s', (bucket,))
    return _new_session(rows[0]['user_id'])


def demo_login(origin='local'):
    store=get_store()
    if store:limit_attempt(store,'demo-origin',origin,30,60)
    return _new_session('U1001', True)


def current(token, touch=True):
    if not token or not isinstance(token, str) or len(token) > 128:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    store = get_store()
    rows = store.query('SELECT * FROM zx_auth_sessions WHERE token_hash=%s', (digest,)) if store else []
    row = rows[0] if rows else _demo_sessions.get(digest) if not store else None
    now = time.time()
    if not row or row['expires_at'] <= now or row['last_seen'] < now - IDLE_SECONDS:
        return None
    demo = bool(row['is_demo'])
    if demo:
        person = {'display_name': '演示体验用户', 'username': 'demo'}
    else:
        people = store.query('SELECT display_name,username FROM zx_auth_identities WHERE user_id=%s', (row['user_id'],))
        if not people:
            return None
        person = people[0]
    if touch and row['last_seen'] < now - 30:
        if store:
            with store.transaction() as cur:
                cur.execute('UPDATE zx_auth_sessions SET last_seen=%s WHERE token_hash=%s', (now, digest))
        else:
            row['last_seen'] = now
    return {**person, 'user_id': row['user_id'], 'is_demo': demo,
            'can_view_ops': demo, 'expires_at': row['expires_at']}


def logout(token):
    if not token:
        return
    digest = hashlib.sha256(token.encode()).hexdigest()
    store = get_store()
    if store:
        with store.transaction() as cur:
            cur.execute('DELETE FROM zx_auth_sessions WHERE token_hash=%s', (digest,))
            if cur.rowcount:
                cur.execute('INSERT INTO zx_ops_events(event_ts,event_type,branch_id,ref_id,actor,payload) VALUES (%s,%s,%s,%s,%s,%s)',
                            (time.time(),'auth_logout','B08',None,'IDENTITY',dumps({'result':'revoked'})))
    else:
        _demo_sessions.pop(digest, None)


def public_user(person):
    return {k: person[k] for k in ('display_name', 'username', 'is_demo', 'can_view_ops', 'expires_at')}


def safe_next(value, person=None):
    allowed = ('/app', '/assistant', '/ops', '/app#dashboard', '/app#workspace')
    target = value if value in allowed else '/app'
    return '/app' if target == '/ops' and person and not person['can_view_ops'] else target
