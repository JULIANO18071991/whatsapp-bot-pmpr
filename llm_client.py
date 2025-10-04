# llm_client.py
# -*- coding: utf-8 -*-
"""
Camada de LLM (OpenAI compatível).
Usa Chat Completions com mensagens e um system prompt seguro.
"""

import os
from typing import Any, Dict, List

from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definido.")

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # opcional (compatível com proxy/provider)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1536"))

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)

SYSTEM_PROMPT = """\
Você é um assistente que responde de forma objetiva, confiável e didática.
Você recebe:
1) A PERGUNTA do usuário
2) Uma lista de TRECHOS recuperados (TopK), cada um com metadados (ex.: doc_id, artigo, título)
3) Um pequeno HISTÓRICO da conversa

REGRAS:
- Baseie-se nos TRECHOS quando a pergunta for normativa, legal ou documental. Quando citar o conteúdo, resuma e explique.
- Se os trechos não forem suficientes, diga o que faltou ou sugira como o usuário pode detalhar melhor.
- Seja direto, em português do Brasil, e organize em tópicos quando ajudar.
- Em respostas longas, traga um resumo final de 1-2 linhas com o “essencial”.
- Nunca invente dados de documento. Se não souber, assuma a limitação.

FORMATO DOS TRECHOS (exemplo):
- doc_id: 20210728_portaria_cg641..., artigo: 9, excerto: "...texto..."
"""

def _compactar_trechos(trechos: List[Dict[str, Any]], max_chars: int = 3500) -> str:
    """
    Junta trechos em um bloco textual curto, respeitando limite aproximado.
    Espera itens com chaves como: doc_id, artigo_numero, titulo, excerto/trecho, score, url, etc.
    """
    if not trechos:
        return ""

    linhas: List[str] = []
    for i, t in enumerate(trechos, start=1):
        doc_id = t.get("doc_id") or t.get("docId") or t.get("document_id") or "-"
        artigo = t.get("artigo_numero") or t.get("artigo") or "-"
        titulo = t.get("titulo") or t.get("title") or "-"
        excerto = t.get("trecho") or t.get("excerto") or t.get("text") or t.get("chunk") or ""
        score = t.get("score")
        meta_score = f" (score: {score:.3f})" if isinstance(score, (int, float)) else ""
        linha = f"[{i}] doc_id={doc_id} | artigo={artigo} | título={titulo}{meta_score}\n→ {excerto}".strip()
        linhas.append(linha)

    bloco = "\n\n".join(linhas)
    if len(bloco) <= max_chars:
        return bloco
    # corta de forma simples
    return bloco[:max_chars] + "\n…(trechos truncados)…"


def _build_messages(pergunta: str, trechos: List[Dict[str, Any]], memoria: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Monta as mensagens para a API de chat.
    - memoria: lista de {"role": "user"/"assistant", "content": "..."}
    """
    msgs: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # histórico curto
    if memoria:
        # mantém no máximo ~6 mensagens (user/assistant alternando)
        cutoff = max(0, len(memoria) - 6)
        for m in memoria[cutoff:]:
            role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
            content = str(m.get("content", "")).strip()
            if content:
                msgs.append({"role": role, "content": content})

    # anexa trechos (contexto)
    bloco_trechos = _compactar_trechos(trechos)
    if bloco_trechos:
        msgs.append({
            "role": "system",
            "content": f"TRECHOS RECUPERADOS (para consulta, use apenas se forem relevantes):\n{bloco_trechos}"
        })

    # pergunta atual
    msgs.append({"role": "user", "content": pergunta})
    return msgs


def gerar_resposta(pergunta: str, trechos: List[Dict[str, Any]], memoria: List[Dict[str, str]]) -> str:
    """
    Gera resposta chamando o provedor (OpenAI compatível).
    Parâmetros na ordem CORRETA: pergunta, trechos, memoria.
    """
    try:
        messages = _build_messages(pergunta, trechos, memoria)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or "Não foi possível gerar uma resposta no momento."
    except Exception as e:
        # log mínimo — evite vazar segredos
        print(f"[ERRO gerar_resposta] {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
