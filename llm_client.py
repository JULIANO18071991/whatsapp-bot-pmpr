# llm_client.py — Versão Final com Instrução para Construir a Resposta

import os
import json
from typing import List, Dict, Any
from openai import OpenAI

# --- Configuração do Cliente OpenAI ---
# Usando gpt-4o-mini, que é um bom ponto de partida. Se falhar, podemos trocar.
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1024"))

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None,
)

# --- Estrutura do Prompt ---
SYSTEM_PROMPT = (
    "Você é um assistente especialista da PMPR. Responda de forma clara e organizada, "
    "usando apenas os trechos de portarias fornecidos no contexto. "
    "Ao final de cada informação, cite a fonte, como (Portaria 641, art. 6)."
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
    Constrói a lista de mensagens para a LLM com a tarefa de construir uma resposta.
    """
    context_lines = []
    for i, trecho_bruto in enumerate(trechos):
        t = _as_dict(trecho_bruto)
        ref = f"Portaria {t.get('numero_portaria', '?')}, art. {t.get('artigo_numero', '?')}"
        texto = (t.get("texto") or "").strip()
        context_lines.append(f"[Fonte {i+1}] ({ref})\nTrecho: \"{texto}\"")

    # Se não houver trechos, a resposta de falha é garantida.
    if not context_lines:
        context_block = "Nenhum trecho de contexto foi encontrado."
    else:
        context_block = "\n\n".join(context_lines)

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content:
            msgs.append({"role": role, "content": content})
            
    # --- PROMPT AJUSTADO PARA CONSTRUIR UMA RESPOSTA ---
    msgs.append({
        "role": "user",
        "content": (
            f"## Pergunta do Usuário:\n{pergunta}\n\n"
            f"## Contexto Encontrado (Fontes):\n{context_block}\n\n"
            f"## Sua Tarefa:\n"
            "1. **Use as informações do contexto para construir uma resposta completa e bem organizada** para a pergunta do usuário.\n"
            "2. **Sintetize os trechos relevantes** em um texto coeso. Não apenas liste os resumos, crie uma explicação clara.\n"
            "3. Organize a resposta em tópicos se isso ajudar na clareza (ex: 'Condições', 'Como Solicitar').\n"
            "4. **É obrigatório citar a fonte** de cada informação no formato (Portaria XXX, art. Y) ao final da frase ou do tópico.\n"
            "5. Se o contexto for 'Nenhum trecho de contexto foi encontrado', responda exatamente: 'Não encontrei informações sobre \"{pergunta}\" nas portarias consultadas.'"
        )
    })
    return msgs

def gerar_resposta(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> str:
    """
    Gera uma resposta da LLM, garantindo que o processo seja robusto contra erros de tipo.
    """
    try:
        msgs = _build_messages(pergunta, trechos, memoria)
        
        # Descomente a linha abaixo se precisar depurar o prompt exato enviado à OpenAI
        # print(json.dumps(msgs, indent=2, ensure_ascii=False))
        
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

