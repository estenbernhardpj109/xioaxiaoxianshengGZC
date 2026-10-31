# -*- coding: utf-8 -*-
"""
agent/context.py — 多轮对话上下文与会话管理

维护每个会话的历史、待补全意图、执行中的计划与待确认步骤，
支撑“意图不明确时主动追问澄清”与“确认后继续执行”的多轮交互。
"""

import threading
import time
import uuid


class ConversationSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.history = []          # [{"role":"user"/"agent","text":...}]
        self.pending_intent = None # 等待澄清的意图
        self.pending_entities = {} # 已收集实体
        self.pending_plan = None   # 执行中的计划
        self.last_active = time.time()
        self.snapshot = None
        self.snapshot_version = None
        self.paused = False
        self.challenge = None
        self.failures = 0
        self.locked = False

    def add(self, role, text):
        self.history.append({"role": role, "text": text, "ts": time.time()})
        self.last_active = time.time()

    def summary(self):
        return {
            "session_id": self.session_id,
            "turns": len(self.history),
            "history": self.history[-10:],
        }


class SessionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def get_or_create(self, session_id=None):
        with self._lock:
            if not session_id:
                session_id = "sess_" + uuid.uuid4().hex
            if session_id not in self._sessions:
                self._sessions[session_id] = ConversationSession(session_id)
            return self._sessions[session_id]

    def get(self, session_id):
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self):
        with self._lock:
            return list(self._sessions.keys())


_session_mgr = SessionManager()


def get_session_manager():
    return _session_mgr
