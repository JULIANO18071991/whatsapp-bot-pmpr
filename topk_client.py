# topk_client.py
# -*- coding: utf-8 -*-
"""
Cliente TopK resiliente, com normalização de saída e diagnóstico.
- search_topk(query, k=5)
- buscar_topk(query, k=5)  (alias)
- topk_status()            (diagnóstico)
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

_FIELDS_SEMANTIC = ["texto", "caput", "ementa", "titulo"]

# Tolerante a ausência do SDK
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


_init()


def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}


def _merge_caput_texto(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    caput = (item.get("caput") or "").strip()
    texto = (item.get("texto") or "").strip()
    if caput:
        parts.append(caput)
    if texto and not (caput and texto.startswith(caput)):
        parts.append(texto)
    return " ".join(parts).strip()


def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    doc_id = item.get("doc_id") or item.get("document_id") or item.get("id") or "-"
    artigo = item.get("artigo_numero") or item.get("artigo") or item.get("section") or "-"
    titulo = item.get("titulo") or item.get("title") or item.get("document_title") or "-"
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
    score = item.get("score") or item.get("_score") or item.get("similarity") or item.get("sem") or None
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
    """Builder: semantic+bm25, garantindo .query(query) e k/topk."""
    try:
        if not hasattr(collection, "search"):
            return None
        b = collection.search()  # type: ignore

        # Seleção de campos (se existir)
        if hasattr(b, "fields"):
            try:
                b.fields(["texto", "caput", "ementa", "titulo", "artigo_numero", "numero_portaria", "ano"])  # type: ignore
            except Exception:
                pass

        # Alguns SDKs pedem semantic() sem args; outros aceitam field=...
        if hasattr(b, "semantic"):
            try:
                b.semantic()  # type: ignore
            except Exception:
                try:
                    b.semantic(field="texto")  # type: ignore
                except Exception:
                    pass

        if hasattr(b, "bm25"):
            try:
                b.bm25()  # type: ignore
            except Exception:
                try:
                    b.bm25(weight=0.3)  # type: ignore
                except Exception:
                    pass

        # query e k/topk (variantes)
        if hasattr(b, "query"):
            b.query(query)  # type: ignore
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
    """Atalho semantic_search, tentando variantes de parâmetros."""
    try:
        if not hasattr(collection, "semantic_search"):
            return None

        # 1) Sem campo (se houver default)
        try:
            res = collection.semantic_search(query=query, top_k=k)  # type: ignore
            if isinstance(res, list) and res:
                return [_normalize_item(r) for r in res]
        except Exception:
            pass

        # 2) Com campo explícito (diferentes nomes de parâmetro)
        for fld in _FIELDS_SEMANTIC:
            for kw in ({"field": fld}, {"text_field": fld}, {"search_field": fld}):
                try:
                    res = collection.semantic_search(query=query, top_k=k, **kw)  # type: ignore
                    if isinstance(res, list) and res:
                        return [_normalize_item(r) for r in res]
                except Exception:
                    continue
    except Exception as e:
        print(f"[WARN TOPK] semantic_search falhou: {e}")
    return None


def _search_via_similarity(collection, query: str, k: int) -> Optional[List[Dict[str, Any]]]:
    """Fallback semantic_similarity(text=...), forçando campo quando preciso."""
    try:
        if not hasattr(collection, "semantic_similarity"):
            return None

        # 1) Sem campo
        try:
            res = collection.semantic_similarity(text=query, top_k=k)  # type: ignore
            if isinstance(res, list) and res:
                return [_normalize_item(r) for r in res]
        except Exception:
            pass

        # 2) Com campo explícito
        for fld in _FIELDS_SEMANTIC:
            for kw in ({"field": fld}, {"text_field": fld}, {"search_field": fld}):
                try:
                    res = collection.semantic_similarity(text=query, top_k=k, **kw)  # type: ignore
                    if isinstance(res, list) and res:
                        return [_normalize_item(r) for r in res]
                except Exception:
                    continue
    except Exception as e:
        print(f"[WARN TOPK] semantic_similarity falhou: {e}")
    return None


def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if not query:
        return []
    if _collection is None:
        _init()
        if _collection is None:
            return []

    # Ordem prática: builder → semantic_search → semantic_similarity
    for fn in (_search_via_builder, _search_via_semantic, _search_via_similarity):
        res = fn(_collection, query, k)  # type: ignore
        if isinstance(res, list) and res:
            sane = [r for r in res if (r.get("trecho") or "").strip()]
            return sane or res
    return []


def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    return search_topk(query, k)


def topk_status() -> Dict[str, Any]:
    return {
        "sdk_imported": _SDK_IMPORTED,
        "api_key_set": bool(os.getenv("TOPK_API_KEY")),
        "region": os.getenv("TOPK_REGION"),
        "collection_name": os.getenv("TOPK_COLLECTION", "pmpr_portarias"),
        "initialized": _collection is not None and _init_error is None,
        "init_error": _init_error,
    }


__all__ = ["search_topk", "buscar_topk", "topk_status"]
