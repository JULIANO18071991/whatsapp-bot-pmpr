import re
import requests
from datetime import datetime


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

    # ---------- helpers de parsing ----------

    def _unwrap(self, j):
        """
        Normaliza formatos de resposta do Cloudflare:
        - { "success": true, "result": { data: [...] } }
        - { "results": [...] }
        - { "data": [...] }
        - [ ... ]  # lista direta
        """
        if isinstance(j, list):
            return j
        if isinstance(j, dict):
            # Caso { success, result: { data: [...] } }
            res = j.get("result")
            if isinstance(res, dict) and isinstance(res.get("data"), list):
                return res["data"]
            # Outros formatos comuns
            for key in ("results", "data", "documents", "items"):
                v = j.get(key)
                if isinstance(v, list):
                    return v
        return []

    def _first_text(self, item):
        """
        Extrai o primeiro texto do array content[].text
        """
        content = item.get("content") or []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                return str(c["text"])
        return ""

    def _norm_score(self, s):
        """
        Converte '0,7028296' -> 0.7028296 (float).
        """
        if isinstance(s, (int, float)):
            return float(s)
        if isinstance(s, str):
            return float(s.replace(",", "."))
        return 0.0

    def _extract_date(self, filename: str, text: str) -> str:
        """
        Tenta extrair data em DD/MM/AAAA.
        - do filename: 'YYYY MM DD - ...' -> DD/MM/YYYY
        - do texto: 'DE 08 DE NOVEMBRO DE 2011' (PT) -> 08/11/2011
        """
        # 1) Padrão no filename: 'YYYY MM DD'
        m = re.search(r'(\d{4})[ _\-/.](\d{2})[ _\-/.](\d{2})', filename)
        if m:
            yyyy, mm, dd = m.group(1), m.group(2), m.group(3)
            try:
                dt = datetime(int(yyyy), int(mm), int(dd))
                return dt.strftime("%d/%m/%Y")
            except Exception:
                pass

        # 2) Padrão textual: 'DE 08 DE NOVEMBRO DE 2011'
        meses = {
            "JANEIRO": "01", "FEVEREIRO": "02", "MARÇO": "03", "MARCO": "03", "ABRIL": "04",
            "MAIO": "05", "JUNHO": "06", "JULHO": "07", "AGOSTO": "08", "SETEMBRO": "09",
            "OUTUBRO": "10", "NOVEMBRO": "11", "DEZEMBRO": "12",
        }
        m2 = re.search(r'(\d{1,2})\s*DE\s*([A-ZÇÃÉÊÓÔÚÍ]+)\s*DE\s*(\d{4})', text.upper())
        if m2:
            dd, mon, yyyy = m2.group(1), m2.group(2), m2.group(3)
            mm = meses.get(mon, None)
            if mm:
                dd = dd.zfill(2)
                return f"{dd}/{mm}/{yyyy}"

        return "s/ data"

    def _extract_number(self, text: str, filename: str) -> str:
        """
        Tenta extrair 'nº XXX' de 'Portaria ... 778' etc.
        """
        # Procura no texto (ex.: PORTARIA DO COMANDO-GERAL Nº 778)
        m = re.search(r'(?:N[ºO]|N\.?|N°)\s*([0-9]{1,6})', text.upper())
        if m:
            return m.group(1)

        # Procura padrão no filename (ex.: 'Portaria CG 778')
        m2 = re.search(r'(\d{1,6})(?!\d)', filename)
        if m2:
            return m2.group(1)

        return "s/ nº"

    def _extract_subject(self, text: str) -> str:
        """
        Tenta pegar a linha com 'Disciplina ...' ou a primeira frase significativa.
        """
        # Linha com 'Disciplina ...'
        m = re.search(r'(Disciplina[^.\n]{5,200})', text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()

        # Primeira frase decente
        parts = re.split(r'[\n\.]', text)
        for p in parts:
            p = p.strip()
            if len(p) > 20:
                return p[:180]
        return "assunto não informado"

    # ---------- chamadas públicas ----------

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
        payload = {"query": query, "limit": top_k}

        r = requests.post(url, headers=self._hdr, json=payload, timeout=30)
        r.raise_for_status()

        try:
            raw = r.json()
        except ValueError:
            raise RuntimeError(f"AutoRAG retornou não-JSON: {r.text[:200]}")

        items = self._unwrap(raw)

        passages = []
        for it in items[:top_k]:
            filename = it.get("filename") or (it.get("attributes") or {}).get("filename") or "Documento"
            text = self._first_text(it)
            score = self._norm_score(it.get("score", 0.0))

            meta_date = self._extract_date(filename, text)
            meta_num = self._extract_number(text, filename)
            meta_subject = self._extract_subject(text)

            passages.append({
                "snippet": text[:1200],
                "source_uri": "",  # Cloudflare não fornece URL pública; deixe vazio
                "score": score,
                "meta": {
                    "title": filename,
                    "number": meta_num,
                    "subject": meta_subject,
                    "date": meta_date,
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
