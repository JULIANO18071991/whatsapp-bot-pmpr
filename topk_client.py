# topk_client.py
# -*- coding: utf-8 -*-
"""
Cliente TopK usando a DSL oficial (topk_sdk.query), compatível com várias versões.
- search_topk(query, k=5)
- buscar_topk(query, k=5)  (alias)
- topk_status()            (diagnóstico)
"""

from __future__ import annotations
import os
import unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# --- SDK base ---
_SDK_IMPORTED = True
try:
    from topk_sdk import Client  # type: ignore
except Exception:
    Client = None  # type: ignore
    _SDK_IMPORTED = False

# --- DSL de consulta (igual ao seu quick_test_topk.py) ---
_QUERY_IMPORTED = True
try:
    from topk_sdk.query import select, field, fn, match  # type: ignore
except Exception:
    _QUERY_IMPORTED = False

_client = None
_collection = None
_init_error: Optional[str] = None

_COLLECTION_NAME = os.getenv("TOPK_COLLECTION", "pmpr_portarias")
_TEXT_FIELD     = os.getenv("TOPK_TEXT_FIELD", "texto")  # campo com semantic_index/keyword_index

def _init() -> None:
    """Inicializa client e coleção."""
    global _client, _collection, _init_error
    if not _SDK_IMPORTED or Client is None:
        _init_error = "sdk_not_imported"
        print("[WARN TOPK] SDK topk_sdk indisponível; busca desativada.")
        return

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    if not api_key or not region:
        _init_error = "missing_env"
        print("[WARN TOPK] TOPK_API_KEY/TOPK_REGION ausentes; busca desativada.")
        return

    try:
        _client = Client(api_key=api_key, region=region)  # type: ignore
        # preferimos o caminho padrão; se falhar, tentamos alternativas
        coll = None
        if hasattr(_client, "collection"):
            try:
                coll = _client.collection(_COLLECTION_NAME)  # type: ignore
            except Exception as e:
                _dbg(f"collection() falhou: {e}")
        if coll is None and hasattr(_client, "collections"):
            try:
                coll = _client.collections[_COLLECTION_NAME]  # type: ignore
            except Exception as e:
                _dbg(f"collections[...] falhou: {e}")
        if coll is None and hasattr(_client, "get_collection"):
            try:
                coll = _client.get_collection(_COLLECTION_NAME)  # type: ignore
            except Exception as e:
                _dbg(f"get_collection() falhou: {e}")

        _collection = coll
        if _collection is None:
            _init_error = "collection_unavailable"
            print(f"[WARN TOPK] Coleção '{_COLLECTION_NAME}' não disponível.")
            return

        _init_error = None
        if DEBUG:
            try:
                _dbg(f"type(collection)={type(_collection)}")
            except Exception:
                pass
    except Exception as e:
        _client = None
        _collection = None
        _init_error = f"init_error:{e}"
        print(f"[WARN TOPK] Falha ao inicializar cliente/coleção: {e}")

_init()

