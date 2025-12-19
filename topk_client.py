# topk_client.py — MULTI-COLEÇÕES (corrigido e estável)
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ============================================================
# CONFIGURAÇÃO
# ============================================================
COLLECTION_NAME = os.getenv("TOPK_COLLECTION", "Portaria")

# 🔒 COLEÇÕES FIXAS (SEM ENV)
MULTI_COLLECTIONS = [
    "Portaria",
    "Diretriz",
    "Lei",
    "Decreto",
    "Memorando",
    "Orientacoes",
    "Manuais",
    "Nota_de_Instrucao",
    "POP",
    "PAP",
    "Resolucao",
]

TEXT_FIELD    = "texto"
EMENTA_FIELD  = "ementa"
TITULO_FIELD  = "titulo"

PORTARIA_FIELD = "numero"
ANO_FIELD      = "data"
ART_FIELD      = "artigo_numero"

SEM_WEIGHT = 0.8
LEX_WEIGHT = 0.2
W_TEXT   = 0.4
W_EMENTA = 0.3
W_TITULO = 0.3

# ============================================================
# SDK / DSL
# ============================================================
try:
    from topk_sdk import Client
    from topk_sdk.query import select, field, fn, match
    _SDK_IMPORTED = True
    _QUERY_IMPORTED = True
except Exception:
    _SDK_IMPORTED = False
    _QUERY_IMPORTED = False
    Client = None

_collection = None
_init_error: Optional[str] = None

def _init_collection(name: str):
    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    if not api_key or not region or not _SDK_IMPORTED:
        return None
    try:
        client = Client(api_key=api_key, region=region)
        return client.collection(name)
    except Exception as e:
        _dbg(f"Falha ao abrir coleção '{name}': {e}")
        return None

def _init():
    global _collection, _init_error
    _collection = _init_collection(COLLECTION_NAME)
    _init_error = None if _collection else "collection_unavailable"

_init()

# ============================================================
# UTILS
# ============================================================
def _as_dict(rec: Any) -> Dict[str, Any]:
    return rec if isinstance(rec, dict) else getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    texto  = (item.get(TEXT_FIELD) or "").strip()
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    return f"{ementa}\n\n{texto}" if texto and ementa else texto or ementa

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    return {
        "doc_id": item.get("doc_id") or item.get("id") or "-",
        "artigo_numero": item.get(ART_FIELD) or "-",
        "titulo": item.get(TITULO_FIELD) or "",
        "trecho": _merge_excerto(item),
        "score": item.get("score") or item.get("text_score") or item.get("sim"),
        "numero_portaria": item.get(PORTARIA_FIELD) or "",
        "ano": item.get(ANO_FIELD) or "",
        "_raw": item,
    }

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for i in items:
        k = (i["doc_id"], i["artigo_numero"], i["titulo"])
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out

def _norm(q: str) -> str:
    return unicodedata.normalize("NFKD", q).strip()

def _is_id_like(q: str) -> bool:
    return "portaria" in q.lower() and re.search(r"\d{2,6}", q)

# ============================================================
# QUERIES
# ============================================================
def _keyword_query(col, q: str, k: int):
    sel = select(
        "doc_id", TITULO_FIELD, PORTARIA_FIELD, ANO_FIELD,
        TEXT_FIELD, EMENTA_FIELD,
        text_score=fn.bm25_score()
    )
    return [_normalize_item(r) for r in col.query(sel).filter(match(q)).topk(field("text_score"), k)]

def _hybrid_query(col, q: str, k: int):
    sel = select(
        "doc_id", TITULO_FIELD, PORTARIA_FIELD, ANO_FIELD,
        TEXT_FIELD, EMENTA_FIELD,
        sim_texto=fn.semantic_similarity(TEXT_FIELD, q),
        sim_ementa=fn.semantic_similarity(EMENTA_FIELD, q),
        sim_titulo=fn.semantic_similarity(TITULO_FIELD, q),
        text_score=fn.bm25_score()
    )
    score = (
        SEM_WEIGHT * (
            W_TEXT * field("sim_texto") +
            W_EMENTA * field("sim_ementa") +
            W_TITULO * field("sim_titulo")
        ) + LEX_WEIGHT * field("text_score")
    )
    return [_normalize_item(r) for r in col.query(sel).topk(score, k)]

# ============================================================
# API
# ============================================================
def search_topk(query: str, k: int = 5):
    if not _collection:
        return []
    q = _norm(query)
    res = _keyword_query(_collection, q, k) if _is_id_like(q) else _hybrid_query(_collection, q, k)
    return _dedupe(res)

def buscar_topk(query: str, k: int = 5):
    return search_topk(query, k)

def buscar_topk_multi(query: str, k: int = 5) -> List[Dict[str, Any]]:
    resultados: List[Dict[str, Any]] = []
    q = _norm(query)

    for name in MULTI_COLLECTIONS:
        col = _init_collection(name)
        if not col:
            continue

        try:
            res = _keyword_query(col, q, k) if _is_id_like(q) else _hybrid_query(col, q, k)
            resultados.extend(res)
            _dbg(f"[{name}] {len(res)} itens")
        except Exception as e:
            _dbg(f"Erro na coleção '{name}': {e}")

    return _dedupe(resultados)[:k]

def topk_status():
    return {
        "initialized": bool(_collection),
        "collections": MULTI_COLLECTIONS,
        "sdk": _SDK_IMPORTED,
    }

__all__ = ["buscar_topk", "buscar_topk_multi", "search_topk", "topk_status"]
