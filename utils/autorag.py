import requests


class AutoRAGClient:
    def __init__(self, settings):
        # URL base até o nome do RAG (sem /search no final)
        self.base = settings.AUTORAG_BASE_URL.rstrip("/")

        # Token do Cloudflare (Bearer). Aceita AUTORAG_ADMIN_TOKEN ou CF_AUTORAG_TOKEN.
        self.api_token = (
            getattr(settings, "AUTORAG_ADMIN_TOKEN", None)
            or getattr(settings, "CF_AUTORAG_TOKEN", None)
            or ""
        )

        self._hdr = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_token}" if self.api_token else "",
        }

    def _unwrap(self, j):
        """
        Normaliza formatos de resposta do Cloudflare:
        - { "success": true, "result": [...] }
        - { "results": [...] }
        - { "data": [...] }
        - [ ... ]  # lista direta
        """
        if isinstance(j, list):
            return j
        if isinstance(j, dict):
            for key in ("result", "results", "data", "documents", "items"):
                v = j.get(key)
                if isinstance(v, list):
                    return v
        return []

    def retrieve(self, query: str, top_k: int = 5):
        """
        Executa POST {BASE}/search no AutoRAG (Cloudflare), igual ao seu curl:

        curl {BASE}/search \
          -H 'Content-Type: application/json' \
          -H 'Authorization: Bearer <TOKEN>' \
          -d '{"query":"...","limit":5}'
        """
        if not self.api_token:
            raise RuntimeError("AUTORAG_ADMIN_TOKEN (ou CF_AUTORAG_TOKEN) não configurado.")

        url = f"{self.base}/search"
        payload = {"query": query, "limit": top_k, "top_k": top_k}

        r = requests.post(url, headers=self._hdr, json=payload, timeout=30)
        r.raise_for_status()

        try:
            raw = r.json()
        except ValueError:
            raise RuntimeError(f"AutoRAG retornou não-JSON: {r.text[:200]}")

        items = self._unwrap(raw)

        passages = []
        for p in items[:top_k]:
            meta = p.get("meta") or {}
            passages.append({
                "snippet": (p.get("snippet") or p.get("text") or p.get("chunk") or "")[:1200],
                "source_uri": p.get("source_uri") or p.get("url") or meta.get("url") or "",
                "score": float(p.get("score", 0.0)),
                "meta": {
                    "title": meta.get("title") or p.get("title") or "Documento",
                    "number": meta.get("number") or p.get("number") or "s/ nº",
                    "subject": meta.get("subject") or p.get("subject") or "assunto não informado",
                    "date": meta.get("date") or p.get("date") or "s/ data",
                },
            })
        return passages

    def reindex(self):
        """ POST {BASE}/reindex (se seu RAG tiver esse endpoint habilitado) """
        if not self.api_token:
            raise RuntimeError("AUTORAG_ADMIN_TOKEN (ou CF_AUTORAG_TOKEN) não configurado.")
        url = f"{self.base}/reindex"
        r = requests.post(url, headers=self._hdr, json={}, timeout=60)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return {"raw": r.text[:200]}
