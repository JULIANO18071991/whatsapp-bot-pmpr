# topk_client.py
# -*- coding: utf-8 -*-
"""
Cliente TopK resiliente, multi-rotas, com normalização e diagnóstico.
- search_topk(query, k=5)
- buscar_topk(query, k=5)  (alias)
- topk_status()
"""
from __future__ import annotations
import os
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

_FIELDS = ["texto", "caput", "ementa", "titulo"]  # ordem de relevância

# --- SDK ---
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
    """Inicializa cliente/coleção."""
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


# ----------------------- util -----------------------
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
    # normaliza traço/tipografia sem “estragar” acentos (útil p/ BM25)
    q = q.replace("–", "-").replace("—", "-").strip()
    # remove NBSP e espaços esquisitos
    q = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in q)
    return q


# ----------------------- rotas de busca -----------------------
def _try_keyword_search(collection, query: str, k: int) -> Tuple[str, List[Dict[str, Any]]]:
    """Tenta keyword/BM25 por campo via API direta se existir."""
    results: List[Dict[str, Any]] = []
    if not hasattr(collection, "keyword_search"):
        return ("keyword(noop)", results)
    for fld in _FIELDS:
        try:
            res = collection.keyword_search(query=query, top_k=k, field=fld)  # type: ignore
            if isinstance(res, list) and res:
                results.extend(_normalize_item(r) for r in res)
        except Exception:
            continue
    return ("keyword_search", results)

def _try_semantic_similarity(collection, query: str, k: int) -> Tuple[str, List[Dict[str, Any]]]:
    """Tenta semantic_similarity por campo explícito (compatível com várias versões)."""
    results: List[Dict[str, Any]] = []
    if not hasattr(collection, "semantic_similarity"):
        return ("semantic(noop)", results)

    # 1) sem campo – se a coleção possuir um default
    try:
        res = collection.semantic_similarity(text=query, top_k=k)  # type: ignore
        if isinstance(res, list) and res:
            results.extend(_normalize_item(r) for r in res)
    except Exception:
        pass

    # 2) com campo explícito (várias keys aceitas)
    for fld in _FIELDS:
        for kw in ({"field": fld}, {"text_field": fld}, {"search_field": fld}):
            try:
                res = collection.semantic_similarity(text=query, top_k=k, **kw)  # type: ignore
                if isinstance(res, list) and res:
                    results.extend(_normalize_item(r) for r in res)
            except Exception:
                continue
    return ("semantic_similarity", results)

def _try_builder(collection, query: str, k: int) -> Tuple[str, List[Dict[str, Any]]]:
    """Tenta builder (semantic + bm25), garantindo query/fields/k."""
    results: List[Dict[str, Any]] = []
    try:
        if not hasattr(collection, "search"):
            return ("builder(noop)", results)
        b = collection.search()  # type: ignore

        # fields
        if hasattr(b, "fields"):
            try:
                b.fields(["texto", "caput", "ementa", "titulo", "artigo_numero", "numero_portaria", "ano"])  # type: ignore
            except Exception:
                pass

        # habilita os módulos (semântico/BM25)
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

        # query e k
        if hasattr(b, "query"):
            b.query(query)  # type: ignore
        if hasattr(b, "topk"):
            b.topk(k)  # type: ignore
        elif hasattr(b, "k"):
            b.k(k)  # type: ignore

        if hasattr(b, "execute"):
            res = b.execute()  # type: ignore
            if isinstance(res, list) and res:
                results.extend(_normalize_item(r) for r in res)
        return ("builder", results)
    except Exception as e:
        print(f"[WARN TOPK] builder search falhou: {e}")
        return ("builder(error)", results)


# ----------------------- API pública -----------------------
def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Busca documentos no TopK agregando múltiplas rotas.
    Retorna itens normalizados: [{doc_id, artigo_numero, titulo, trecho, score, url, ...}]
    """
    if not query:
        return []
    if _collection is None:
        _init()
        if _collection is None:
            return []

    q = _norm_query(query)

    # Ordem: semantic → keyword → builder (ou troque a ordem se preferir BM25 primeiro)
    label1, r1 = _try_semantic_similarity(_collection, q, k)     # type: ignore
    label2, r2 = _try_keyword_search(_collection, q, k)          # type: ignore
    label3, r3 = _try_builder(_collection, q, k)                 # type: ignore

    merged = _dedupe([*r1, *r2, *r3])

    # DEBUG leve (não vaza segredos)
    print(f"[TOPK DEBUG] {label1}={len(r1)} {label2}={len(r2)} {label3}={len(r3)} merged={len(merged)}")

    # filtra os que têm conteúdo
    sane = [r for r in merged if (r.get("trecho") or "").strip()]
    return (sane or merged)[:k]


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
