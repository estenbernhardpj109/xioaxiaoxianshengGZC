"""Server-side data minimisation for leadership views; raw identifiers never leave SQL."""
import hashlib
import re


def alias(value, prefix='客户'):
    return prefix + hashlib.sha256(str(value).encode()).hexdigest()[:6].upper()


def mask_name(value):
    value = str(value or '')
    return value[:1] + '*' * max(1, len(value) - 1) if value else ''


def mask_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[:3] + '****' + digits[-4:] if len(digits) >= 7 else '***'


def mask_account(value):
    value = str(value or '')
    return '**** ' + value[-4:] if value else '****'


def redact(value):
    """Conservative free-text redaction, also used for operational audit comments."""
    text = str(value or '')
    text = re.sub(r'(?<!\d)1\d{10}(?!\d)', lambda m: mask_phone(m[0]), text)
    text = re.sub(r'(?<!\d)\d{15,19}[Xx]?(?!\d)', '****', text)
    text = re.sub(r'(?i)(sk-[a-z0-9_-]{12,}|(?:password|密码|otp|token)\s*[:=：]\s*\S+)', '[已隐藏]', text)
    return text[:200]


def browser_payload(value):
    """Mask sensitive identifiers in customer-facing DTOs; never truncate answers."""
    if isinstance(value,dict):
        out={}
        for key,item in value.items():
            if key in ('phone_full','password_hash','token_hash','owner_id'):continue
            if key in ('account_no','account_number'):out[key]=mask_account(item)
            elif key in ('phone','mobile'):out[key]=mask_phone(item) if item else ''
            else:out[key]=browser_payload(item)
        return out
    if isinstance(value,list):return [browser_payload(x) for x in value]
    if isinstance(value,str):
        value=re.sub(r'(?<!\d)1\d{10}(?!\d)',lambda m:mask_phone(m[0]),value)
        value=re.sub(r'(?<!\d)\d{15,19}[Xx]?(?!\d)','****',value)
        return re.sub(r'(?i)(sk-[a-z0-9_-]{12,}|(?:password|密码|otp|token)\s*[:=：]\s*\S+)','[已隐藏]',value)
    return value
