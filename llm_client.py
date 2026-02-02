# llm_client.py
# -*- coding: utf-8 -*-

import os
from typing import Any, Dict, List
from openai import OpenAI

# =========================
# OPENAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definido.")

client = OpenAI(api_key=OPENAI_API_KEY)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1536"))

# =========================
# ORDENADOR HIERÁRQUICO
# =========================
ORDEM_DOCUMENTOS = [
    "Diretriz",
    "Resolucao",
    "Portaria",
    "POP",
    "PAP",
    "Orientacoes",
    "Nota_de_Instrucao",
    "Memorando",
]

# =========================
# FORMATADORES
# =========================
def _fmt_doc(t: Dict[str, Any]) -> str:
    numero = t.get("numero_portaria") or "s/n"
    ano = t.get("ano") or ""
    artigo = t.get("artigo_numero") or "-"
    trecho = (t.get("trecho") or "").strip()

    # 🔹 Tipo do documento vem da coleção
    tipo = t.get("fonte_colecao") or "Documento"

    # 🔹 Identificação institucional limpa
    if numero != "s/n" and ano:
        identificacao = f"{tipo} nº {numero}/{ano}"
    elif numero != "s/n":
        identificacao = f"{tipo} nº {numero}"
    else:
        identificacao = tipo

    return (
        f"• {identificacao}\n"
        f"  Art./Item: {artigo}\n"
        f"  → {trecho}"
    )

def _montar_bloco_documentos(resultados: Dict[str, List[Dict[str, Any]]]) -> str:
    blocos = []

    for colecao in ORDEM_DOCUMENTOS:
        docs = resultados.get(colecao)
        if not docs:
            continue

        linhas = [f"[{colecao.upper()}]"]
        for d in docs:
            linhas.append(_fmt_doc(d))

        blocos.append("\n".join(linhas))

    return "\n\n".join(blocos)

# =========================
# BUILD MESSAGES
# =========================
def _build_messages(pergunta: str, resultados: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    documentos = _montar_bloco_documentos(resultados)

    system_prompt = (
        "Você é um assistente jurídico da PMPR que responde de forma objetiva, confiável e didática.\n"
        "Sempre baseie sua resposta APENAS nos TRECHOS RECUPERADOS. Se faltar base, diga exatamente o que falta.\n"
        "Quando a pergunta envolver normas, CITE explicitamente o Documento e o Artigo usados.\n"
         "• Se o número do Documento não aparecer no texto do trecho, use os METADADOS fornecidos (portaria/ano/artigo).\n"
        "  predominante(s) nos trechos e, se possível, indique os artigos onde o tema aparece.\n"
        "Formato de citação sugerido: 'Fonte: Nome do Documento nº Numero do documento/Ano — art. '.\n"
        "Responda em português do Brasil; em respostas longas, finalize com um resumo de 1–2 linhas."
        f"{documentos}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": pergunta.strip()},
    ]

# =========================
# API PÚBLICA
# =========================
def gerar_resposta(pergunta: str, resultados: Dict[str, List[Dict[str, Any]]]) -> str:
    try:
        messages = _build_messages(pergunta, resultados)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERRO gerar_resposta] {e}")
        return "Erro ao gerar resposta."
