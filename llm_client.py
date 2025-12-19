# topk_client.py (compatível com o schema atual)
# -*- coding: utf-8 -*-
"""
Busca HÍBRIDA (Semântica + BM25) para documentos oficiais (schema atual):
- Keyword: assuntos, data, doc_id, numero, tipo_documento
- Semantic: titulo, ementa, texto
Ajustes:
- Remove 'caput' do score (não existe no schema)
- Não usa filtro lexical obrigatório (evita zerar resultados)
- Semântica: texto (0.7), ementa (0.15), titulo (0.15)
- BM25: campos keyword disponíveis
- Roteamento: ID-like -> keyword-first -> híbrido -> semântico
"""

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ------------------ configuração ------------------
COLLECTION_NAME = os.getenv("TOPK_COLLECTION", "pmpr_portarias")

# 🔥 COLEÇÕES PARA BUSCA MÚLTIPLA
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

# Campos conforme schema
TEXT_FIELD    = os.getenv("TOPK_TEXT_FIELD", "texto")
EMENTA_FIELD  = os.getenv("TOPK_EMENTA_FIELD", "ementa")
TITULO_FIELD  = os.getenv("TOPK_TITULO_FIELD", "titulo")

# Metadados
PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero")
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "data")
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")

# Pesos
SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))
W_TEXT   = float(os.getenv("TOPK_W_TEXT",   "0.4"))
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.3"))
W_TITULO = float(os.getenv("TOPK_W_TITULO", "0.3"))

KEYWORD_FIELDS = ["doc_id", "numero", "data", "assuntos", "tipo_documento"]

# ------------------ SDK / DSL ------------------
_SDK_IMPORTED = True
try:
    from topk_sdk import Client
except Exception:
    _SDK_IMPORTED = False
    Client = None

_QUERY_IMPORTED = True
try:
    from topk_sdk.query import select, field, fn, match
except Exception:
    _QUERY_IMPORTED = False

_client = None
_collection = None
_init_error: Optional[str] = None

def _init() -> None:
    global _client, _collection, _init_error
    if not _SDK_IMPORTED or Client is None:
        _init_error = "sdk_not_imported"
        return

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    if not api_key or not region:
        _init_error = "missing_env"
        return

    try:
        _client = Client(api_key=api_key, region=region)
        col = None
        try:
            col = _client.collection(COLLECTION_NAME)
        except Exception:
            pass

        _collection = col
        if _collection is None:
            _init_error = "collection_unavailable"
            return
        _init_error = None
    except Exception as e:
        _collection = None
        _init_error = f"init_error:{e}"

_init()

# ------------------ utils ------------------
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    texto  = (item.get(TEXT_FIELD) or "").strip()
    return " ".join(p for p in [ementa, texto] if p)

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    return {
        "doc_id": item.get("doc_id") or item.get("id") or "-",
        "artigo_numero": item.get(ART_FIELD) or "-",
        "titulo": item.get(TITULO_FIELD) or "-",
        "trecho": (
            item.get("trecho")
            or _merge_excerto(item)
            or item.get("text")
            or ""
        ).strip(),
        "score": item.get("score") or item.get("text_score") or item.get("sim"),
        "numero_portaria": item.get(PORTARIA_FIELD) or "",
        "ano": item.get(ANO_FIELD) or "",
        "_raw": item,
    }

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set(); out = []
    for it in items:
        key = (it["doc_id"], it["artigo_numero"], it["titulo"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out

def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(c).startswith("Z") else c for c in q).strip()

def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _is_id_like(q: str) -> bool:
    ql = _ascii(q.lower())
    return "portaria" in ql and _extract_number(ql) is not None

# ------------------ consultas ------------------
def _keyword_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    if not _QUERY_IMPORTED: return []
    try:
        rows = (
            col.query(
                select(
                    "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
                    TEXT_FIELD, EMENTA_FIELD,
                    text_score=fn.bm25_score(),
                )
            )
            .filter(match(q) | match(_ascii(q)))
            .topk(field("text_score"), k)
        )
        return [_normalize_item(r) for r in rows]
    except Exception:
        return []

def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    if not _QUERY_IMPORTED: return []
    try:
        rows = (
            col.query(
                select(
                    "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
                    TEXT_FIELD, EMENTA_FIELD,
                    sim_texto=fn.semantic_similarity(TEXT_FIELD, q),
                    sim_ementa=fn.semantic_similarity(EMENTA_FIELD, q),
                    sim_titulo=fn.semantic_similarity(TITULO_FIELD, q),
                    text_score=fn.bm25_score(),
                )
            )
            .topk(
                SEM_WEIGHT * (
                    W_TEXT*field("sim_texto") +
                    W_EMENTA*field("sim_ementa") +
                    W_TITULO*field("sim_titulo")
                ) + LEX_WEIGHT * field("text_score"),
                k
            )
        )
        return [_normalize_item(r) for r in rows]
    except Exception:
        return []

# ------------------ API pública ------------------
def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if not query or _collection is None:
        return []

    res = _keyword_query(_collection, query, k) if _is_id_like(query) else []
    if not res:
        res = _hybrid_query(_collection, query, k)

    return _dedupe([r for r in res if r["trecho"]])[:k]

def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    return search_topk(query, k)

# 🔥 BUSCA EM MÚLTIPLAS COLEÇÕES (ADIÇÃO SOLICITADA)
def buscar_topk_multi(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if not query or not Client:
        return []

    resultados: List[Dict[str, Any]] = []

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    client = Client(api_key=api_key, region=region)

    for nome in MULTI_COLLECTIONS:
        try:
            col = client.collection(nome)
        except Exception:
            continue

        res = _keyword_query(col, query, k) if _is_id_like(query) else []
        if not res:
            res = _hybrid_query(col, query, k)

        resultados.extend(r for r in res if r["trecho"])

    return _dedupe(resultados)[:k]

def topk_status() -> Dict[str, Any]:
    return {
        "initialized": _collection is not None and _init_error is None,
        "collection": COLLECTION_NAME,
        "multi_collections": MULTI_COLLECTIONS,
        "init_error": _init_error,
    }

__all__ = ["search_topk", "buscar_topk", "buscar_topk_multi", "topk_status"]
