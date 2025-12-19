# topk_client.py
# -*- coding: utf-8 -*-
"""
Busca HÍBRIDA (Semântica + BM25) MULTI-COLEÇÃO para documentos oficiais.

Fluxo:
- Executa a mesma query em TODAS as coleções configuradas
- Keyword-first se ID-like
- Fallback híbrido
- Normaliza, deduplica e retorna tudo para o LLM

Coleções separadas por tipo:
Decreto, Diretriz, Lei, Manuais, Memorando, Nota_de_Instrucao,
Orientacoes, PAP, POP, Portaria, Resolucao
"""

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

# ==========================================================
# DEBUG
# ==========================================================
DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ==========================================================
# CONFIGURAÇÃO
# ==========================================================
TOPK_COLLECTIONS = os.getenv(
    "TOPK_COLLECTIONS",
    "Decreto,Diretriz,Lei,Manuais,Memorando,Nota_de_Instrucao,Orientacoes,PAP,POP,Portaria,Resolucao"
).split(",")

# Campos semânticos
TEXT_FIELD    = os.getenv("TOPK_TEXT_FIELD", "texto")
EMENTA_FIELD  = os.getenv("TOPK_EMENTA_FIELD", "ementa")
TITULO_FIELD  = os.getenv("TOPK_TITULO_FIELD", "titulo")

# Metadados
PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero")
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "data")
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")

# Pesos híbridos
SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))

W_TEXT   = float(os.getenv("TOPK_W_TEXT",   "0.4"))
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.3"))
W_TITULO = float(os.getenv("TOPK_W_TITULO", "0.3"))

# ==========================================================
# SDK / QUERY DSL
# ==========================================================
try:
    from topk_sdk import Client  # type: ignore
    from topk_sdk.query import select, field, fn, match  # type: ignore
    _SDK_IMPORTED = True
    _QUERY_IMPORTED = True
except Exception:
    Client = None  # type: ignore
    _SDK_IMPORTED = False
    _QUERY_IMPORTED = False

_client = None
_collections: Dict[str, Any] = {}
_init_error: Optional[str] = None

# ==========================================================
# INIT MULTI-COLEÇÃO
# ==========================================================
def _init() -> None:
    global _client, _collections, _init_error

    if not _SDK_IMPORTED or Client is None:
        _init_error = "sdk_not_imported"
        return

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")

    if not api_key or not region:
        _init_error = "missing_env"
        return

    try:
        _client = Client(api_key=api_key, region=region)  # type: ignore
        _collections = {}

        for name in TOPK_COLLECTIONS:
            name = name.strip()
            try:
                col = _client.collection(name)
                _collections[name] = col
                _dbg(f"Coleção carregada: {name}")
            except Exception as e:
                _dbg(f"Falha ao carregar coleção {name}: {e}")

        if not _collections:
            _init_error = "no_collections_loaded"
        else:
            _init_error = None

    except Exception as e:
        _init_error = f"init_error:{e}"

_init()

# ==========================================================
# UTILS
# ==========================================================
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    texto  = (item.get(TEXT_FIELD) or "").strip()
    return " ".join(p for p in [ementa, texto] if p).strip()

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    return {
        "doc_id": item.get("doc_id") or item.get("id") or "-",
        "artigo_numero": item.get(ART_FIELD) or "-",
        "titulo": item.get(TITULO_FIELD) or "-",
        "trecho": (
            item.get("trecho")
            or item.get("excerto")
            or _merge_excerto(item)
            or item.get("text")
            or item.get("content")
            or ""
        ).strip(),
        "score": item.get("score") or item.get("text_score") or item.get("sim"),
        "numero_portaria": item.get(PORTARIA_FIELD) or "",
        "ano": item.get(ANO_FIELD) or "",
        "_raw": item,
    }

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for it in items:
        key = (it["doc_id"], it["artigo_numero"], it["titulo"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out

def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(c).startswith("Z") else c for c in q).strip()

def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _is_id_like(q: str) -> bool:
    ql = _ascii(q.lower())
    return "portaria" in ql and _extract_number(ql) is not None

# ==========================================================
# QUERIES
# ==========================================================
def _keyword_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    if not _QUERY_IMPORTED:
        return []

    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD,
            PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            text_score=fn.bm25_score(),
        )
        qb = col.query(sel)
        qb = qb.filter(match(q) | match(_ascii(q)))
        qb = qb.topk(field("text_score"), k)
        return [_normalize_item(r) for r in qb]
    except Exception as e:
        _dbg(f"keyword_query erro: {e}")
        return []

def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    if not _QUERY_IMPORTED:
        return []

    qn = _norm_spaces(q)
    num = _extract_number(qn)

    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD,
            PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            sim_texto=fn.semantic_similarity(TEXT_FIELD, qn),
            sim_ementa=fn.semantic_similarity(EMENTA_FIELD, qn),
            sim_titulo=fn.semantic_similarity(TITULO_FIELD, qn),
            text_score=fn.bm25_score(),
        )

        qb = col.query(sel)

        # ✅ OBRIGATÓRIO para BM25 funcionar no TopK
        qb = qb.filter(
            match(qn) | match(_ascii(qn))
        )

        sem_mix = (
            W_TEXT * field("sim_texto") +
            W_EMENTA * field("sim_ementa") +
            W_TITULO * field("sim_titulo")
        )

        score = SEM_WEIGHT * sem_mix + LEX_WEIGHT * field("text_score")

        if num:
            score = score + 0.05 * field("text_score")

        qb = qb.topk(score, k)

        try:
            qb = qb.rerank()
        except Exception:
            pass

        return [_normalize_item(r) for r in qb]

    except Exception as e:
        _dbg(f"hybrid_query erro: {e}")
        return []

# ==========================================================
# API PÚBLICA — MULTI-COLEÇÃO
# ==========================================================
def search_topk_multi(query: str, k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    if not query:
        return {}

    if not _collections:
        _init()
        if not _collections:
            return {}

    output: Dict[str, List[Dict[str, Any]]] = {}

    for name, col in _collections.items():
        results: List[Dict[str, Any]] = []

        if _is_id_like(query):
            results = _keyword_query(col, query, k)

        if not results:
            results = _hybrid_query(col, query, k)

        sane = [r for r in results if r["trecho"]]

        if sane:
            for r in sane:
                r["fonte_colecao"] = name
            output[name] = _dedupe(sane)[:k]

        _dbg(f"[{name}] {len(sane)} resultados")

    return output

# Compatibilidade
def buscar_topk_multi(query: str, k: int = 5):
    return search_topk_multi(query, k)

# ==========================================================
# STATUS
# ==========================================================
def topk_status() -> Dict[str, Any]:
    return {
        "initialized": bool(_collections),
        "collections_loaded": list(_collections.keys()),
        "init_error": _init_error,
        "weights": {
            "semantic": SEM_WEIGHT,
            "lexical": LEX_WEIGHT,
        }
    }

__all__ = [
    "search_topk_multi",
    "buscar_topk_multi",
    "topk_status",
]
