# topk_client.py
# -*- coding: utf-8 -*-
"""
Busca HÍBRIDA (Semântica + BM25) MULTI-COLEÇÃO para documentos oficiais.

Fluxo (por coleção):
- Se a query parece "ID-like" (ex.: "diretriz 004", "portaria 277"), tenta keyword-first (BM25 puro)
- Se não vier nada, tenta híbrido (semântica + BM25)
- Se BM25 falhar (por falta de match/keyword index), faz fallback semântico puro

Retorno:
- Dict[colecao -> lista de itens normalizados], cada item com "fonte_colecao"
"""

from __future__ import annotations
import os, re, unicodedata
from typing import Any, Dict, List, Optional

# ==========================================================
# DEBUG
# ==========================================================
DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ==========================================================
# CONFIG
# ==========================================================
TOPK_COLLECTIONS = [
    c.strip() for c in os.getenv(
        "TOPK_COLLECTIONS",
        "Decreto,Diretriz,Lei,Manuais,Memorando,Nota_de_Instrucao,Orientacoes,PAP,POP,Portaria,Resolucao"
    ).split(",")
    if c.strip()
]

# Campos semânticos (conforme seu schema)
TEXT_FIELD    = os.getenv("TOPK_TEXT_FIELD", "texto")
EMENTA_FIELD  = os.getenv("TOPK_EMENTA_FIELD", "ementa")
TITULO_FIELD  = os.getenv("TOPK_TITULO_FIELD", "titulo")

# Metadados (keyword)
PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero")
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "data")
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")  # pode não existir em alguns docs

# Pesos híbridos
SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))

W_TEXT   = float(os.getenv("TOPK_W_TEXT",   "0.4"))
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.3"))
W_TITULO = float(os.getenv("TOPK_W_TITULO", "0.3"))

# ==========================================================
# SDK / QUERY DSL
# ==========================================================
try:
    from topk_sdk import Client  # type: ignore
    from topk_sdk.query import select, field, fn, match  # type: ignore
    _SDK_IMPORTED = True
    _QUERY_IMPORTED = True
except Exception:
    Client = None  # type: ignore
    _SDK_IMPORTED = False
    _QUERY_IMPORTED = False

_client = None
_collections: Dict[str, Any] = {}
_init_error: Optional[str] = None

# ==========================================================
# INIT
# ==========================================================
def _init() -> None:
    global _client, _collections, _init_error

    if not _SDK_IMPORTED or Client is None:
        _init_error = "sdk_not_imported"
        _dbg("SDK topk_sdk indisponível.")
        return

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")

    if not api_key or not region:
        _init_error = "missing_env"
        _dbg("TOPK_API_KEY/TOPK_REGION ausentes.")
        return

    try:
        _client = Client(api_key=api_key, region=region)  # type: ignore
        _collections = {}

        for name in TOPK_COLLECTIONS:
            try:
                col = _client.collection(name)  # type: ignore
                _collections[name] = col
                _dbg(f"Coleção carregada: {name}")
            except Exception as e:
                _dbg(f"Falha ao carregar coleção {name}: {e}")

        _init_error = None if _collections else "no_collections_loaded"

    except Exception as e:
        _init_error = f"init_error:{e}"
        _collections = {}

_init()

# ==========================================================
# UTILS
# ==========================================================
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _norm_spaces(q: str) -> str:
    return "".join(" " if unicodedata.category(c).startswith("Z") else c for c in q).strip()

def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

def _merge_excerto(item: Dict[str, Any]) -> str:
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    texto  = (item.get(TEXT_FIELD) or "").strip()
    return " ".join(p for p in [ementa, texto] if p).strip()

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    doc_id = item.get("doc_id") or item.get("document_id") or item.get("id") or item.get("_id") or "-"
    return {
        "doc_id": doc_id,
        "artigo_numero": item.get(ART_FIELD) or item.get("artigo") or item.get("section") or "-",
        "titulo": item.get(TITULO_FIELD) or item.get("title") or "-",
        "trecho": (
            item.get("trecho")
            or item.get("excerto")
            or _merge_excerto(item)
            or item.get("text")
            or item.get("content")
            or ""
        ).strip(),
        "score": item.get("score") or item.get("text_score") or item.get("sim") or item.get("similarity") or item.get("_score"),
        "numero_portaria": item.get(PORTARIA_FIELD) or item.get("num") or "",
        "ano": item.get(ANO_FIELD) or "",
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

def _is_id_like(q: str) -> bool:
    """
    Considera "id-like" quando menciona um tipo + número:
      - diretriz 004
      - portaria 277
      - lei 1234
      - decreto 999
      - resolução 12 / resolucao 12
      - memorando 55
      - nota de instrucao 10
    """
    ql = _ascii(q.lower())
    num = _extract_number(ql)
    if not num:
        return False

    tipos = [
        "portaria", "diretriz", "lei", "decreto", "resolucao", "resolução",
        "memorando", "manual", "orientacao", "orientação", "pap", "pop",
        "nota de instrucao", "nota de instrução", "nota_de_instrucao",
    ]
    return any(t in ql for t in tipos)

# ==========================================================
# QUERIES
# ==========================================================
def _keyword_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    """BM25 puro (keyword). bm25_score exige ter match() no filter."""  # docs
    if not _QUERY_IMPORTED:
        return []

    qn = _norm_spaces(q)
    qa = _ascii(qn)

    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD,
            PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            text_score=fn.bm25_score(),
        )

        qb = col.query(sel)

        # IMPORTANTE: match() precisa existir quando bm25_score é usado
        qb = qb.filter(match(qn) | match(qa))

        qb = qb.topk(field("text_score"), k)
        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else [_normalize_item(r) for r in qb]
    except Exception as e:
        _dbg(f"keyword_query erro: {e}")
        return []

