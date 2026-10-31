"""The tool boundary is authoritative. Model output is always untrusted."""
import math
from decimal import Decimal, InvalidOperation


def money(value):
    if isinstance(value, bool):
        raise ValueError('金额必须是正数')
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d <= 0 or d > Decimal('100000000') or d != d.quantize(Decimal('.01')):
            raise ValueError('金额必须为正数且最多两位小数')
        return float(d)
    except (InvalidOperation, TypeError):
        raise ValueError('无效金额')


def validate(operation, params):
    from .tools import TOOL_PARAM_SCHEMAS
    if operation not in TOOL_PARAM_SCHEMAS or not isinstance(params, dict):
        raise ValueError('未知工具或参数格式错误')
    _, props, required = TOOL_PARAM_SCHEMAS[operation]
    if set(params) - set(props):
        raise ValueError('拒绝未声明参数')
    for name in required:
        if name not in params or params[name] is None or params[name] == '':
            raise ValueError('缺少参数 ' + name)
    for name, val in params.items():
        spec = props[name]
        kind = spec.get('type')
        if val is None:
            raise ValueError('参数不能为空 ' + name)
        if kind == 'string' and (not isinstance(val, str) or len(val) > 500):
            raise ValueError('无效文本 ' + name)
        if kind == 'number' and (isinstance(val, bool) or not isinstance(val, (float, int)) or not math.isfinite(val)):
            raise ValueError('无效数值 ' + name)
        if kind == 'boolean' and type(val) is not bool:
            raise ValueError('无效布尔值 ' + name)
        if 'enum' in spec and val not in spec['enum']:
            raise ValueError('无效选项 ' + name)
        if name in ('amount', 'new_limit'):
            money(val)
    return params
