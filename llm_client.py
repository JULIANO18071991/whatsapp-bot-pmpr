# llm_client.py — compatível com openai >= 1.x

import os
from typing import List, Dict

# Se você usa um gateway (ex.: Azure/OpenAI Proxy), permita BASE_URL opcional
from openai import OpenAI

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # opcional
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "700"))

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None,
)

SYSTEM_PROMPT = (
    "Você é um assistente da PMPR. Responda com precisão, cite as portarias quando útil. "
    "Se não houver base nos trechos fornecidos, diga que não encontrou."
)

def _build_messages(pergunta: str, trechos: List[Dict], memoria: List[Dict]) -> list:
    """
    trechos: lista de dicts {'doc_id','numero_portaria','ano','parent_level','artigo_numero','texto','arquivo'}
    memoria: lista de turnos anteriores [{'role':'user'|'assistant','content': '...'}] (até 3 últimos)
    """
    context_lines = []
    for t in trechos:
        ref = f"Portaria {t.get('numero_portaria','')} ({t.get('ano','')}) - {t.get('parent_level','')} art.{t.get('artigo_numero','')}"
        exc = (t.get("texto") or "").strip()
        context_lines.append(f"[{ref}]\n{exc}")

    context_block = "\n\n".join(context_lines) if context_lines else "(sem trechos relevantes)"

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    # memória curta (se existirem)
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        msgs.append({"role": role, "content": m.get("content", "")})
    # instruções + pergunta
    msgs.append({
        "role": "user",
        "content": (
            "Pergunta do usuário:\n"
            f"{pergunta}\n\n"
            "Contexto (trechos encontrados):\n"
            f"{context_block}\n\n"
            "Tarefa: responda de forma objetiva e cite a(s) Portaria(s) quando adequado. "
            "Se não houver evidência nos trechos, diga explicitamente que não encontrou base."
        )
    })
    return msgs

def gerar_resposta(pergunta: str, trechos: List[Dict], memoria: List[Dict]) -> str:
    msgs = _build_messages(pergunta, trechos, memoria)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=msgs,
        temperature=OPENAI_TEMPERATURE,
        max_tokens=OPENAI_MAX_TOKENS,
    )
    return resp.choices[0].message.content.strip()
