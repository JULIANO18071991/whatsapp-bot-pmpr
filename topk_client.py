# topk_client.py
# -*- coding: utf-8 -*-
"""
Cliente TopK resiliente, com normalização de saída.
- Exporta search_topk(query, k=5)
- Mantém compatibilidade com implementações antigas via alias buscar_topk = search_topk
"""

import os
from typing import Any, Dict, List, Optional

# Tolerante à ausência do SDK em build inicial
try:
    from topk_sdk import Client  # type: ignore
except Exception:
    Client = None  # type: ignore

TOPK_API_KEY = os.getenv("TOPK_API_KEY")
TOPK_REGION = os.getenv("TOPK_REGION")
TOPK_COLLECTION = os.getenv("TOPK_COLLECTION", "pmpr_portarias")

_client = None
_collection = None


def _init() -> None:
    """Inicializa o cliente/coleção sem quebrar o import do módulo."""
    global _client, _collection
    if not (TOPK_API_KEY and TOPK_REGION and Client):
        print("[WARN TOPK] TOPK_API_KEY/TOPK_REGION ausentes ou SDK indisponível; busca desativada.")
        _client = None
        _collection = None
        return

    try:
        _client = Client(api_key=TOPK_API_KEY, region=TOPK_REGION)  # type: ignore
        # Alguns SDKs expõem .collection("name")
        if hasattr(_client, "collection"):
            _collection = _client.collection(TOPK_COLLECTION)  # type: ignore
        else:
            _collection = None
        if not _collection:
            print(f"[WARN TOPK] Coleção '{TOPK_COLLECTION}' não disponível (SDK diferente?).")
    except Exception as e:
        print(f"[WARN TOPK] Falha ao inicializar cliente/coleção: {e}")
        _client = None
        _collection = None


_init()


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Converte um item do TopK para um formato estável consumido pela LLM."""
    doc_id = item.get("doc_id") or item.get("document_id") or item.get("id") or "-"
    artigo = item.get("artigo_numero") or item.get("artigo") or item.get("section") or "-"
    titulo = item.get("titulo") or item.get("title") or item.get("document_title") or "-"
    excerto = (
        item.get("trecho")
        or item.get("excerto")
        or item.get("text")
        or item.get("chunk")
        or item.get("content")
        or ""
    )
    score = item.get("score") or item.get("_score") or item.get("similarity") or None
    url = item.get("url") or item.get("source_url") or None

    return {
        "doc_id": doc_id,
        "artigo_numero": artigo,
        "titulo": titulo,
        "trecho": excerto,
        "score": score,
        "url": url,
        "_raw": item,  # útil para debug
    }


def _search_via_semantic(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Tentativa 1: API semantic_search(query, top_k)."""
    try:
        if hasattr(collection, "semantic_search"):
            res = collection.semantic_search(query=query, top_k=k)  # type: ignore
            if isinstance(res, list):
                return [_normalize_item(r) for r in res]
    except Exception as e:
        print(f"[WARN TOPK] semantic_search falhou: {e}")
    return None


def _search_via_builder(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Tentativa 2: builder .search().semantic().bm25().topk().execute()."""
    try:
        if hasattr(collection, "search"):
            builder = collection.search()  # type: ignore
            if hasattr(builder, "semantic"):
                builder.semantic(query)  # type: ignore
            if hasattr(builder, "bm25"):
                # Peso menor para BM25, se a combinação for suportada pelo SDK
                builder.bm25(query, weight=0.3)  # type: ignore
            if hasattr(builder, "topk"):
                builder.topk(k)  # type: ignore
            if hasattr(builder, "execute"):
                res = builder.execute()  # type: ignore
                if isinstance(res, list):
                    return [_normalize_item(r) for r in res]
    except Exception as e:
        print(f"[WARN TOPK] builder search falhou: {e}")
    return None


def _search_via_similarity(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Tentativa 3: semantic_similarity(text, top_k)."""
    try:
        if hasattr(collection, "semantic_similarity"):
            res = collection.semantic_similarity(text=query, top_k=k)  # type: ignore
            if isinstance(res, list):
                return [_normalize_item(r) for r in res]
    except Exception as e:
        print(f"[WARN TOPK] semantic_similarity falhou: {e}")
    return None


def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Busca documentos no TopK. Retorna lista normalizada:
    [{doc_id, artigo_numero, titulo, trecho, score, url, _raw}, ...]
    - Nunca lança exceção para o chamador: em erro, retorna [].
    """
    if not query:
        return []
    if not _collection:
        # Sem coleção disponível: retorna vazio (fallback)
        return []

    # Tentativas em ordem
    for fn in (_search_via_semantic, _search_via_builder, _search_via_similarity):
        res = fn(_collection, query, k)
        if isinstance(res, list):
            return res

    return []


# Compatibilidade com código antigo
def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """Alias retrocompatível para implementações antigas."""
    return search_topk(query, k)


# Opcional: controlar o que é exportado por 'from topk_client import *'
__all__ = ["search_topk", "buscar_topk"]
