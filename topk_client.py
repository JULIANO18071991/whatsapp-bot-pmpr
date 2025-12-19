# topk_client.py — MULTI-COLEÇÕES (hardcoded, estável)
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ============================================================
# 🔧 CONFIGURAÇÃO FIXA (SEM ENV)
# ============================================================

# Coleção principal (retrocompatibilidade)
COLLECTION_NAME = "Portaria"

# 🔥 COLEÇÕES DEFINIDAS DIRETAMENTE NO CÓDIGO
MULTI_COLLECTIONS = [
    "Portaria",
    "Diretriz",
    "Lei",
    "Decreto",
    "Memorando",
    "Orientacoes",
    "Manuais",
    "NotaInstrucao",
    "POP",
    "PAP",
    "Resolucao",
]

# Campos do schema
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

_collection = None
_init_error: Optional[str] = None


def _init_collection(name: str):
    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")

    if not api_key or not region or not Client:
        _dbg("TopK não configurado corretamente.")
        return None

    try:
        client = Client(api_key=api_key, region=region)

        if hasattr(client, "collection"):
            return client.collection(name)

        if hasattr(client, "collections"):
            return client.collections[name]

        if hasattr(client, "get_collection"):
            return client.get_collection(name)

    except Exception as e:
        _dbg(f"Erro ao inicializar coleção '{name}': {e}")

    return None


def _init() -> None:
    global _collection, _init_error
    _collection = _init_collection(COLLECTION_NAME)
    if _collection is None:
        _init_error = f"collection_unavailable:{COLLECTION_NAME}"
    else:
        _init_error = None


_init()

# ============================================================
# 🔧 UTILS (INALTERADOS)
# ============================================================
def _as_dict(rec: Any) -> Dict[str, Any]:
    return rec if isinstance(rec, dict) else getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    texto = (item.get(TEXT_FIELD) or "").strip()
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    return f"{ementa}\n\n{texto}" if texto and ementa else texto or ementa

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    return {
        "doc_id": item.get("doc_id") or item.get("id") or "-",
        "artigo_numero": item.get(ART_FIELD) or "-",
        "titulo": item.get(TITULO_FIELD) or "",
        "trecho": _merge_excerto(item),
        "score": item.get("score") or item.get("_score"),
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

def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(c).startswith("Z") else c for c in q).strip()

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _is_id_like(q: str) -> bool:
    return "portaria" in q.lower() and _extract_number(q) is not None


# ============================================================
# 🔍 CONSULTAS (INALTERADAS)
# ============================================================
def _keyword_query(col, q: str, k: int):
    sel = select(
        "doc_id", TITULO_FIELD, PORTARIA_FIELD, ANO_FIELD,
        TEXT_FIELD, EMENTA_FIELD,
        text_score=fn.bm25_score(),
    )
    qb = col.query(sel).filter(match(q)).topk(field("text_score"), k)
    return [_normalize_item(r) for r in qb]

def _hybrid_query(col, q: str, k: int):
    sel = select(
        "doc_id", TITULO_FIELD, PORTARIA_FIELD, ANO_FIELD,
        TEXT_FIELD, EMENTA_FIELD,
        sim=fn.semantic_similarity(TEXT_FIELD, q),
        text_score=fn.bm25_score(),
    )
    qb = col.query(sel).topk(
        SEM_WEIGHT * field("sim") + LEX_WEIGHT * field("text_score"),
        k
    )
    return [_normalize_item(r) for r in qb]


# ============================================================
# 🔥 MULTI-COLEÇÕES (FINAL)
# ============================================================
def buscar_topk_multi(query: str, k: int = 5) -> List[Dict[str, Any]]:
    resultados: List[Dict[str, Any]] = []

    for name in MULTI_COLLECTIONS:
        col = _init_collection(name)
        if not col:
            _dbg(f"Coleção '{name}' não encontrada.")
            continue

        try:
            res = _keyword_query(col, query, k) if _is_id_like(query) else _hybrid_query(col, query, k)
            resultados.extend(res)
        except Exception as e:
            _dbg(f"Erro na coleção '{name}': {e}")

    return _dedupe(resultados)[:k]


__all__ = ["buscar_topk_multi"]
