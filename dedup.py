import redis
import os

REDIS_URL = os.getenv("REDIS_URL")

redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class Dedup:
    def __init__(self, ttl=3600):
        self.ttl = ttl  # 1 hora

    def seen(self, msg_id: str) -> bool:
        if not msg_id:
            return False

        key = f"dedup:{msg_id}"

        # SETNX = só cria se não existir
        was_set = redis_client.setnx(key, "1")

        if was_set:
            redis_client.expire(key, self.ttl)
            return False  # NÃO visto ainda

        return True  # JÁ foi processado
