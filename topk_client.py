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

# Campos conforme schema
TEXT_FIELD    = os.getenv("TOPK_TEXT_FIELD", "texto")     # Semantic
EMENTA_FIELD  = os.getenv("TOPK_EMENTA_FIELD", "ementa")  # Semantic
TITULO_FIELD  = os.getenv("TOPK_TITULO_FIELD", "titulo")  # Semantic

# Metadados
PORTARIA_FIELD = os.getenv("TOPK_NUM_FIELD", "numero")    # Keyword
ANO_FIELD      = os.getenv("TOPK_ANO_FIELD", "data")      # Keyword (data texto)
ART_FIELD      = os.getenv("TOPK_ART_FIELD", "artigo_numero")  # pode não existir em todos os docs

# Pesos
SEM_WEIGHT = float(os.getenv("TOPK_SEM_WEIGHT", "0.8"))
LEX_WEIGHT = float(os.getenv("TOPK_LEX_WEIGHT", "0.2"))
W_TEXT   = float(os.getenv("TOPK_W_TEXT",   "0.4"))
W_EMENTA = float(os.getenv("TOPK_W_EMENTA", "0.3"))
W_TITULO = float(os.getenv("TOPK_W_TITULO", "0.3"))

# Campos lexicais disponíveis no schema
KEYWORD_FIELDS = [
    "doc_id", "numero", "data", "assuntos", "tipo_documento"
]

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
            try: col = _client.collection(COLLECTION_NAME)  # type: ignore
            except Exception as e: _dbg(f"collection() falhou: {e}")
        if col is None and hasattr(_client, "collections"):
            try: col = _client.collections[COLLECTION_NAME]  # type: ignore
            except Exception as e: _dbg(f"collections[...] falhou: {e}")
        if col is None and hasattr(_client, "get_collection"):
            try: col = _client.get_collection(COLLECTION_NAME)  # type: ignore
            except Exception as e: _dbg(f"get_collection() falhou: {e}")

        _collection = col
        if _collection is None:
            _init_error = "collection_unavailable"
            print(f"[WARN TOPK] Coleção '{COLLECTION_NAME}' não disponível.")
            return
        _init_error = None
        _dbg(f"type(collection)={type(_collection)}")
    except Exception as e:
        _client = None
        _collection = None
        _init_error = f"init_error:{e}"
        print(f"[WARN TOPK] Falha ao inicializar: {e}")

_init()

# ------------------ utils ------------------
def _as_dict(rec: Any) -> Dict[str, Any]:
    if isinstance(rec, dict):
        return rec
    return getattr(rec, "__dict__", {}) or {}

def _merge_excerto(item: Dict[str, Any]) -> str:
    # junta ementa + texto quando útil
    ementa = (item.get(EMENTA_FIELD) or "").strip()
    texto  = (item.get(TEXT_FIELD) or "").strip()
    parts = [p for p in [ementa, texto] if p]
    return " ".join(parts).strip()

def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = _as_dict(raw)
    doc_id = item.get("doc_id") or item.get("document_id") or item.get("id") or item.get("_id") or "-"
    artigo = item.get(ART_FIELD) or item.get("artigo") or item.get("section") or "-"
    titulo = item.get(TITULO_FIELD) or item.get("title") or item.get("document_title") or "-"
    excerto = (
        item.get("trecho")
        or item.get("excerto")
        or _merge_excerto(item)
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
    seen = set(); out: List[Dict[str, Any]] = []
    for it in items:
        key = (it.get("doc_id"), it.get("artigo_numero"), (it.get("titulo") or "").strip())
        if key in seen: continue
        seen.add(key); out.append(it)
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
    return ("portaria" in ql and _extract_number(ql) is not None) or bool(re.search(r"\bportaria[_\s-]*cg[_\s-]*\d+\b", ql))

# ------------------ consultas ------------------
def _keyword_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    """BM25/keyword puro sobre os campos Keyword do schema."""
    if not _QUERY_IMPORTED: return []
    q_norm, q_ascii = _norm_spaces(q), _ascii(_norm_spaces(q))
    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,  # para montar excerto
            text_score = fn.bm25_score(),  # sobre default keyword fields da coleção
        )
        qb = col.query(sel)
        qb = qb.filter( match(q_norm) | match(q_ascii) )
        qb = qb.topk(field("text_score"), k)
        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except Exception as e:
        _dbg(f"keyword_query falhou: {e}")
        return []

