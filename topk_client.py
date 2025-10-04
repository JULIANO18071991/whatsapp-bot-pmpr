# topk_client.py
# -*- coding: utf-8 -*-
"""
Cliente TopK com inicialização resiliente.
Retorna uma lista de trechos padronizados para a LLM.

Observação: diferentes versões do SDK têm APIs distintas. Este módulo
envolve as chamadas em try/except e normaliza o retorno.
"""

import os
from typing import Any, Dict, List, Optional

try:
    from topk_sdk import Client
except Exception as e:
    Client = None  # type: ignore

TOPK_API_KEY = os.getenv("TOPK_API_KEY")
TOPK_REGION = os.getenv("TOPK_REGION")
TOPK_COLLECTION = os.getenv("TOPK_COLLECTION", "pmpr_portarias")

_client = None
_collection = None

def _init():
    global _client, _collection
    if not (TOPK_API_KEY and TOPK_REGION and Client):
        print("[ERRO TOPK] TOPK_API_KEY/TOPK_REGION ausentes ou SDK indisponível.")
        _client = None
        _collection = None
        return

    try:
        _client = Client(api_key=TOPK_API_KEY, region=TOPK_REGION)
        # Algumas versões usam client.collection("name")
        if hasattr(_client, "collection"):
            _collection = _client.collection(TOPK_COLLECTION)
        else:
            _collection = None
        # Aqui poderíamos validar schema/índices se necessário.
    except Exception as e:
        print(f"[ERRO TOPK] Falha ao inicializar cliente/coleção: {e}")
        _client = None
        _collection = None

_init()


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza um item retornado pelo TopK para o formato esperado pela LLM."""
    # Campos comuns (tente mapear o que existir)
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
        # mantém o original para debug opcional
        "_raw": item,
    }


def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Realiza busca nos documentos.
    Implementa fallback para diferentes APIs do SDK.

    Retorno: List[ {doc_id, artigo_numero, titulo, trecho, score, url, _raw} ]
    """
    if not query or not _collection:
        return []

    # Tente diferentes estilos de chamada conforme a versão do SDK/índices:
    # 1) Semântica pura (com índice semântico)
    try:
        if hasattr(_collection, "semantic_search"):
            # Ex.: resultados = collection.semantic_search(field="titulo|texto", query=query, top_k=k)
            resultados = _collection.semantic_search(query=query, top_k=k)  # type: ignore
            if isinstance(resultados, list):
                return [_normalize_item(r) for r in resultados]
    except Exception as e:
        print(f"[WARN TOPK] semantic_search falhou: {e}")

    # 2) Pipeline-style (builder): .search().semantic(...).topk(k).execute()
    try:
        if hasattr(_collection, "search"):
            # Muitas libs expõem um builder "search()"
            builder = _collection.search()  # type: ignore
            # tentativa: método semantic() + topk()
            if hasattr(builder, "semantic"):
                builder.semantic(query)
            # se houver BM25:
            if hasattr(builder, "bm25"):
                # Peso menor para BM25 (30%) — depende do SDK combinar internamente
                builder.bm25(query, weight=0.3)  # type: ignore
            # define k:
            if hasattr(builder, "topk"):
                builder.topk(k)

            # executar
            if hasattr(builder, "execute"):
                resultados = builder.execute()
                if isinstance(resultados, list):
                    return [_normalize_item(r) for r in resultados]
    except Exception as e:
        print(f"[WARN TOPK] builder search falhou: {e}")

    # 3) match() / semantic_similarity() hipotéticos
    try:
        # Exemplo fictício — ajuste conforme seu SDK real:
        if hasattr(_collection, "semantic_similarity"):
            resultados = _collection.semantic_similarity(text=query, top_k=k)  # type: ignore
            if isinstance(resultados, list):
                return [_normalize_item(r) for r in resultados]
    except Exception as e:
        print(f"[WARN TOPK] semantic_similarity falhou: {e}")

    # Sem resultados / sem suporte
    return []
