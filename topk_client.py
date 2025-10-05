# topk_client.py
# -*- coding: utf-8 -*-
"""
Cliente TopK resiliente, com normalização de saída e diagnóstico.
- Exporta:
    - search_topk(query, k=5)
    - buscar_topk(query, k=5)  (alias)
    - topk_status()            (diagnóstico opcional)
"""

from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

# Tolerante à ausência do SDK durante o build/deploy
_SDK_IMPORTED = True
try:
    from topk_sdk import Client  # type: ignore
except Exception:
    Client = None  # type: ignore
    _SDK_IMPORTED = False

_client = None
_collection = None
_init_error: Optional[str] = None


def _init() -> None:
    """Inicializa cliente/coleção sem quebrar o import do módulo."""
    global _client, _collection, _init_error

    if not _SDK_IMPORTED or Client is None:
        _init_error = "sdk_not_imported"
        print("[WARN TOPK] SDK topk_sdk indisponível; busca desativada.")
        return

    api_key = os.getenv("TOPK_API_KEY")
    region = os.getenv("TOPK_REGION")
    collection_name = os.getenv("TOPK_COLLECTION", "pmpr_portarias")

    if not api_key or not region:
        _init_error = "missing_env"
        print("[WARN TOPK] TOPK_API_KEY/TOPK_REGION ausentes; busca desativada.")
        return

    try:
        _client = Client(api_key=api_key, region=region)  # type: ignore
        _collection = _client.collection(collection_name) if hasattr(_client, "collection") else None
        if not _collection:
            _init_error = "collection_unavailable"
            print(f"[WARN TOPK] Coleção '{collection_name}' não disponível (SDK diferente?).")
            return
        _init_error = None
    except Exception as e:
        _client = None
        _collection = None
        _init_error = f"init_error:{e}"
        print(f"[WARN TOPK] Falha ao inicializar cliente/coleção: {e}")


_init()  # inicializa no import


def _merge_caput_texto(item: Dict[str, Any]) -> str:
    """Combina caput + texto quando existirem (remove duplicidades simples)."""
    parts: List[str] = []
    caput = (item.get("caput") or "").strip()
    texto = (item.get("texto") or "").strip()
    if caput:
        parts.append(caput)
    if texto:
        # evita repetir caput quando o texto começa igual
        if not caput or not texto.startswith(caput):
            parts.append(texto)
    return " ".join(parts).strip()


def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    # fallback muito permissivo
    return getattr(rec, "__dict__", {}) or {}


def _normalize_item(raw: Any) -> Dict[str, Any]:
    """
    Converte um item do TopK para um formato estável consumido pela LLM.
    Saída:
      {doc_id, artigo_numero, titulo, trecho, score, url, numero_portaria, ano, _raw}
    """
    item = _as_dict(raw)

    doc_id = (
        item.get("doc_id")
        or item.get("document_id")
        or item.get("id")
        or "-"
    )
    artigo = (
        item.get("artigo_numero")
        or item.get("artigo")
        or item.get("section")
        or "-"
    )
    titulo = (
        item.get("titulo")
        or item.get("title")
        or item.get("document_title")
        or "-"
    )

    # NOVO: prioriza caput+texto; depois outros aliases usuais
    caput_texto = _merge_caput_texto(item)
    excerto = (
        item.get("trecho")
        or item.get("excerto")
        or caput_texto
        or item.get("ementa")
        or item.get("text")
        or item.get("chunk")
        or item.get("content")
        or ""
    ).strip()

    score = (
        item.get("score")
        or item.get("_score")
        or item.get("similarity")
        or item.get("sem")
        or None
    )
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


def _search_via_builder(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Tenta a busca 'builder' (semantic + bm25)."""
    try:
        if not hasattr(collection, "search"):
            return None
        b = collection.search()  # type: ignore

        # Seleção de campos quando suportado (ignorará silenciosamente se não existir)
        if hasattr(b, "fields"):
            try:
                b.fields(["texto", "caput", "ementa", "titulo", "artigo_numero", "numero_portaria", "ano"])  # type: ignore
            except Exception:
                pass

        if hasattr(b, "semantic"):
            b.semantic(query)  # type: ignore
        if hasattr(b, "bm25"):
            # Peso baixo p/ BM25; ajuste conforme qualidade dos índices
            b.bm25(query, weight=0.3)  # type: ignore

        # Diversas variantes vistas no SDK
        if hasattr(b, "topk"):
            b.topk(k)  # type: ignore
        elif hasattr(b, "k"):
            b.k(k)  # type: ignore

        if hasattr(b, "execute"):
            res = b.execute()  # type: ignore
            if isinstance(res, list):
                return [_normalize_item(r) for r in res]
    except Exception as e:
        print(f"[WARN TOPK] builder search falhou: {e}")
    return None


def _search_via_semantic(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Tenta o atalho semantic_search (quando exposto)."""
    try:
        if hasattr(collection, "semantic_search"):
            res = collection.semantic_search(query=query, top_k=k)  # type: ignore
            if isinstance(res, list):
                return [_normalize_item(r) for r in res]
    except Exception as e:
        print(f"[WARN TOPK] semantic_search falhou: {e}")
    return None


def _search_via_similarity(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Fallback: semantic_similarity(text=...)."""
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
      [{doc_id, artigo_numero, titulo, trecho, score, url, numero_portaria, ano, _raw}, ...]
    Nunca lança exceção para o chamador: em erro, retorna [].
    """
    if not query:
        return []
    if _collection is None:
        # tenta re-inicializar caso o processo tenha carregado ENV depois
        _init()
        if _collection is None:
            return []

    # Ordem prática: builder (semântico+bm25) → semantic_search → semantic_similarity
    for fn in (_search_via_builder, _search_via_semantic, _search_via_similarity):
        res = fn(_collection, query, k)  # type: ignore
        if isinstance(res, list) and res:
            # Filtra resultados que não tenham conteúdo útil
            sane = [r for r in res if (r.get("trecho") or "").strip()]
            if sane:
                return sane
            # se não houver nenhum com conteúdo, ainda retornamos a lista crua
            return res

    return []


def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """Alias retrocompatível para implementações antigas."""
    return search_topk(query, k)


def topk_status() -> Dict[str, Any]:
    """Pequeno relatório de diagnóstico (sem vazar segredos)."""
    return {
        "sdk_imported": _SDK_IMPORTED,
        "api_key_set": bool(os.getenv("TOPK_API_KEY")),
        "region": os.getenv("TOPK_REGION"),
        "collection_name": os.getenv("TOPK_COLLECTION", "pmpr_portarias"),
        "initialized": _collection is not None and _init_error is None,
        "init_error": _init_error,
    }


__all__ = ["search_topk", "buscar_topk", "topk_status"]
