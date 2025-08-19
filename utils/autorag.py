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
        if isinstance(j, list):
            return j
        if isinstance(j, dict):
            res = j.get("result")
            if isinstance(res, dict) and isinstance(res.get("data"), list):
                return res["data"]
            for key in ("results", "data", "documents", "items"):
                v = j.get(key)
                if isinstance(v, list):
                    return v
        return []

    def _first_text(self, item):
        content = item.get("content") or []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                return str(c["text"])
        return ""

    def _norm_score(self, s):
        if isinstance(s, (int, float)):
            return float(s)
        if isinstance(s, str):
            return float(s.replace(",", "."))
        return 0.0

    def _extract_date(self, filename: str, text: str) -> str:
        m = re.search(r'(\d{4})[ _\-/.](\d{2})[ _\-/.](\d{2})', filename)
        if m:
            yyyy, mm, dd = m.group(1), m.group(2), m.group(3)
            try:
                dt = datetime(int(yyyy), int(mm), int(dd))
                return dt.strftime("%d/%m/%Y")
            except Exception:
                pass

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
        m = re.search(r'(?:N[ºO]|N\.?|N°)\s*([0-9]{1,6})', text.upper())
        if m:
            return m.group(1)

        m2 = re.search(r'(\d{1,6})(?!\d)', filename)
        if m2:
            return m2.group(1)

        return "s/ nº"

    def _extract_subject(self, text: str) -> str:
        m = re.search(r'(Disciplina[^.\n]{5,200})', text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()

        parts = re.split(r'[\n\.]', text)
        for p in parts:
            p = p.strip()
            if len(p) > 20:
                return p[:180]
        return "assunto não informado"

    def _format_meta_line(self, title: str, number: str, subject: str, date: str) -> str:
        t = (title or "Documento").strip()
        n = (number or "s/ nº").strip()
        s = (subject or "assunto não informado").strip()
        d = (date or "s/ data").strip()
        return f"{t} nº {n} — {s} — {d}"

    # ---------- chamadas públicas ----------

    def ai_search(self, query: str):
        """
        Executa POST {BASE}/ai-search (busca + geração).
        Só manda a query, já que as instruções estão configuradas no painel.
        """
        if not self.api_token:
            raise RuntimeError("CF_AUTORAG_TOKEN (ou AUTORAG_ADMIN_TOKEN) não configurado.")

        url = f"{self.base}/ai-search"
        payload = {"query": query}

        r = requests.post(url, headers=self._hdr, json=payload, timeout=60)
        r.raise_for_status()

        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}
        result = (data.get("result") or {})

        response_text = result.get("response") or ""
        items = result.get("data") or []

        sources = []
        for it in items:
            filename = it.get("filename") or (it.get("attributes") or {}).get("filename") or "Documento"
            text = self._first_text(it)
            score = self._norm_score(it.get("score", 0.0))

            meta_date = self._extract_date(filename, text)
            meta_num = self._extract_number(text, filename)
            meta_subject = self._extract_subject(text)

            sources.append({
                "snippet": text[:1200],
                "source_uri": "",
                "score": score,
                "meta": {
                    "title": filename,
                    "number": meta_num,
                    "subject": meta_subject,
                    "date": meta_date,
                    "formatted": self._format_meta_line(filename, meta_num, meta_subject, meta_date),
                },
            })

        return {
            "response": response_text,
            "sources": sources,
            "raw": data,
        }
