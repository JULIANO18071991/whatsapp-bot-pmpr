@@
-import requests
+import requests
 
 
 class AutoRAGClient:
     def __init__(self, settings):
         self.base = settings.AUTORAG_BASE_URL.rstrip("/")
-        self.admin_token = settings.AUTORAG_ADMIN_TOKEN
-        self._hdr = {"Content-Type": "application/json"}
-        if self.admin_token:
-            self._hdr["X-Admin-Token"] = self.admin_token
+        # No Cloudflare, as chamadas usam Authorization: Bearer <API_TOKEN>
+        self.api_token = settings.AUTORAG_ADMIN_TOKEN  # use seu CF token aqui
+        self._hdr = {
+            "Content-Type": "application/json",
+            "Authorization": f"Bearer {self.api_token}" if self.api_token else "",
+        }
 
-    def retrieve(self, query: str, top_k: int = 5):
-        """
-        Chama o AutoRAG para recuperar trechos relevantes.
-        Espera resposta: [{"snippet": "...","source_uri":"...","score":0.87, "meta":{...}}]
-        """
-        url = f"{self.base}/retrieval"
-        params = {"query": query, "top_k": top_k}
-        r = requests.get(url, headers=self._hdr, params=params, timeout=30)
-        r.raise_for_status()
-        data = r.json() or []
-        # Sanitização mínima
-        passages = []
-        for p in data[:top_k]:
-            passages.append(
-                {
-                    "snippet": p.get("snippet", "")[:1200],
-                    "source_uri": p.get("source_uri", ""),
-                    "score": float(p.get("score", 0.0)),
-                    "meta": p.get("meta", {}),
-                }
-            )
-        return passages
+    def retrieve(self, query: str, top_k: int = 5):
+        """
+        Cloudflare AutoRAG: POST {BASE}/search
+        Body esperado:
+          { "query": "...", "limit": <int> }  # alguns exemplos usam "top_k"; usamos ambos
+        Resposta esperada: lista de objetos com snippet/source/meta.
+        """
+        url = f"{self.base}/search"
+        payload = {"query": query, "limit": top_k, "top_k": top_k}
+        r = requests.post(url, headers=self._hdr, json=payload, timeout=30)
+        r.raise_for_status()
+        data = r.json() or []
+        # Normaliza campos
+        passages = []
+        for p in data[:top_k]:
+            passages.append({
+                "snippet": (p.get("snippet") or p.get("text") or "")[:1200],
+                "source_uri": p.get("source_uri") or p.get("url") or "",
+                "score": float(p.get("score", 0.0)),
+                "meta": p.get("meta") or {
+                    "title": p.get("title"),
+                    "number": p.get("number"),
+                    "subject": p.get("subject"),
+                    "date": p.get("date"),
+                },
+            })
+        return passages
 
     def reindex(self):
-        """
-        Dispara reindex no AutoRAG (modo manual ou refresh imediato).
-        Espera resposta: {"job":"<id>", "status":"queued|running|done"}
-        """
-        url = f"{self.base}/reindex"
-        r = requests.post(url, headers=self._hdr, json={}, timeout=60)
-        r.raise_for_status()
-        return r.json()
+        """ Cloudflare AutoRAG: POST {BASE}/reindex """
+        url = f"{self.base}/reindex"
+        r = requests.post(url, headers=self._hdr, json={}, timeout=60)
+        r.raise_for_status()
+        return r.json()
