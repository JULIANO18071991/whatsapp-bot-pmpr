# memory_redis.py
# -*- coding: utf-8 -*-
import os, json
from typing import List, Literal, TypedDict
import redis

Role = Literal["user", "assistant"]
class Msg(TypedDict):
    role: Role
    content: str

MAX_MSGS = int(os.getenv("MEMORY_MAX_MSGS", "6"))
TTL_SECONDS = int(os.getenv("MEMORY_TTL_SECONDS", "604800"))  # 7 dias

def _client():
    url = os.getenv("REDIS_URL")
    if url:
        return redis.Redis.from_url(url, decode_responses=True)
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD"),
        decode_responses=True,
    )

r = _client()

class RedisMemory:
    def __init__(self, prefix: str = "mem"):
        self.prefix = prefix

    def _key(self, user: str) -> str:
        return f"{self.prefix}:{user}"

    def add_user_msg(self, user: str, msg: str) -> None:
        self._append(user, {"role": "user", "content": (msg or "").strip()})

    def add_assistant_msg(self, user: str, msg: str) -> None:
        self._append(user, {"role": "assistant", "content": (msg or "").strip()})

    def _append(self, user: str, m: Msg) -> None:
        if not user or not m["content"]:
            return
        key = self._key(user)
        p = r.pipeline()
        p.lpush(key, json.dumps(m, ensure_ascii=False))
        p.ltrim(key, 0, MAX_MSGS - 1)  # mantém só as N mais recentes
        if TTL_SECONDS > 0:
            p.expire(key, TTL_SECONDS)
        p.execute()

    def get_context(self, user: str) -> List[Msg]:
        data = r.lrange(self._key(user), 0, MAX_MSGS - 1)  # mais novas primeiro
        return [json.loads(x) for x in reversed(data)]     # devolve mais antigas primeiro

    # ---- aliases de compatibilidade ----
    def add_msg(self, user: str, msg: str) -> None: self.add_user_msg(user, msg)
    def add(self, user: str, msg: str) -> None: self.add_user_msg(user, msg)
    def get(self, user: str) -> List[Msg]: return self.get_context(user)

# Deduplicação opcional de IDs do WhatsApp
class Dedup:
    def __init__(self, prefix="dedup", ttl=3600):
        self.prefix, self.ttl = prefix, ttl
    def seen(self, msg_id: str) -> bool:
        if not msg_id: return False
        key = f"{self.prefix}:{msg_id}"
        added = r.setnx(key, "1")  # True se a chave não existia
        if added and self.ttl > 0:
            r.expire(key, self.ttl)
        return not added  # True => já visto
