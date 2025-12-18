# topk_client.py — versão expandida para MULTI-COLEÇÕES
# -*- coding: utf-8 -*-

"""
Agora o CLIENTE TOPK suporta dois modos:

1) buscar_topk(query)
   → busca apenas na coleção única configurada (retrocompatível)

2) buscar_topk_multi(query)
   → busca em TODAS as coleções definidas no .env:
        TOPK_COLLECTIONS="Portaria,Diretriz,Lei,Decreto,..."

Cada coleção é consultada usando seu pipeline híbrido/semântico atual.
"""

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ============================================================
# 🔧 CONFIGURAÇÃO
# ============================================================
COLLECTION_NAME = os.getenv("TOPK_COLLECTION", "pmpr_portarias")
MULTI_COLLECTIONS = [
    c.strip() for c in os.getenv("TOPK_COLLECTIONS", "").split(",") if c.strip()
]

TEXT_FIELD    = os.getenv("TOPK_TEXT_FIELD", "texto")
EMENTA_FIELD  = os.getenv("TOPK_EMENTA_FIELD", "ementa")
TITULO_FIELD  = os.getenv("TOPK_TITULO_FIELD", "titulo")

PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero")
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "data")
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")

SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))
W_TEXT   = float(os.getenv("TOPK_W_TEXT",   "0.4"))
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.3"))
W_TITULO = float(os.getenv("TOPK_W_TITULO", "0.3"))

KEYWORD_FIELDS = [
    "doc_id", "numero", "data", "assuntos", "tipo_documento"
]

# ============================================================
# 🔧 SDK / DSL
# ============================================================
_SDK_IMPORTED = True
try:
    from topk_sdk import Client  # type: ignore
except Exception:
    _SDK_IMPORTED = False
    Client = None  # type: ignore

_QUERY_IMPORTED = True
try:
    from topk_sdk.query import select, field, fn, match  # type: ignore
except Exception:
    _QUERY_IMPORTED = False

_client = None
_collection = None
_init_error: Optional[str] = None


def _init_collection(name: str):
    """Inicializa uma coleção específica."""
    if not _SDK_IMPORTED or Client is None:
        return None

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    if not api_key or not region:
        return None

    try:
        client = Client(api_key=api_key, region=region)

        col = None
        if hasattr(client, "collection"):
            try: col = client.collection(name)
            except: pass

        if col is None and hasattr(client, "collections"):
            try: col = client.collections[name]
            except: pass

        if col is None and hasattr(client, "get_collection"):
            try: col = client.get_collection(name)
            except: pass

        return col
    except:
        return None


def _init() -> None:
    """Inicializa a coleção principal (retrocompatível)."""
    global _client, _collection, _init_error
    col = _init_collection(COLLECTION_NAME)
    if col is None:
        _init_error = f"collection_unavailable:{COLLECTION_NAME}"
        return
    _collection = col
    _init_error = None


_init()

# ============================================================
# 🔧 UTILS
# ============================================================
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    texto  = (item.get(TEXT_FIELD) or "").strip()
    parts = [p for p in [ementa, texto] if p]
    return " ".join(parts).strip()

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    doc_id = item.get("doc_id") or item.get("id") or "-"
    artigo = item.get(ART_FIELD) or "-"
    titulo = item.get(TITULO_FIELD) or ""
    excerto = (
        item.get("trecho")
        or item.get("excerto")
        or _merge_excerto(item)
        or item.get("text")
        or ""
    ).strip()
    score = item.get("score") or None
    url = item.get("url")
    numero = item.get(PORTARIA_FIELD) or ""
    ano = item.get(ANO_FIELD) or ""

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

def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(ch).startswith("Z") else ch for ch in q).strip()

def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _is_id_like(q: str) -> bool:
    ql = _ascii(q.lower())
    return ("portaria" in ql and _extract_number(ql) is not None)


# ============================================================
# 🔍 CONSULTAS
# ============================================================
def _keyword_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    if not _QUERY_IMPORTED: return []
    q_norm = _norm_spaces(q)
    q_ascii = _ascii(q_norm)
    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            text_score=fn.bm25_score(),
        )
        qb = col.query(sel)
        qb = qb.filter(match(q_norm) | match(q_ascii))
        qb = qb.topk(field("text_score"), k)
        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except:
        return []

