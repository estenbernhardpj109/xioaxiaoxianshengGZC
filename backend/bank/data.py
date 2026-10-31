# -*- coding: utf-8 -*-
"""
bank/data.py — 脱敏模拟银行数据

数据合规说明（符合赛题《四、数据规范》）：
- 本文件所有用户、账户、交易、理财、卡片、订阅、订单数据均为**程序生成的虚构数据**。
- 不含任何真实客户信息、真实姓名、真实手机号或真实交易记录。
- 人名使用常见中文姓氏+虚构名字；手机号、卡号为随机生成且已脱敏（非真实号段）。
- 若在生产环境使用，需替换为经授权的真实数据并提供数据来源合法性证明。
"""

import copy
import time

# 固定数据快照基准。业务动作和授权仍使用实际时间。
from datetime import datetime, timezone, timedelta
NOW = datetime(2026,10,31,12,tzinfo=timezone(timedelta(hours=8))).timestamp()


def _days_ago(d):
    return NOW - d * 86400


def _hours_ago(h):
    return NOW - h * 3600


def build_initial_data():
    """返回一份全新的、深拷贝的初始模拟数据（每次调用独立，避免并发串改）。"""
    data = {
        # 当前登录用户（Agent 操作主体，防越权以该 user_id 为边界）
        "current_user_id": "U1001",

        # 用户表（仅虚构数据）
        "users": {
            "U1001": {
                "user_id": "U1001",
                "name": "张明",
                "phone": "138****6021",
                "phone_full": "13800006021",  # 仅供同名/手机号转账匹配用，演示脱敏
                "remark": "我自己",
                "id_card_mask": "510***********1234",
            },
            "U1002": {
                "user_id": "U1002",
                "name": "李婷",
                "phone": "139****8830",
                "phone_full": "13900008830",
                "remark": "婷姐",
                "id_card_mask": "320***********5678",
            },
            "U1003": {
                "user_id": "U1003",
                "name": "王浩",
                "phone": "137****2256",
                "phone_full": "13700002256",
                "remark": "老王",
                "id_card_mask": "110***********9012",
            },
            "U1004": {
                "user_id": "U1004",
                "name": "陈晓",
                "phone": "135****7741",
                "phone_full": "13500007741",
                "remark": "晓晓",
                "id_card_mask": "440***********3456",
            },
        },

        # 账户表
        "accounts": {
            "U1001": {
                "user_id": "U1001",
                "account_no": "6225****1001",
                "balance": 86532.50,
                "currency": "CNY",
                "type": "活期储蓄",
            },
            "U1002": {"user_id": "U1002", "account_no": "6225****1002", "balance": 12050.00, "currency": "CNY", "type": "活期储蓄"},
            "U1003": {"user_id": "U1003", "account_no": "6225****1003", "balance": 4320.75, "currency": "CNY", "type": "活期储蓄"},
            "U1004": {"user_id": "U1004", "account_no": "6225****1004", "balance": 99860.00, "currency": "CNY", "type": "活期储蓄"},
        },

        # 交易流水（近 60 天样本，虚构）
        "transactions": [
            # 张明（U1001）的消费流水
            {"txn_id": "T0001", "user_id": "U1001", "counterparty": "盒马鲜生", "amount": 218.40, "direction": "out", "category": "餐饮食品", "channel": "扫码支付", "ts": _days_ago(2)},
            {"txn_id": "T0002", "user_id": "U1001", "counterparty": "滴滴出行", "amount": 36.00, "direction": "out", "category": "交通出行", "channel": "APP", "ts": _days_ago(2)},
            {"txn_id": "T0003", "user_id": "U1001", "counterparty": "工资代发", "amount": 15800.00, "direction": "in", "category": "收入", "channel": "代发", "ts": _days_ago(10)},
            {"txn_id": "T0004", "user_id": "U1001", "counterparty": "美团外卖", "amount": 45.50, "direction": "out", "category": "餐饮食品", "channel": "APP", "ts": _days_ago(5)},
            {"txn_id": "T0005", "user_id": "U1001", "counterparty": "京东商城", "amount": 1299.00, "direction": "out", "category": "购物消费", "channel": "APP", "ts": _days_ago(8)},
            {"txn_id": "T0006", "user_id": "U1001", "counterparty": "国家电网", "amount": 168.30, "direction": "out", "category": "生活缴费", "channel": "代扣", "ts": _days_ago(15)},
            {"txn_id": "T0007", "user_id": "U1001", "counterparty": "星巴克", "amount": 39.00, "direction": "out", "category": "餐饮食品", "channel": "扫码支付", "ts": _days_ago(1)},
            {"txn_id": "T0008", "user_id": "U1001", "counterparty": "未知商户X", "amount": 4999.00, "direction": "out", "category": "疑似异常", "channel": "深夜支付", "ts": _hours_ago(3)},  # 用于异常识别演示
            {"txn_id": "T0009", "user_id": "U1001", "counterparty": "淘宝", "amount": 320.00, "direction": "out", "category": "购物消费", "channel": "APP", "ts": _days_ago(20)},
            {"txn_id": "T0010", "user_id": "U1001", "counterparty": "房租转账", "amount": 3500.00, "direction": "out", "category": "居住", "channel": "转账", "ts": _days_ago(25)},
        ],

        # 理财产品（虚构）
        "wealth_products": [
            {"product_id": "W001", "name": "稳盈90天", "risk_level": "低", "expected_return": "2.85%", "min_amount": 1000, "type": "固收类", "term": "90天"},
            {"product_id": "W002", "name": "增利180天", "risk_level": "中", "expected_return": "3.90%", "min_amount": 5000, "type": "混合类", "term": "180天"},
            {"product_id": "W003", "name": "权益优选", "risk_level": "高", "expected_return": "浮动", "min_amount": 10000, "type": "权益类", "term": "无固定"},
            {"product_id": "W004", "name": "货币基金A", "risk_level": "低", "expected_return": "1.95%", "min_amount": 100, "type": "货币类", "term": "T+0"},
        ],

        # 持仓
        "holdings": {
            "U1001": [
                {"product_id": "W001", "amount": 20000, "buy_ts": _days_ago(30)},
                {"product_id": "W004", "amount": 5000, "buy_ts": _days_ago(12)},
            ]
        },

        # 卡片（虚构）
        "cards": {
            "U1001": [
                {"card_id": "C1001", "type": "借记卡", "status": "正常", "daily_limit": 50000, "used_today": 2306.20},
                {"card_id": "C1002", "type": "信用卡", "status": "正常", "credit_limit": 30000, "used_limit": 8600, "available": 21400},
            ]
        },

        # 订阅/代扣（虚构）
        "subscriptions": {
            "U1001": [
                {"sub_id": "S001", "merchant": "腾讯视频会员", "amount": 25.00, "cycle": "月", "next_charge": _days_ago(-3), "status": "生效中"},
                {"sub_id": "S002", "merchant": "网易云音乐", "amount": 15.00, "cycle": "月", "next_charge": _days_ago(-8), "status": "生效中"},
                {"sub_id": "S003", "merchant": "某云盘超级会员", "amount": 198.00, "cycle": "年", "next_charge": _days_ago(-20), "status": "生效中"},
            ]
        },

        # 订单（虚构）
        "orders": {
            "U1001": [
                {"order_id": "O1001", "product": "蓝牙耳机", "amount": 299.00, "status": "已发货", "progress": "运输中-派送", "refund_status": "无", "ts": _days_ago(4)},
                {"order_id": "O1002", "product": "机械键盘", "amount": 459.00, "status": "已完成", "progress": "已签收", "refund_status": "无", "ts": _days_ago(18)},
                {"order_id": "O1003", "product": "运动鞋", "amount": 528.00, "status": "退款中", "progress": "商家已同意-退款到账", "refund_status": "退款中-预计3日", "ts": _days_ago(2)},
            ]
        },

        # 待执行定时任务（定时转账等）
        "scheduled_tasks": [],

        # 新客活动（虚构）：人生阶段 1000 元礼包
        "new_customer_activity": {
            "name": "人生阶段陪伴礼包",
            "budget": 1000,
            "nodes": ["毕业启航", "职场新人", "组建家庭", "宝贝降临", "乐享银龄"],
            "granted": {},  # user_id -> 已领取节点
        },
    }
    # Reproducible enrichment for cross-table visual demonstrations.
    import random
    from datetime import datetime, timezone, timedelta
    rng = random.Random(2026)
    anchor = datetime(2026,10,31,12,tzinfo=timezone(timedelta(hours=8)))
    merchants = [('餐饮食品','午间食堂','扫码支付',35), ('交通出行','城市轨道','APP',8),
                 ('购物消费','百货商城','APP',280), ('生活缴费','水电服务','代扣',150)]
    for month in range(1,11):
        for j in range(32):
            cat, merchant, channel, avg = rng.choice(merchants)
            dt = anchor.replace(month=month,day=rng.randint(1,28),hour=rng.choice([8,12,18,20]))
            data['transactions'].append({'txn_id':f'SYN{month:02}{j:03}', 'user_id':'U1001',
                'counterparty':merchant,'amount':round(avg*rng.uniform(.5,2),2),'direction':'out',
                'category':cat,'channel':channel,'ts':dt.timestamp()})
        dt=anchor.replace(month=month,day=10)
        data['transactions'].append({'txn_id':f'SAL{month:02}', 'user_id':'U1001','counterparty':'模拟工资',
                'amount':15800,'direction':'in','category':'收入','channel':'代发','ts':dt.timestamp()})
        for sub in data['subscriptions']['U1001'][:2]:
            data['transactions'].append({'txn_id':f'DEB{month:02}{sub["sub_id"]}', 'user_id':'U1001',
                'counterparty':sub['merchant'],'amount':sub['amount'],'direction':'out',
                'category':'订阅服务','channel':'代扣','ts':dt.replace(day=15).timestamp()})
    # Keep the original ten records as supplied prototype samples. Their timestamps
    # are anchored to the same reproducible dataset clock rather than import time.
    for t in data['transactions'][:10]:
        t['ts'] = anchor.timestamp() - (NOW - t['ts'])
    return data


def get_initial_data():
    return copy.deepcopy(build_initial_data())
