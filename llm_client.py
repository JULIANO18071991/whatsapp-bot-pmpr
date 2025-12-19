# llm_client.py
# -*- coding: utf-8 -*-

import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple
from openai import OpenAI

# -------- OpenAI --------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = None
if OPENAI_API_KEY:
    client = OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1536"))

# -------- helpers --------
def _as_str(x: Any) -> str:
    return "" if x is None else str(x)

def _coerce_mem(mem: Any) -> List[Dict[str, str]]:
    if isinstance(mem, list) and mem and isinstance(mem[0], dict) and "role" in mem[0]:
        return mem
    if isinstance(mem, str) and mem.strip():
        return [{"role": "user", "content": mem.strip()}]
    return []

def _pick(v: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in v and v[k]:
            return v[k]
    raw = v.get("_raw") or {}
    for k in keys:
        if k in raw and raw[k]:
            return raw[k]
    return None

def _fmt_portaria(num: Any, ano: Any) -> str:
    num_s = _as_str(num).strip()
    ano_s = _as_str(ano).strip()
    if num_s and ano_s:
        return f"{num_s}/{ano_s}"
    return num_s or ano_s or "-"

def _format_trechos(trechos: List[Dict[str, Any]], max_chars: int = 6500) -> str:
    linhas: List[str] = []
    for i, t in enumerate(trechos, 1):
        doc_id  = _pick(t, "doc_id", "id", "_id") or "-"
        artigo  = _pick(t, "artigo_numero", "artigo", "section") or "-"
        titulo  = _pick(t, "titulo", "title", "document_title") or "-"
        excerto = _pick(t, "trecho", "texto", "caput", "ementa") or ""
        score   = _pick(t, "score", "_score", "similarity", "text_score", "sim")
        numero  = _pick(t, "numero_portaria", "num")
        ano     = _pick(t, "ano")

        meta_score = f" (score {float(score):.3f})" if isinstance(score, (int, float)) else ""
        pstr = _fmt_portaria(numero, ano)

        header = f"[{i}] portaria={pstr} | artigo={artigo} | título={titulo} | doc_id={doc_id}{meta_score}"
        linhas.append(header + "\n→ " + _as_str(excerto).strip())

    bloco = "\n\n".join(linhas)
    return bloco if len(bloco) <= max_chars else bloco[:max_chars] + "\n…(trechos truncados)…"

def _extract_meta(trechos: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], str]:
    por_map: Dict[str, set] = defaultdict(set)
    contagem: Counter = Counter()

    for t in trechos:
        num = _as_str(_pick(t, "numero_portaria", "num")).strip()
        ano = _as_str(_pick(t, "ano")).strip()
        artigo = _as_str(_pick(t, "artigo_numero", "artigo", "section")).strip()
        chave = _fmt_portaria(num, ano)
        if chave != "-":
            por_map[chave].add(artigo or "-")
            contagem[chave] += 1

    majoritaria = contagem.most_common(1)[0][0] if contagem else ""
    por_map_ord = {k: sorted(list(v), key=lambda x: (x == "-", x)) for k, v in por_map.items()}
    return por_map_ord, majoritaria

def _build_messages(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []

    system_rules = (
        "Você é um assistente jurídico da PMPR.\n"
        "Baseie-se APENAS nos trechos fornecidos.\n"
        "Cite Portaria e artigo sempre que possível.\n"
        "Finalize com um resumo curto."
    )

    msgs.append({"role": "system", "content": system_rules})
    msgs += _coerce_mem(memoria)

    bloco = _format_trechos(trechos)
    mapa, major = _extract_meta(trechos)

    msgs.append({"role": "system", "content": "TRECHOS RECUPERADOS:\n" + bloco})
    msgs.append({"role": "user", "content": pergunta.strip()})

    return msgs

# -------- API pública --------
def gerar_resposta(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> str:
    if not client:
        return "Erro interno: serviço de IA indisponível no momento."

    try:
        messages = _build_messages(pergunta, trechos, memoria)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[ERRO gerar_resposta] {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
