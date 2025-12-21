# topk_client.py
# -*- coding: utf-8 -*-
"""
Busca HÍBRIDA (Semântica + BM25) MULTI-COLEÇÃO para documentos oficiais.

Arquitetura:
- Busca INDEPENDENTE por coleção
- Ranking INTERNO por coleção
- Apenas coleções com resultado relevante retornam
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
# CONFIG
# ==========================================================
TOPK_COLLECTIONS = [
    c.strip() for c in os.getenv(
        "TOPK_COLLECTIONS",
        "Decreto,Diretriz,Lei,Manuais,Memorando,Nota_de_Instrucao,Orientacoes,PAP,POP,Portaria,Resolucao"
    ).split(",") if c.strip()
]

TEXT_FIELD    = os.getenv("TOPK_TEXT_FIELD", "texto")
EMENTA_FIELD  = os.getenv("TOPK_EMENTA_FIELD", "ementa")
TITULO_FIELD  = os.getenv("TOPK_TITULO_FIELD", "titulo")

PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero")
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "data")
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")

SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))

W_TEXT   = float(os.getenv("TOPK_W_TEXT", "0.4"))
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.3"))
W_TITULO = float(os.getenv("TOPK_W_TITULO", "0.3"))

# ==========================================================
# SDK
# ==========================================================
try:
    from topk_sdk import Client
    from topk_sdk.query import select, field, fn, match
    _QUERY_IMPORTED = True
except Exception:
    Client = None
    _QUERY_IMPORTED = False

_client = None
_collections: Dict[str, Any] = {}
_init_error: Optional[str] = None

# ==========================================================
# INIT
# ==========================================================
def _init() -> None:
    global _client, _collections, _init_error

    if Client is None:
        _init_error = "sdk_not_imported"
        return

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")

    if not api_key or not region:
        _init_error = "missing_env"
        return

    _client = Client(api_key=api_key, region=region)
    _collections = {}

    for name in TOPK_COLLECTIONS:
        try:
            _collections[name] = _client.collection(name)
            _dbg(f"Coleção carregada: {name}")
        except Exception as e:
            _dbg(f"Falha ao carregar coleção {name}: {e}")

    _init_error = None if _collections else "no_collections_loaded"

_init()

# ==========================================================
# UTILS
# ==========================================================
def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(c).startswith("Z") else c for c in q).strip()

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _as_dict(rec: Any) -> Dict[str, Any]:
    return rec if isinstance(rec, dict) else getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    return " ".join(
        p for p in [
            item.get(EMENTA_FIELD, "").strip(),
            item.get(TEXT_FIELD, "").strip()
        ] if p
    )

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    return {
        "doc_id": item.get("doc_id") or item.get("id") or "-",
        "artigo_numero": item.get(ART_FIELD) or "-",
        "titulo": item.get(TITULO_FIELD) or "-",
        "trecho": _merge_excerto(item),
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

def _is_id_like(q: str) -> bool:
    ql = _ascii(q.lower())
    return _extract_number(ql) is not None

# ==========================================================
# QUERIES
# ==========================================================
def _keyword_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    try:
        qb = col.query(
            select(
                "doc_id", TITULO_FIELD, ART_FIELD,
                PORTARIA_FIELD, ANO_FIELD,
                TEXT_FIELD, EMENTA_FIELD,
                text_score=fn.bm25_score(),
            ).filter(match(q) | match(_ascii(q)))
             .topk(field("text_score"), k)
        )
        return [_normalize_item(r) for r in qb]
    except Exception:
        return []

def _semantic_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    qn = _norm_spaces(q)
    qb = col.query(
        select(
            "doc_id", TITULO_FIELD, ART_FIELD,
            PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            sim_texto=fn.semantic_similarity(TEXT_FIELD, qn),
            sim_ementa=fn.semantic_similarity(EMENTA_FIELD, qn),
            sim_titulo=fn.semantic_similarity(TITULO_FIELD, qn),
        ).topk(
            W_TEXT * field("sim_texto") +
            W_EMENTA * field("sim_ementa") +
            W_TITULO * field("sim_titulo"),
            k
        )
    )
    return [_normalize_item(r) for r in qb]

def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    qn = _norm_spaces(q)
    try:
        qb = col.query(
            select(
                "doc_id", TITULO_FIELD, ART_FIELD,
                PORTARIA_FIELD, ANO_FIELD,
                TEXT_FIELD, EMENTA_FIELD,
                sim_texto=fn.semantic_similarity(TEXT_FIELD, qn),
                sim_ementa=fn.semantic_similarity(EMENTA_FIELD, qn),
                sim_titulo=fn.semantic_similarity(TITULO_FIELD, qn),
                text_score=fn.bm25_score(),
            ).filter(match(qn) | match(_ascii(qn)))
             .topk(
                SEM_WEIGHT * (
                    W_TEXT * field("sim_texto") +
                    W_EMENTA * field("sim_ementa") +
                    W_TITULO * field("sim_titulo")
                ) + LEX_WEIGHT * field("text_score"),
                k
             )
        )
        return [_normalize_item(r) for r in qb]
    except Exception:
        return _semantic_query(col, q, k)

# ==========================================================
# API PÚBLICA
# ==========================================================
def search_topk_multi(query: str, k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    output: Dict[str, List[Dict[str, Any]]] = {}

    for name, col in _collections.items():
        results = []
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

def buscar_topk_multi(query: str, k: int = 5):
    return search_topk_multi(query, k)

def topk_status():
    return {
        "collections_loaded": list(_collections.keys()),
        "init_error": _init_error,
    }
