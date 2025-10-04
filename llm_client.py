# llm_client.py — Versão Simplificada para Respostas Diretas

import os
import json
from typing import List, Dict, Any
from openai import OpenAI

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1024"))

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None)

SYSTEM_PROMPT = (
    "Você é um assistente especialista da PMPR. Responda de forma clara e organizada, "
    "usando apenas os trechos de portarias fornecidos no contexto. "
    "Ao final de cada informação, cite a fonte, como (Portaria 641, art. 6)."
)

def _as_dict(item: Any) -> Dict:
    if isinstance(item, dict): return item
    if isinstance(item, str):
        try:
            data = json.loads(item)
            if isinstance(data, dict): return data
        except json.JSONDecodeError: pass
    return {"texto": str(item)}

def _build_messages(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> list:
    context_lines = []
    for i, trecho_bruto in enumerate(trechos):
        t = _as_dict(trecho_bruto)
        ref = f"Portaria {t.get('numero_portaria', '?')}, art. {t.get('artigo_numero', '?')}"
        texto = (t.get("texto") or "").strip()
        context_lines.append(f"[Fonte {i+1}] ({ref})\nTrecho: \"{texto}\"")

    context_block = "\n\n".join(context_lines) if context_lines else "Nenhum trecho de contexto foi encontrado."

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content: msgs.append({"role": role, "content": content})
            
    # MUDANÇA: Prompt muito mais simples e direto.
    msgs.append({
        "role": "user",
        "content": (
            f"Pergunta: {pergunta}\n\n"
            f"Contexto:\n{context_block}\n\n"
            f"Tarefa: Com base no contexto, responda à pergunta de forma completa e organizada. "
            "Se o contexto não contiver a resposta, diga 'Não encontrei informações sobre \"{pergunta}\" nas portarias consultadas.'"
        )
    })
    return msgs

def gerar_resposta(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> str:
    try:
        msgs = _build_messages(pergunta, trechos, memoria)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=msgs,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERRO em gerar_resposta]: {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
