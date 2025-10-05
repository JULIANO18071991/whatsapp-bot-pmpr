# memory.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from collections import defaultdict, deque
from typing import Deque, Dict, List, Literal, TypedDict
import threading

Role = Literal["user", "assistant"]

class Msg(TypedDict):
    role: Role
    content: str

class Memory:
    def __init__(self, max_msgs: int = 6) -> None:
        self.max_msgs = max_msgs
        self._data: Dict[str, Deque[Msg]] = defaultdict(lambda: deque(maxlen=self.max_msgs))
        self._lock = threading.Lock()

    def add_user_msg(self, user: str, msg: str) -> None:
        self._append(user, "user", msg)

    def add_assistant_msg(self, user: str, msg: str) -> None:
        self._append(user, "assistant", msg)

    # aliases legado
    def add_msg(self, user: str, msg: str) -> None: self.add_user_msg(user, msg)
    def add(self, user: str, msg: str) -> None: self.add_user_msg(user, msg)

    def get_context(self, user: str) -> List[Msg]:
        with self._lock:
            if user not in self._data:
                return []
            return list(self._data[user])

    def get(self, user: str) -> List[Msg]:
        return self.get_context(user)

    def clear(self, user: str | None = None) -> None:
        with self._lock:
            if user is None:
                self._data.clear()
            else:
                self._data.pop(user, None)

    def _append(self, user: str, role: Role, msg: str) -> None:
        if not user:
            return
        text = "" if msg is None else str(msg).strip()
        if not text:
            return
        with self._lock:
            self._data[user].append({"role": role, "content": text})
