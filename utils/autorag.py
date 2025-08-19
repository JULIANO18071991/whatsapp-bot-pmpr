import requests

class AutoRAGClient:
    def __init__(self, settings):
        self.base = settings.AUTORAG_BASE_URL.rstrip("/")
        self.admin_token = settings.AUTORAG_ADMIN_TOKEN
        self._hdr = {"Content-Type": "application/json"}
        if self.admin_token:
            self._hdr["X-Admin-Token"] = self.admin_token

    def retrieve(self, query: str, top_k: int = 5):
        url = f"{self.base}/retrieval"
        params = {"query": query, "top_k": top_k}
        r = requests.get(url, headers=self._hdr, params=params, timeout=30)
        r.raise_for_status()
        data = r.json() or []
        passages = []
        for p in data[:top_k]:
            passages.append(
                {
                    "snippet": p.get("snippet", "")[:1200],
                    "source_uri": p.get("source_uri", ""),
                    "score": float(p.get("score", 0.0)),
                    "meta": p.get("meta", {}),
                }
            )
        return passages

    def reindex(self):
        url = f"{self.base}/reindex"
        r = requests.post(url, headers=self._hdr, json={}, timeout=60)
        r.raise_for_status()
        return r.json()