def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    if not _QUERY_IMPORTED: return []
    q_norm = _norm_spaces(q)
    num = _extract_number(q_norm)
    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            sim_texto  = fn.semantic_similarity(TEXT_FIELD, q_norm),
            sim_ementa = fn.semantic_similarity(EMENTA_FIELD, q_norm),
            sim_titulo = fn.semantic_similarity(TITULO_FIELD, q_norm),
            text_score = fn.bm25_score(),
        )
        qb = col.query(sel)

        sem_mix = (
            W_TEXT*field("sim_texto") +
            W_EMENTA*field("sim_ementa") +
            W_TITULO*field("sim_titulo")
        )
        base = SEM_WEIGHT * sem_mix + LEX_WEIGHT * field("text_score")

        if num:
            qb = qb.topk(base + 0.05*field("text_score"), k)
        else:
            qb = qb.topk(base, k)

        try: qb = qb.rerank()
        except: pass

        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except:
        return []


# ============================================================
# 🔥 API PRINCIPAL — MODO ÚNICO (retrocompatível)
# ============================================================
def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if not query:
        return []

    if _collection is None:
        _init()
        if _collection is None:
            return []

    # ID-like → keyword first
    if _is_id_like(query):
        res = _keyword_query(_collection, query, k)
    else:
        res = _hybrid_query(_collection, query, k)

    # fallback semântico puro
    if not res:
        try:
            rows = _collection.query(
                select(
                    "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
                    TEXT_FIELD, EMENTA_FIELD,
                    sim = fn.semantic_similarity(TEXT_FIELD, _norm_spaces(query)),
                ).topk(field("sim"), k)
            )
            res = [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
        except:
            pass

    res = [r for r in res if (r.get("trecho") or "").strip()]
    return _dedupe(res)[:k]


def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """Versão original — busca apenas na coleção principal."""
    return search_topk(query, k)


# ============================================================
# 🔥 NOVO MODO — MULTI-COLEÇÕES
# ============================================================
def buscar_topk_multi(query: str, k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """
    Executa search_topk(query) em TODAS as coleções listadas no .env:
        TOPK_COLLECTIONS="Portaria,Diretriz,Lei,..."

    Retorna:
        {
           "Portaria": [...],
           "Diretriz": [...],
           ...
        }
    """

    resultados: Dict[str, List[Dict[str, Any]]] = {}

    for col_name in MULTI_COLLECTIONS:
        col = _init_collection(col_name)
        if col is None:
            resultados[col_name] = []
            continue

        # executa exatamente o mesmo pipeline (keyword → híbrido → semântico puro)
        def run(col):
            if _is_id_like(query):
                res = _keyword_query(col, query, k)
            else:
                res = _hybrid_query(col, query, k)

            if not res:
                # fallback
                try:
                    rows = col.query(
                        select(
                            "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
                            TEXT_FIELD, EMENTA_FIELD,
                            sim = fn.semantic_similarity(TEXT_FIELD, _norm_spaces(query)),
                        ).topk(field("sim"), k)
                    )
                    res = [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
                except:
                    res = []

            res = [r for r in res if (r.get("trecho") or "").strip()]
            return _dedupe(res)[:k]

        resultados[col_name] = run(col)

    return resultados


# ============================================================
# ℹ️ STATUS
# ============================================================
def topk_status() -> Dict[str, Any]:
    return {
        "sdk_imported": _SDK_IMPORTED,
        "query_dsl_imported": _QUERY_IMPORTED,
        "api_key_set": bool(os.getenv("TOPK_API_KEY")),
        "region": os.getenv("TOPK_REGION"),
        "collection_name": COLLECTION_NAME,
        "multi_collections": MULTI_COLLECTIONS,
        "initialized": _collection is not None and _init_error is None,
        "init_error": _init_error,
    }


__all__ = [
    "search_topk",
    "buscar_topk",
    "buscar_topk_multi",
    "topk_status"
]
