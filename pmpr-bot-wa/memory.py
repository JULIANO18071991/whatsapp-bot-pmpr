from collections import defaultdict, deque
from typing import Deque, Dict

class Memory:
    """Memória simples em processo (até N últimas mensagens por usuário)."""
    def __init__(self, max_msgs: int = 3):
        self.max_msgs = max_msgs
        self._data: Dict[str, Deque[str]] = defaultdict(lambda: deque(maxlen=self.max_msgs))

    def add_msg(self, user: str, msg: str) -> None:
        self._data[user].append(msg)

    def get_context(self, user: str) -> str:
        if user not in self._data or not self._data[user]:
            return ""
        return "\n".join(self._data[user])
