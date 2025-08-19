import requests

class AutoRAGClient:
    def __init__(self, settings):
        # URL base até o nome do RAG (sem /search no final)
        self.base = settings.AUTORAG_BASE_URL.rstrip("/")
        self.api_token = settings.AUTORAG_ADMIN_TOKEN  # Token do Cloudflare
        self._hdr = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}" if self.api_token else "",
        }

    def retrieve(self, query: str, top_k: int = 5):
        """
        Executa POST /search no AutoRAG (Cloudflare), igual ao curl.
        """
        url = f"{self.base}/search"
        payload = {"query": query, "limit": top_k}
        r = requests.post(url, headers=self._hdr, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json() or []

        passages = []
        for p in data[:top_k]:
            passages.append({
                "snippet": (p.get("snippet") or p.get("text") or "")[:1200],
                "source_uri": p.get("source_uri") or p.get("url") or "",
                "score": float(p.get("score", 0.0)),
                "meta": p.get("meta") or {
                    "title": p.get("title"),
                    "number": p.get("number"),
                    "subject": p.get("subject"),
                    "date": p.get("date"),
                },
            })
        return passages

    def reindex(self):
        """
        Se quiser reindexar no Cloudflare (se habilitado).
        """
        url = f"{self.base}/reindex"
        r = requests.post(url, headers=self._hdr, json={}, timeout=60)
        r.raise_for_status()
        return r.json()
