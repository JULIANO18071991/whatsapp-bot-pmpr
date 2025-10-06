# topk_client.py
# -*- coding: utf-8 -*-
"""
Busca HÍBRIDA (Semântica + BM25) para documentos oficiais.
- Pesos default: 0.8 (semântica) / 0.2 (BM25)
- Multi-campo semântico: texto (0.6), caput (0.25), ementa (0.15)
- Filtro lexical em .filter(match(...)) (compatível com o quick_test_topk.py)
- Resiliência para número: (field == num) OR match(num)
- Rerank no final do top-k + fallback por número
- API: search_topk(query, k=5) / buscar_topk(query, k=5)
"""

from __future__ import annotations
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional

DEBUG = os.getenv("DEBUG", "0") == "1"
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[TOPK DEBUG] {msg}")

# ------------------ configuração ------------------
COLLECTION_NAME = os.getenv("TOPK_COLLECTION", "pmpr_portarias")

# campos textuais indexados semanticamente
TEXT_FIELD   = os.getenv("TOPK_TEXT_FIELD", "texto")
CAPUT_FIELD  = os.getenv("TOPK_CAPUT_FIELD", "caput")
EMENTA_FIELD = os.getenv("TOPK_EMENTA_FIELD", "ementa")

# metadados úteis
PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero_portaria")
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "ano")
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")

# pesos: híbrido 80/20 e multi-campos
SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))     # 0.8
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))     # 0.2

W_TEXT   = float(os.getenv("TOPK_W_TEXT",   "0.6"))         # texto
W_CAPUT  = float(os.getenv("TOPK_W_CAPUT",  "0.25"))        # caput
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.15"))        # ementa

# ------------------ SDK / DSL ------------------
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

def _init() -> None:
    """Inicializa client/coleção."""
    global _client, _collection, _init_error
    if not _SDK_IMPORTED or Client is None:
        _init_error = "sdk_not_imported"
        print("[WARN TOPK] SDK topk_sdk indisponível.")
        return

    api_key = os.getenv("TOPK_API_KEY")
    region  = os.getenv("TOPK_REGION")
    if not api_key or not region:
        _init_error = "missing_env"
        print("[WARN TOPK] TOPK_API_KEY/TOPK_REGION ausentes.")
        return

    try:
        _client = Client(api_key=api_key, region=region)  # type: ignore
        col = None
        if hasattr(_client, "collection"):
            try:
                col = _client.collection(COLLECTION_NAME)  # type: ignore
            except Exception as e:
                _dbg(f"collection() falhou: {e}")
        if col is None and hasattr(_client, "collections"):
            try:
                col = _client.collections[COLLECTION_NAME]  # type: ignore
            except Exception as e:
                _dbg(f"collections[...] falhou: {e}")
        if col is None and hasattr(_client, "get_collection"):
            try:
                col = _client.get_collection(COLLECTION_NAME)  # type: ignore
            except Exception as e:
                _dbg(f"get_collection() falhou: {e}")

        _collection = col
        if _collection is None:
            _init_error = "collection_unavailable"
            print(f"[WARN TOPK] Coleção '{COLLECTION_NAME}' não disponível.")
            return
        _init_error = None
        if DEBUG:
            _dbg(f"type(collection)={type(_collection)}")
    except Exception as e:
        _client = None
        _collection = None
        _init_error = f"init_error:{e}"
        print(f"[WARN TOPK] Falha ao inicializar: {e}")

_init()

# ------------------ util ------------------
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _merge_caput_texto(item: Dict[str, Any]) -> str:
    caput = (item.get(CAPUT_FIELD) or "").strip()
    texto = (item.get(TEXT_FIELD) or "").strip()
    if caput and texto and texto.startswith(caput):
        return texto
    return " ".join([p for p in [caput, texto] if p]).strip()

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    doc_id = item.get("doc_id") or item.get("document_id") or item.get("id") or item.get("_id") or "-"
    artigo = item.get(ART_FIELD) or item.get("artigo") or item.get("section") or "-"
    titulo = item.get("titulo") or item.get("title") or item.get("document_title") or "-"
    excerto = (
        item.get("trecho")
        or item.get("excerto")
        or _merge_caput_texto(item)
        or item.get(EMENTA_FIELD)
        or item.get("text")
        or item.get("chunk")
        or item.get("content")
        or ""
    ).strip()
    score = item.get("score") or item.get("text_score") or item.get("sim") or item.get("similarity") or item.get("_score") or None
    url = item.get("url") or item.get("source_url") or None
    numero = item.get(PORTARIA_FIELD) or item.get("num") or ""
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
    return "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in q).strip()

def _ascii(q: str) -> str:
    return unicodedata.normalize("NFD", q).encode("ascii", "ignore").decode("ascii")

def _extract_number(q: str) -> Optional[str]:
    m = re.search(r"\b(\d{2,6})\b", q)
    return m.group(1) if m else None

