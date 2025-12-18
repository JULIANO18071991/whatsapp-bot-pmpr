# llm_client.py — versão MULTI-COLEÇÕES mantendo formato clássico de resposta
# -*- coding: utf-8 -*-
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple
from openai import OpenAI

# -------- OpenAI --------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definido.")

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
    linhas = []
    for i, t in enumerate(trechos, 1):

        doc_id   = _pick(t, "doc_id") or "-"
        artigo   = _pick(t, "artigo_numero", "item", "section") or "-"
        titulo   = _pick(t, "titulo") or "-"
        excerto  = _pick(t, "trecho", "texto", "ementa") or ""
        score    = _pick(t, "score")
        numero   = _pick(t, "numero_portaria", "num")
        ano      = _pick(t, "ano")

        ref = _fmt_portaria(numero, ano)

        meta_score = f" (score {float(score):.3f})" if isinstance(score, (float, int)) else ""

        header = f"[{i}] ref={ref} | artigo/item={artigo} | título={titulo} | doc_id={doc_id}{meta_score}"
        linhas.append(header + "\n→ " + _as_str(excerto).strip())

    bloco = "\n\n".join(linhas)
    return bloco if len(bloco) <= max_chars else bloco[:max_chars] + "\n…(trechos truncados)…"

def _extract_meta(trechos: List[Dict[str, Any]]) -> List[str]:
    """
    Extrai lista de portarias/diretrizes/etc.
    """
    refs = []
    for t in trechos:
        num = _as_str(_pick(t, "numero_portaria", "num")).strip()
        ano = _as_str(_pick(t, "ano")).strip()
        ref = _fmt_portaria(num, ano)
        if ref != "-":
            refs.append(ref)

    # ordena por frequência
    freq = Counter(refs)
    return [r for r, _ in freq.most_common()]


def _build_messages(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> List[Dict[str, str]]:
    msgs = []

    system_rules = (
        "Você é um assistente jurídico especializado nas normas da PMPR.\n"
        "IMPORTANTE: sua resposta deve seguir exatamente este modelo lógico:\n\n"
        "1) Introdução explicando se há base normativa:\n"
        "   'De acordo com os trechos recuperados, existe previsão normativa sobre...'\n\n"
        "2) Lista de tópicos (bullet points), cada um citando:\n"
        "   • Tipo de documento (Portaria, Diretriz, POP, etc)\n"
        "   • Número/ano (quando houver)\n"
        "   • Artigo ou item\n"
        "   • Explicação clara do que ele determina\n\n"
        "3) Conclusão consolidada:\n"
        "   'Portanto, com base nos documentos X e Y, conclui-se que...'\n\n"
        "4) Resumo final de 1 linha:\n"
        "   'Resumo: ...'\n\n"
        "NÃO liste por coleção. A resposta deve ser unificada e fluída.\n"
        "NÃO invente normas. Baseie-se APENAS nos trechos fornecidos.\n"
        "Se faltar base para afirmar algo, informe explicitamente."
    )
    msgs.append({"role": "system", "content": system_rules})

    msgs += _coerce_mem(memoria)

    # Trechos combinados (não separados por coleção)
    bloco = _format_trechos(trechos)
    refs_ordenadas = _extract_meta(trechos)

    ref_info = "DOCUMENTOS IDENTIFICADOS: " + ", ".join(refs_ordenadas) if refs_ordenadas else "Nenhuma referência normativa identificada."

    msgs.append({"role": "system", "content": "TRECHOS RECUPERADOS:\n" + bloco})
    msgs.append({"role": "system", "content": ref_info})

    msgs.append({"role": "user", "content": pergunta})

    return msgs

# -------- API pública --------
def gerar_resposta(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> str:
    try:
        messages = _build_messages(pergunta, trechos, memoria)

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )

        return (resp.choices[0].message.content or "").strip() or "Não consegui gerar resposta."
    except Exception as e:
        print(f"[ERRO gerar_resposta] {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
