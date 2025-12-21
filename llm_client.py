# llm_client.py
# -*- coding: utf-8 -*-
import os
from collections import defaultdict
from typing import Any, Dict, List
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

def _fmt_documento(num: Any, ano: Any) -> str:
    num_s = _as_str(num).strip()
    ano_s = _as_str(ano).strip()
    if num_s and ano_s:
        return f"{num_s}/{ano_s}"
    return num_s or ano_s or "-"

# ==========================================================
# FORMATAÇÃO POR COLEÇÃO
# ==========================================================
def _format_trechos_por_colecao(
    trechos: List[Dict[str, Any]],
    max_chars: int = 6500
) -> str:
    """
    Agrupa trechos por coleção (Decreto, Diretriz, etc.)
    e monta um bloco estruturado para a LLM.
    """
    grupos: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trechos:
        colecao = t.get("_colecao") or t.get("fonte_colecao") or "OUTROS"
        grupos[colecao.upper()].append(t)

    blocos: List[str] = []

    for colecao, itens in grupos.items():
        linhas: List[str] = [f"[{colecao}]"]
        for i, t in enumerate(itens, 1):
            titulo  = _pick(t, "titulo", "title") or "-"
            artigo  = _pick(t, "artigo_numero", "artigo", "section") or "-"
            numero  = _pick(t, "numero_portaria", "numero", "num")
            ano     = _pick(t, "ano", "data")
            trecho  = _pick(t, "trecho", "texto", "ementa") or ""

            doc_str = _fmt_documento(numero, ano)

            linhas.append(
                f"{i}. Documento nº {doc_str} | Art./Item: {artigo} | Título: {titulo}\n"
                f"→ {_as_str(trecho).strip()}"
            )

        blocos.append("\n".join(linhas))

    texto = "\n\n".join(blocos)
    return texto if len(texto) <= max_chars else texto[:max_chars] + "\n…(trechos truncados)…"

# ==========================================================
# MENSAGENS PARA O LLM
# ==========================================================
def _build_messages(
    pergunta: str,
    trechos: List[Dict[str, Any]],
    memoria: Any
) -> List[Dict[str, str]]:

    msgs: List[Dict[str, str]] = []

    system_rules = (
        "Você é um assistente jurídico da Polícia Militar do Paraná (PMPR).\n"
        "Responda de forma objetiva, técnica e fundamentada.\n"
        "Utilize EXCLUSIVAMENTE os trechos fornecidos.\n\n"
        "INSTRUÇÕES IMPORTANTES:\n"
        "- Organize a resposta POR TIPO DE DOCUMENTO (ex: Decreto, Diretriz, Resolução).\n"
        "- Cite explicitamente o número do documento e o artigo/item correspondente.\n"
        "- Não mencione coleções que não possuam trechos relevantes.\n"
        "- Se um documento tratar parcialmente do tema, indique isso.\n"
        "- Se não houver base suficiente, diga claramente.\n\n"
        "Formato esperado:\n"
        "• O Decreto nº X estabelece que...\n"
        "• A Diretriz nº Y dispõe que...\n"
        "• A Resolução nº Z prevê que...\n"
    )
    msgs.append({"role": "system", "content": system_rules})

    # memória
    msgs += _coerce_mem(memoria)

    # contexto estruturado
    contexto_docs = _format_trechos_por_colecao(trechos)
    msgs.append({
        "role": "system",
        "content": "DOCUMENTOS RELEVANTES ENCONTRADOS:\n" + contexto_docs
    })

    # pergunta do usuário
    msgs.append({"role": "user", "content": pergunta.strip()})

    return msgs

# ==========================================================
# API PÚBLICA
# ==========================================================
def gerar_resposta(
    pergunta: str,
    trechos: List[Dict[str, Any]],
    memoria: Any
) -> str:
    try:
        messages = _build_messages(pergunta, trechos, memoria)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return (resp.choices[0].message.content or "").strip() or \
            "Não foi possível gerar uma resposta com base nos documentos disponíveis."
    except Exception as e:
        print(f"[ERRO gerar_resposta] {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
