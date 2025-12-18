# llm_client.py — versão MULTI-COLEÇÕES revisada e corrigida
# -*- coding: utf-8 -*-
import os
from collections import Counter
from typing import Any, Dict, List
from openai import OpenAI

# ---------------- OpenAI ----------------
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


# ---------------- Helpers ----------------
def _as_str(x: Any) -> str:
    return "" if x is None else str(x)


def _coerce_mem(mem: Any) -> List[Dict[str, str]]:
    if isinstance(mem, list) and mem and isinstance(mem[0], dict) and "role" in mem[0]:
        return mem
    if isinstance(mem, str) and mem.strip():
        return [{"role": "user", "content": mem.strip()}]
    return []


def _pick(v: Dict[str, Any], *keys: str) -> Any:
    """Busca campos no dict e no _raw."""
    for k in keys:
        if k in v and v[k]:
            return v[k]
    raw = v.get("_raw") or {}
    for k in keys:
        if k in raw and raw[k]:
            return raw[k]
    return None


def _fmt_ref(tipo: str, numero: Any, ano: Any, doc_id: Any) -> str:
    """
    Formata referência conforme o tipo de documento.
    """
    numero_s = _as_str(numero).strip()
    ano_s = _as_str(ano).strip()
    doc_id_s = _as_str(doc_id).strip()

    if tipo.lower() in ["portaria", "diretriz", "decreto", "lei", "resolução"]:
        if numero_s and ano_s:
            return f"{numero_s}/{ano_s}"
        return numero_s or ano_s or doc_id_s or "-"

    # POP, PAP, Manual, Memorando, Orientações, etc.
    return numero_s or doc_id_s or "-"


def _format_trechos(trechos: List[Dict[str, Any]], max_chars: int = 6500) -> str:
    """
    Formata os trechos enviados ao LLM com:
    • tipo de documento
    • referência
    • artigo/item
    • título
    • excerto
    """
    linhas = []

    for i, t in enumerate(trechos, 1):
        tipo     = _pick(t, "tipo_documento") or "-"
        artigo   = _pick(t, "artigo_numero", "item", "section") or "-"
        titulo   = _pick(t, "titulo") or "-"
        excerto  = _pick(t, "trecho", "texto", "ementa") or ""
        score    = _pick(t, "score")

        numero   = _pick(t, "numero_portaria", "numero", "num")
        ano      = _pick(t, "ano", "data")
        doc_id   = _pick(t, "doc_id", "id")

        ref = _fmt_ref(tipo, numero, ano, doc_id)

        score_txt = ""
        if isinstance(score, (float, int)):
            score_txt = f" (score {float(score):.3f})"

        header = (
            f"[{i}] tipo={tipo} | ref={ref} | artigo/item={artigo} | "
            f"título={titulo} | doc_id={doc_id}{score_txt}"
        )

        linhas.append(header + "\n→ " + _as_str(excerto).strip())

    bloco = "\n\n".join(linhas)

    if len(bloco) <= max_chars:
        return bloco
    return bloco[:max_chars] + "\n…(trechos truncados)…"


def _extract_meta(trechos: List[Dict[str, Any]]) -> List[str]:
    """
    Identifica documentos citados — referência normativa.
    """
    refs = []
    for t in trechos:
        tipo   = _pick(t, "tipo_documento") or "-"
        numero = _pick(t, "numero_portaria", "numero", "num")
        ano    = _pick(t, "ano", "data")
        doc_id = _pick(t, "doc_id")

        ref = f"{tipo}: " + _fmt_ref(tipo, numero, ano, doc_id)
        refs.append(ref)

    freq = Counter(refs)
    return [r for r, _ in freq.most_common()]


# ---------------- SYSTEM MESSAGE ----------------
SYSTEM_RULES = (
    "Você é um assistente jurídico especializado nas normas da PMPR.\n"
    "**NÃO invente artigos, itens, normas ou números de documentos.**\n"
    "A resposta deve usar EXCLUSIVAMENTE os trechos fornecidos.\n"
    "Se não houver base normativa suficiente, diga claramente.\n\n"
    "FORMATO OBRIGATÓRIO DA RESPOSTA:\n"
    "1) Introdução\n"
    "   - Explique se há ou não base normativa relacionada.\n\n"
    "2) Exposição estruturada\n"
    "   Para cada trecho relevante, apresente:\n"
    "   • Tipo de documento (Portaria, Diretriz, Decreto, POP, etc.)\n"
    "   • Número/ano ou referência\n"
    "   • Artigo/Item\n"
    "   • Explicação clara e fiel ao trecho\n\n"
    "3) Conclusão final consolidada\n"
    "   - Baseada SOMENTE nas normas fornecidas.\n\n"
    "4) Resumo final (1 linha)\n"
    "   'Resumo: ...'\n"
)


# ---------------- Build Messages ----------------
def _build_messages(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> List[Dict[str, str]]:
    msgs = [{"role": "system", "content": SYSTEM_RULES}]

    msgs += _coerce_mem(memoria)

    bloco = _format_trechos(trechos)
    refs  = _extract_meta(trechos)
    ref_info = "DOCUMENTOS RECUPERADOS: " + ", ".join(refs) if refs else "Nenhum documento identificado."

    msgs.append({"role": "system", "content": "TRECHOS RECUPERADOS:\n" + bloco})
    msgs.append({"role": "system", "content": ref_info})

    msgs.append({"role": "user", "content": pergunta})
    return msgs


# ---------------- Public API ----------------
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
