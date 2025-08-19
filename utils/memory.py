import os
import sqlite3
import numpy as np
from typing import List, Tuple, Callable
from datetime import datetime


class MemoryStore:
    """
    Memória vetorial simples por usuário usando SQLite.
    Tabela:
      id INTEGER PK, user_id TEXT, role TEXT ('user'|'assistant'),
      text TEXT, ts INTEGER (epoch ms), dim INTEGER, embedding BLOB
    """

    def __init__(self, db_path: str, embed_fn: Callable[[str], List[float]]):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.embed_fn = embed_fn
        self._ensure_schema()

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def _ensure_schema(self):
        with self._conn() as cx:
            cx.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    dim INTEGER NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
            cx.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_user_ts ON memory(user_id, ts DESC)"
            )

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.utcnow().timestamp() * 1000)

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        if a.size == 0 or b.size == 0:
            return 0.0
        denom = (np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)

    def save(self, user_id: str, role: str, text: str):
        emb = np.array(self.embed_fn(text), dtype=np.float32)
        with self._conn() as cx:
            cx.execute(
                "INSERT INTO memory (user_id, role, text, ts, dim, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, role, text, self._now_ms(), emb.size, emb.tobytes()),
            )

    def search(self, user_id: str, query: str, top_k: int = 4, lookback: int = 200) -> List[str]:
        """
        Retorna os 'top_k' textos mais similares do histórico recente do usuário.
        """
        q_emb = np.array(self.embed_fn(query), dtype=np.float32)

        with self._conn() as cx:
            rows = cx.execute(
                "SELECT text, dim, embedding FROM memory "
                "WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
                (user_id, lookback),
            ).fetchall()

        scored: List[Tuple[float, str]] = []
        for text, dim, blob in rows:
            emb = np.frombuffer(blob, dtype=np.float32)
            if emb.size != dim:
                continue
            score = self._cosine(q_emb, emb)
            scored.append((score, text))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:top_k]]