def _hybrid_query(col, q: str, k: int) -> List[Dict[str, Any]]:
    """
    Híbrido 80/20:
      score = 0.8 * (0.7*sim(texto) + 0.15*sim(ementa) + 0.15*sim(titulo)) + 0.2 * bm25
    - Filtro lexical removido (evita exclusão de documentos relevantes em PT-BR)
    - Boost leve por número de portaria (se presente) via score lexical
    """
    if not _QUERY_IMPORTED: return []
    q_norm  = _norm_spaces(q); q_ascii = _ascii(q_norm); num = _extract_number(q_norm)
    try:
        sel = select(
            "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
            TEXT_FIELD, EMENTA_FIELD,
            sim_texto   = fn.semantic_similarity(TEXT_FIELD,   q_norm),
            sim_ementa  = fn.semantic_similarity(EMENTA_FIELD, q_norm),
            sim_titulo  = fn.semantic_similarity(TITULO_FIELD, q_norm),
            text_score  = fn.bm25_score(),  # BM25 nos campos Keyword da coleção
        )
        qb = col.query(sel)

        # 🚫 Filtro lexical removido conforme recomendação

        sem_mix = (
            W_TEXT*field("sim_texto") +
            W_EMENTA*field("sim_ementa") +
            W_TITULO*field("sim_titulo")
        )
        base_score = SEM_WEIGHT * sem_mix + LEX_WEIGHT * field("text_score")

        if num:
            bonus = 0.05 * field("text_score")
            qb = qb.topk(base_score + bonus, k)
        else:
            qb = qb.topk(base_score, k)

        try: qb = qb.rerank()
        except Exception: pass

        rows = qb
        return [_normalize_item(r) for r in rows] if isinstance(rows, list) else []
    except Exception as e:
        _dbg(f"hibrida 80/20 falhou: {e}")
        return []

# ------------------ API pública ------------------
def search_topk(query: str, k: int = 5) -> List[Dict[str, Any]]:
    if not query:
        return []
    if _collection is None:
        _init()
        if _collection is None:
            return []

    results: List[Dict[str, Any]] = []

    if _is_id_like(query):
        results = _keyword_query(_collection, query, k)  # type: ignore

    if not results:
        results = _hybrid_query(_collection, query, k)  # type: ignore

    if not results and _QUERY_IMPORTED:
        try:
            rows = _collection.query(
                select(
                    "doc_id", TITULO_FIELD, ART_FIELD, PORTARIA_FIELD, ANO_FIELD,
                    TEXT_FIELD, EMENTA_FIELD,
                    sim = fn.semantic_similarity(TEXT_FIELD, _norm_spaces(query)),
                ).topk(field("sim"), k)
            )
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
        "initialized": _collection is not None and _init_error is None,
        "init_error": _init_error,
        "weights": {
            "hybrid_sem": SEM_WEIGHT, "hybrid_lex": LEX_WEIGHT,
            "w_text": W_TEXT, "w_ementa": W_EMENTA, "w_titulo": W_TITULO,
        },
        "keyword_fields": KEYWORD_FIELDS,
        "semantic_fields": [TEXT_FIELD, EMENTA_FIELD, TITULO_FIELD],
        "portaria_field": PORTARIA_FIELD,
    }

__all__ = ["search_topk", "buscar_topk", "topk_status"]
