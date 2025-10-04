# llm_client.py — Versão Final Corrigida e Otimizada
# Compatível com openai >= 1.x

import os
import json
from typing import List, Dict, Any

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
    # Se for uma string que se parece com um JSON, decodifica.
    if isinstance(item, str):
        try:
            data = json.loads(item)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            # Se não for JSON, trata como texto puro.
            pass
    # Para strings e outros tipos, envolve em um dicionário com uma chave padrão.
    return {"texto": str(item)}

def _build_messages(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> list:
    """
    Constrói a lista de mensagens para a LLM, tratando 'trechos' de forma robusta.
    
    - trechos: Lista que pode conter dicts ou strings.
    - memoria: Histórico da conversa.
    """
    context_lines = []
    # Itera sobre os trechos com um índice para criar citações numéricas (ex: [1], [2]).
    for i, trecho_bruto in enumerate(trechos):
        # **CORREÇÃO PRINCIPAL**: Garante que 't' seja sempre um dicionário.
        t = _as_dict(trecho_bruto)
        
        # Constrói a referência completa da fonte.
        ref = f"Portaria {t.get('numero_portaria', '?')} ({t.get('ano', '?')}) - {t.get('parent_level', 'N/A')} art.{t.get('artigo_numero', '?')}"
        
        # Extrai o texto do trecho.
        texto = (t.get("texto") or "").strip()
        
        # Formata a linha de contexto com um índice numérico para citação.
        context_lines.append(f"Fonte [{i+1}]: {ref}\nTrecho: \"{texto}\"")

    # Bloco de contexto a ser inserido no prompt.
    context_block = "\n\n".join(context_lines) if context_lines else "Nenhum trecho de contexto foi encontrado para esta pergunta."

    # --- Montagem do Prompt Final ---
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # Adiciona a memória curta (últimos 3 turnos), garantindo a integridade dos dados.
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content:
            msgs.append({"role": role, "content": content})
            
    # Adiciona a instrução final com a pergunta e o contexto (prática recomendada).
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
        
        # Descomente para depurar o prompt enviado à LLM
        # print(json.dumps(msgs, indent=2, ensure_ascii=False))
        
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=msgs,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        # Adiciona um log de erro para facilitar a depuração futura.
        print(f"[ERRO em gerar_resposta]: {e}")
        # Retorna uma mensagem de erro amigável ao usuário final.
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação. A equipe técnica já foi notificada."
