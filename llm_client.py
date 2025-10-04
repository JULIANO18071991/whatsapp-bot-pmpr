# llm_client.py — Versão para depurar os resultados do TOPK

import os
import json
from typing import List, Dict, Any
from openai import OpenAI

# --- Configuração do Cliente OpenAI (sem alterações) ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1024")) # Aumentei um pouco para garantir que a resposta caiba

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None,
)

# --- Estrutura do Prompt (MODIFICADA) ---
# MODIFICAÇÃO 1: O prompt do sistema agora é mais direto sobre resumir.
SYSTEM_PROMPT = (
    "Você é um assistente que resume documentos. Sua função é extrair e apresentar as informações "
    "encontradas nos trechos de contexto fornecidos, relacionando-as com a pergunta do usuário. "
    "Apresente os achados de forma clara."
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
            
    # MODIFICAÇÃO 2: A tarefa agora é resumir, não responder.
    msgs.append({
        "role": "user",
        "content": (
            f"Analise os trechos de contexto fornecidos e resuma as informações que eles contêm sobre a pergunta do usuário.\n\n"
            f"## Pergunta do Usuário:\n{pergunta}\n\n"
            f"## Contexto para Análise (Fontes):\n{context_block}\n\n"
            f"## Sua Tarefa:\n"
            "1. Para cada trecho fornecido no contexto, extraia e resuma a informação que ele contém.\n"
            "2. Apresente os resumos de forma organizada, indicando a fonte de cada um com seu número (ex: [1], [2]).\n"

            "3. **Importante:** Não filtre ou omita nenhum trecho. Sua tarefa é mostrar o que foi encontrado, mesmo que não pareça diretamente relacionado à pergunta."
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

