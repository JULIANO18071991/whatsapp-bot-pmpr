# llm_client.py — Versão Final Corrigida e Otimizada
# Compatível com openai >= 1.x

import os
import json
from typing import List, Dict, Any
from openai import OpenAI  # <--- CORREÇÃO: Adicionar esta linha

# --- Configuração do Cliente OpenAI ---
# Carrega configurações de variáveis de ambiente para flexibilidade.
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")  # Opcional, para gateways/proxies
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "700"))

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None,
)

# --- Estrutura do Prompt ---
SYSTEM_PROMPT = (
    "Você é um assistente especialista da PMPR. Responda à pergunta do usuário com base "
    "EXCLUSIVAMENTE no contexto fornecido. Suas respostas devem ser precisas e objetivas. "
    "Cite suas fontes usando colchetes numéricos, como [1], [2], etc. "
    "Se a resposta não estiver no contexto, diga que não encontrou a informação."
)

def _as_dict(item: Any) -> Dict:
    """Garante que um item seja um dicionário para evitar AttributeError."""
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        try:
            data = json.loads(item)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"texto": str(item)}

def _build_messages(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> list:
    """
    Constrói a lista de mensagens para a LLM, tratando 'trechos' de forma robusta.
    """
    context_lines = []
    for i, trecho_bruto in enumerate(trechos):
        t = _as_dict(trecho_bruto)
        ref = f"Portaria {t.get('numero_portaria', '?')} ({t.get('ano', '?')}) - {t.get('parent_level', 'N/A')} art.{t.get('artigo_numero', '?')}"
        texto = (t.get("texto") or "").strip()
        context_lines.append(f"Fonte [{i+1}]: {ref}\nTrecho: \"{texto}\"")

    context_block = "\n\n".join(context_lines) if context_lines else "Nenhum trecho de contexto foi encontrado para esta pergunta."

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content:
            msgs.append({"role": role, "content": content})
            
    msgs.append({
        "role": "user",
        "content": (
            f"Com base no contexto fornecido abaixo, responda à seguinte pergunta do usuário.\n\n"
            f"## Pergunta do Usuário:\n{pergunta}\n\n"
            f"## Contexto para Resposta (Fontes):\n{context_block}\n\n"
            f"## Sua Tarefa:\n"
            "1. Responda à pergunta de forma objetiva usando apenas as informações do contexto.\n"
            "2. Ao usar informações de uma fonte, cite seu número entre colchetes (ex: [1], [2]).\n"
            "3. Se a resposta não estiver no contexto, informe explicitamente que não encontrou base para responder."
        )
    })
    return msgs

def gerar_resposta(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> str:
    """
    Gera uma resposta da LLM, garantindo que o processo seja robusto contra erros de tipo.
    """
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
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação. A equipe técnica já foi notificada."

