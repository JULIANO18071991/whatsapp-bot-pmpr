# topk_client.py — MULTI-COLEÇÕES FIXAS (estável)
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ============================================================
# 🔧 COLEÇÕES FIXAS (SEM ENV)
# ============================================================
COLLECTIONS = [
    "Portaria",
    "Diretriz",
    "Lei",
    "Decreto",
    "Memorando",
    "Orientacoes",
    "Manuais",
    "Nota_Instrucao",
    "POP",
    "PAP",
    "Resolucao",
]

# Coleção padrão (retrocompatível com o bot)
COLLECTION_NAME = "Portaria"

# ============================================================
# 🔧 SCHEMA
# ============================================================
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
# 🔧 SDK
# ============================================================
try:
    from topk_sdk import Client
    from topk_sdk.query import select, field, fn, match
    SDK_OK = True
except Exception:
    SDK_OK = False

_client = None
_col_cache: Dict[str, Any] = {}

def _get_client():
    global _client
    if _client:
        return _client

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    if not api_key or not region:
        raise RuntimeError("TOPK_API_KEY ou TOPK_REGION ausente")

    _client = Client(api_key=api_key, region=region)
    return _client

def _get_collection(name: str):
    if name in _col_cache:
        return _col_cache[name]

    try:
        col = _get_client().collection(name)
        _col_cache[name] = col
        return col
    except Exception as e:
        _dbg(f"Coleção '{name}' não encontrada: {e}")
        return None

# ============================================================
# 🔧 UTILS
# ============================================================
def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(c).startswith("Z") else c for c in q).strip()

def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _is_id_like(q: str) -> bool:
    ql = _ascii(q.lower())
    return "portaria" in ql and _extract_number(ql)

def _as_dict(r: Any) -> Dict[str, Any]:
    return r if isinstance(r, dict) else getattr(r, "__dict__", {})

def _normalize(raw: Any) -> Dict[str, Any]:
    r = _as_dict(raw)
    return {
        "doc_id": r.get("doc_id") or r.get("id") or "-",
        "artigo_numero": r.get(ART_FIELD) or "-",
        "titulo": r.get(TITULO_FIELD) or "",
        "trecho": r.get("trecho") or r.get(TEXT_FIELD) or r.get(EMENTA_FIELD) or "",
        "score": r.get("score") or r.get("text_score") or r.get("sim"),
        "numero_portaria": r.get(PORTARIA_FIELD) or "",
        "ano": r.get(ANO_FIELD) or "",
        "_raw": r,
    }

def _dedupe(lst: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for i in lst:
        k = (i["doc_id"], i["artigo_numero"], i["titulo"])
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out

# ============================================================
# 🔍 BUSCAS
# ============================================================
def _hybrid(col, q: str, k: int):
    qn = _norm_spaces(q)
    sel = select(
        "doc_id", TITULO_FIELD, PORTARIA_FIELD, ANO_FIELD, TEXT_FIELD, EMENTA_FIELD,
        sim_texto=fn.semantic_similarity(TEXT_FIELD, qn),
        sim_ementa=fn.semantic_similarity(EMENTA_FIELD, qn),
        sim_titulo=fn.semantic_similarity(TITULO_FIELD, qn),
        text_score=fn.bm25_score(),
    )
    qb = col.query(sel)
    score = (
        SEM_WEIGHT * (
            W_TEXT * field("sim_texto") +
            W_EMENTA * field("sim_ementa") +
            W_TITULO * field("sim_titulo")
        )
        + LEX_WEIGHT * field("text_score")
    )
    qb = qb.topk(score, k)
    return [_normalize(r) for r in qb]

# ============================================================
# 🔥 API PÚBLICA (NÃO QUEBRA O BOT)
# ============================================================
def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    col = _get_collection(COLLECTION_NAME)
    if not col:
        return []
    return _dedupe(_hybrid(col, query, k))[:k]

def buscar_topk_multi(query: str, k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name in COLLECTIONS:
        col = _get_collection(name)
        if not col:
            out[name] = []
            continue
        out[name] = _dedupe(_hybrid(col, query, k))[:k]
    return out

def topk_status():
    return {
        "collections": COLLECTIONS,
        "cache": list(_col_cache.keys()),
        "sdk_ok": SDK_OK,
    }

__all__ = ["buscar_topk", "buscar_topk_multi", "topk_status"]