# ------------------ consulta híbrida 80/20 ------------------
def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    """
    score = 0.8 * (0.6*sim(texto) + 0.25*sim(caput) + 0.15*sim(ementa)) + 0.2 * bm25
    - BM25 precisa de pelo menos um text filter em .filter(match(...)).
    """
    if not _QUERY_IMPORTED:
        _dbg("topk_sdk.query indisponível — instale topk-sdk>=0.5.0")
        return []

    q_norm  = _norm_spaces(q)
    q_ascii = _ascii(q_norm)
    num = _extract_number(q_norm)

    try:
        sel = select(
            "doc_id", "titulo", ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, CAPUT_FIELD, EMENTA_FIELD,  # úteis p/ normalizar trecho
            # componentes de score
            sim_texto  = fn.semantic_similarity(TEXT_FIELD,   q_norm),
            sim_caput  = fn.semantic_similarity(CAPUT_FIELD,  q_norm),
            sim_ementa = fn.semantic_similarity(EMENTA_FIELD, q_norm),
            text_score = fn.bm25_score(),
        )

        qbuilder = col.query(sel)

        # >>> espelhado no quick_test_topk: text filter em .filter(...)
        qbuilder = qbuilder.filter( match(q_norm) | match(q_ascii) )

        # Filtro/boost por número (resiliente)
        if num:
            try:
                qbuilder = qbuilder.filter( (field(PORTARIA_FIELD) == num) | match(num) )
            except Exception:
                qbuilder = qbuilder.filter( match(num) )

        # score híbrido 80/20 + pesos multi-campo
        sem_mix = (W_TEXT*field("sim_texto") + W_CAPUT*field("sim_caput") + W_EMENTA*field("sim_ementa"))
        final_score = SEM_WEIGHT * sem_mix + LEX_WEIGHT * field("text_score")

        qbuilder = qbuilder.topk(final_score, k)

        # rerank final
        try:
            qbuilder = qbuilder.rerank()
        except Exception:
            pass

        rows = qbuilder
        out = [_normalize_item(r) for r in rows] if isinstance(rows, list) else []

        # Fallback defensivo por número (se nada vier e há num)
        if not out and num:
            _dbg("fallback:minimal_number_query")
            try:
                sel2 = select(
                    "doc_id","titulo",ART_FIELD,PORTARIA_FIELD,ANO_FIELD,
                    TEXT_FIELD,CAPUT_FIELD,EMENTA_FIELD,
                    sim = fn.semantic_similarity(TEXT_FIELD, q_norm),
                )
                qb2 = col.query(sel2).filter( (field(PORTARIA_FIELD) == num) | match(num) )
                qb2 = qb2.topk(field("sim"), k)
                try: qb2 = qb2.rerank()
                except Exception: pass
                rows2 = qb2
                out = [_normalize_item(r) for r in rows2] if isinstance(rows2, list) else []
            except Exception as e:
                _dbg(f"fallback minimal falhou: {e}")

        return out
    except Exception as e:
        _dbg(f"hibrida 80/20 falhou: {e}")
        return []

# ------------------ API pública ------------------
def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Retorna [{doc_id, artigo_numero, titulo, trecho, score, url, numero_portaria, ano, _raw}, ...]
    """
    if not query:
        return []
    if _collection is None:
        _init()
        if _collection is None:
            return []

    results = _hybrid_query(_collection, query, k)  # type: ignore

    # fallback extra: semântico puro (preservando num se existir)
    if not results and _QUERY_IMPORTED:
        try:
            q_norm = _norm_spaces(query)
            sel = select(
                "doc_id","titulo",ART_FIELD,PORTARIA_FIELD,ANO_FIELD,
                TEXT_FIELD,CAPUT_FIELD,EMENTA_FIELD,
                sim = fn.semantic_similarity(TEXT_FIELD, q_norm),
            )
            qb = _collection.query(sel)
            num = _extract_number(q_norm)
            if num:
                qb = qb.filter( (field(PORTARIA_FIELD) == num) | match(num) )
            rows = qb.topk(field("sim"), k)
            results = [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
        except Exception:
            pass

    sane = [r for r in results if (r.get("trecho") or "").strip()]
    if DEBUG:
        _dbg(f"search_topk: query='{query}' -> {len(sane)} itens (antes dedupe {len(results)})")
    return _dedupe(sane)[:k]

def buscar_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    return search_topk(query, k)

def topk_status() -> Dict[str, Any]:
    return {
        "sdk_imported": _SDK_IMPORTED,
        "query_dsl_imported": _QUERY_IMPORTED,
        "api_key_set": bool(os.getenv("TOPK_API_KEY")),
        "region": os.getenv("TOPK_REGION"),
        "collection_name": COLLECTION_NAME,
        "text_field": TEXT_FIELD,
        "initialized": _collection is not None and _init_error is None,
        "init_error": _init_error,
        "weights": {
            "hybrid_sem": SEM_WEIGHT, "hybrid_lex": LEX_WEIGHT,
            "w_text": W_TEXT, "w_caput": W_CAPUT, "w_ementa": W_EMENTA,
        }
    }

__all__ = ["search_topk", "buscar_topk", "topk_status"]