def _semantic_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    """Fallback semântico puro (sem bm25)."""
    if not _QUERY_IMPORTED:
        return []

    qn = _norm_spaces(q)
    try:
        qb = col.query(
            select(
                "doc_id", TITULO_FIELD, ART_FIELD,
                PORTARIA_FIELD, ANO_FIELD,
                TEXT_FIELD, EMENTA_FIELD,
                sim=fn.semantic_similarity(TEXT_FIELD, qn),
            ).topk(field("sim"), k)
        )
        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else [_normalize_item(r) for r in qb]
    except Exception as e:
        _dbg(f"semantic_query erro: {e}")
        return []

def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    """
    Híbrido:
      score = SEM_WEIGHT * (W_TEXT*sim(texto) + W_EMENTA*sim(ementa) + W_TITULO*sim(titulo))
            + LEX_WEIGHT * bm25
    Observação: bm25_score exige match() no filter (docs).
    """
    if not _QUERY_IMPORTED:
        return []

    qn = _norm_spaces(q)
    qa = _ascii(qn)
    num = _extract_number(qn)

    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD,
            PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            sim_texto=fn.semantic_similarity(TEXT_FIELD, qn),
            sim_ementa=fn.semantic_similarity(EMENTA_FIELD, qn),
            sim_titulo=fn.semantic_similarity(TITULO_FIELD, qn),
            text_score=fn.bm25_score(),
        )

        qb = col.query(sel)

        # ✅ OBRIGATÓRIO: para bm25_score funcionar, precisa de match() no filter
        # match() sem field busca em todos os campos com keyword_index
        qb = qb.filter(match(qn) | match(qa))

        sem_mix = (
            W_TEXT * field("sim_texto") +
            W_EMENTA * field("sim_ementa") +
            W_TITULO * field("sim_titulo")
        )

        score = SEM_WEIGHT * sem_mix + LEX_WEIGHT * field("text_score")

        # boost leve se houver número explícito (ex: "diretriz 004")
        if num:
            score = score + 0.05 * field("text_score")

        qb = qb.topk(score, k)

        try:
            qb = qb.rerank()
        except Exception:
            pass

        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else [_normalize_item(r) for r in qb]

    except Exception as e:
        _dbg(f"hybrid_query erro: {e}")
        # fallback semântico para não “morrer” a busca inteira
        return _semantic_query(col, q, k)

# ==========================================================
# API PÚBLICA — MULTI-COLEÇÃO
# ==========================================================
def search_topk_multi(query: str, k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    if not query:
        return {}

    if not _collections:
        _init()
        if not _collections:
            return {}

    output: Dict[str, List[Dict[str, Any]]] = {}

    for name, col in _collections.items():
        results: List[Dict[str, Any]] = []

        # keyword-first quando parece consulta por “número do ato”
        if _is_id_like(query):
            results = _keyword_query(col, query, k)

        # híbrido
        if not results:
            results = _hybrid_query(col, query, k)

        sane = [r for r in results if (r.get("trecho") or "").strip()]
        if sane:
            for r in sane:
                r["fonte_colecao"] = name
            output[name] = _dedupe(sane)[:k]

        _dbg(f"[{name}] {len(sane)} resultados")

    return output

def buscar_topk_multi(query: str, k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    return search_topk_multi(query, k)

def topk_status() -> Dict[str, Any]:
    return {
        "initialized": bool(_collections),
        "collections_loaded": list(_collections.keys()),
        "init_error": _init_error,
        "weights": {
            "semantic": SEM_WEIGHT,
            "lexical": LEX_WEIGHT,
            "w_text": W_TEXT,
            "w_ementa": W_EMENTA,
            "w_titulo": W_TITULO,
        },
        "fields": {
            "text": TEXT_FIELD,
            "ementa": EMENTA_FIELD,
            "titulo": TITULO_FIELD,
            "numero": PORTARIA_FIELD,
            "data": ANO_FIELD,
            "art": ART_FIELD,
        },
    }

__all__ = [
    "search_topk_multi",
    "buscar_topk_multi",
    "topk_status",
]