# ---------------- util ----------------
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _merge_caput_texto(item: Dict[str, Any]) -> str:
    caput = (item.get("caput") or "").strip()
    texto = (item.get("texto") or "").strip()
    if caput and texto and texto.startswith(caput):
        return texto
    return " ".join([p for p in [caput, texto] if p]).strip()

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    doc_id = item.get("doc_id") or item.get("document_id") or item.get("id") or item.get("_id") or "-"
    artigo = item.get("artigo_numero") or item.get("artigo") or item.get("section") or "-"
    titulo = item.get("titulo") or item.get("title") or item.get("document_title") or "-"
    excerto = (
        item.get("trecho")
        or item.get("excerto")
        or _merge_caput_texto(item)
        or item.get("ementa")
        or item.get("text")
        or item.get("chunk")
        or item.get("content")
        or ""
    ).strip()
    score = item.get("score") or item.get("text_score") or item.get("sim") or item.get("similarity") or item.get("_score") or None
    url = item.get("url") or item.get("source_url") or None
    numero = item.get("numero_portaria") or item.get("num") or ""
    ano = item.get("ano") or ""

    return {
        "doc_id": doc_id,
        "artigo_numero": artigo,
        "titulo": titulo,
        "trecho": excerto,
        "score": score,
        "url": url,
        "numero_portaria": numero,
        "ano": ano,
        "_raw": item,
    }

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        key = (it.get("doc_id"), it.get("artigo_numero"), (it.get("titulo") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

def _norm_query(q: str) -> str:
    q = q.replace("–", "-").replace("—", "-").strip()
    q = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in q)
    return q

# --------------- buscas via DSL (iguais ao seu teste) ---------------
def _bm25(col, q: str, k: int) -> List[Dict[str, Any]]:
    try:
        rows = col.query(
            select(
                "_id", "titulo", "parent_level", "artigo_numero", "ano", "numero_portaria",
                "caput", "texto", "ementa",
                text_score=fn.bm25_score(),
            )
            .filter(match(q))
            .topk(field("text_score"), k)
        )
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except Exception as e:
        _dbg(f"bm25 falhou: {e}")
        return []

def _semantic(col, q: str, k: int) -> List[Dict[str, Any]]:
    try:
        rows = col.query(
            select(
                "_id", "titulo", "parent_level", "artigo_numero", "ano", "numero_portaria",
                "caput", "texto", "ementa",
                sim=fn.semantic_similarity(_TEXT_FIELD, q),
            )
            .topk(field("sim"), k)
        )
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except Exception as e:
        _dbg(f"semantic falhou: {e}")
        return []

def _hybrid_sem(col, q: str, k: int) -> List[Dict[str, Any]]:
    try:
        rows = col.query(
            select(
                "_id", "titulo", "parent_level", "artigo_numero", "ano", "numero_portaria",
                "caput", "texto", "ementa",
                text_score=fn.bm25_score(),
                sim=fn.semantic_similarity(_TEXT_FIELD, q),
                score=field("text_score") * 0.5 + field("sim") * 0.5,
            )
            .filter(match(q))
            .topk(field("score"), k)
        )
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except Exception as e:
        _dbg(f"hibrido(bm25+sem) falhou: {e}")
        return []

# --------------- API pública ---------------
def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Consulta a coleção via DSL (mesma abordagem do quick_test_topk.py).
    Retorna [{doc_id, artigo_numero, titulo, trecho, score, url, ...}]
    """
    if not query:
        return []
    if _collection is None:
        _init()
        if _collection is None:
            return []

    if not _QUERY_IMPORTED:
        # SDK antigo sem módulo query: melhor não “fingir” que buscamos.
        _dbg("topk_sdk.query indisponível — instale topk-sdk>=0.5.0")
        return []

    q = _norm_query(query)

    r_sem = _semantic(_collection, q, k)     # semântico puro
    r_kw  = _bm25(_collection, q, k)         # keyword/BM25
    r_hyb = _hybrid_sem(_collection, q, k)   # híbrido (BM25 + sem)

    merged = _dedupe([*r_sem, *r_kw, *r_hyb])
    sane = [r for r in merged if (r.get("trecho") or "").strip()]

    if DEBUG:
        _dbg(f"semantic={len(r_sem)} bm25={len(r_kw)} hybrid={len(r_hyb)} merged={len(merged)} sane={len(sane)}")

    return (sane or merged)[:k]

def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    return search_topk(query, k)

def topk_status() -> Dict[str, Any]:
    return {
        "sdk_imported": _SDK_IMPORTED,
        "query_dsl_imported": _QUERY_IMPORTED,
        "api_key_set": bool(os.getenv("TOPK_API_KEY")),
        "region": os.getenv("TOPK_REGION"),
        "collection_name": _COLLECTION_NAME,
        "text_field": _TEXT_FIELD,
        "initialized": _collection is not None and _init_error is None,
        "init_error": _init_error,
    }

__all__ = ["search_topk", "buscar_topk", "topk_status"]
