from collections import defaultdict, deque
from typing import Deque, Dict, List, Literal, TypedDict

Role = Literal["user", "assistant"]

class Msg(TypedDict):
    role: Role
    content: str

class Memory:
    """Memória simples (N últimas mensagens por usuário), com role."""
    def __init__(self, max_msgs: int = 6):
        self.max_msgs = max_msgs
        self._data: Dict[str, Deque[Msg]] = defaultdict(lambda: deque(maxlen=self.max_msgs))

    def add_user_msg(self, user: str, msg: str) -> None:
        self._data[user].append({"role": "user", "content": msg})

    def add_assistant_msg(self, user: str, msg: str) -> None:
        self._data[user].append({"role": "assistant", "content": msg})

    def get_context(self, user: str) -> List[Msg]:
        return list(self._data[user]) if user in self._data else []
