# llm_client.py — Versão Finalíssima com Fallback e Modelo Trocado

import os
import json
from typing import List, Dict, Any
from openai import OpenAI

# --- Configuração do Cliente OpenAI ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
# --- MUDANÇA 1: TROCANDO O MODELO ---
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini") 
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1536")) # Aumentando um pouco

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None,
)

# --- Estrutura do Prompt (Mantida) ---
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
    if not trechos:
        # Lógica de falha movida para cá para garantir a resposta
        return [{"role": "system", "content": "Assistente prestativo."},
                {"role": "user", "content": f"Diga ao usuário que você não encontrou informações sobre '{pergunta}' nas portarias consultadas."}]

    context_lines = []
    for i, trecho_bruto in enumerate(trechos):
        t = _as_dict(trecho_bruto)
        ref = f"Portaria {t.get('numero_portaria', '?')}, art. {t.get('artigo_numero', '?')}"
        texto = (t.get("texto") or "").strip()
        context_lines.append(f"[Fonte {i+1}] ({ref})\nTrecho: \"{texto}\"")
    context_block = "\n\n".join(context_lines)

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content: msgs.append({"role": role, "content": content})
            
    msgs.append({
        "role": "user",
        "content": (
            f"## Pergunta do Usuário:\n{pergunta}\n\n"
            f"## Contexto Encontrado (Fontes):\n{context_block}\n\n"
            f"## Sua Tarefa:\n"
            "1. Use as informações do contexto para construir uma resposta completa e bem organizada para a pergunta do usuário.\n"
            "2. Sintetize os trechos relevantes em um texto coeso. Não apenas liste os resumos, crie uma explicação clara.\n"
            "3. Organize a resposta em tópicos se isso ajudar na clareza (ex: 'Condições', 'Como Solicitar').\n"
            "4. É obrigatório citar a fonte de cada informação no formato (Portaria XXX, art. Y) ao final da frase ou do tópico."
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
        
        resposta_llm = resp.choices[0].message.content.strip()
        
        # --- MUDANÇA 2: VERIFICAÇÃO DE RESPOSTA VAZIA ---
        if not resposta_llm:
            print("[WARN] A OpenAI retornou uma resposta vazia. Enviando mensagem de fallback.")
            return "Não foi possível gerar uma resposta no momento. Por favor, tente reformular sua pergunta."
            
        return resposta_llm
        
    except Exception as e:
        print(f"[ERRO em gerar_resposta]: {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação. A equipe técnica já foi notificada."
