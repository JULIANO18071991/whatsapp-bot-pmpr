# llm_client.py — Versão Final Otimizada

import os
import json
from typing import List, Dict, Any
from openai import OpenAI

# --- Configuração do Cliente OpenAI (sem alterações) ---
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1024"))

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL if OPENAI_BASE_URL else None,
)

# --- Estrutura do Prompt (FINAL) ---
SYSTEM_PROMPT = (
    "Você é um assistente especialista da PMPR. Suas respostas devem ser precisas, objetivas e "
    "baseadas exclusivamente nos trechos de portarias fornecidos no contexto. "
    "Cite suas fontes usando o número da portaria e o artigo, como (Portaria 641, art. 6)."
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
    Constrói a lista de mensagens para a LLM, com instruções otimizadas.
    """
    context_lines = []
    for i, trecho_bruto in enumerate(trechos):
        t = _as_dict(trecho_bruto)
        # Usaremos o número da portaria e o artigo para a citação, que é mais informativo.
        ref = f"Portaria {t.get('numero_portaria', '?')}, art. {t.get('artigo_numero', '?')}"
        texto = (t.get("texto") or "").strip()
        # O índice [Fonte X] ajuda a LLM a diferenciar os blocos.
        context_lines.append(f"[Fonte {i+1}] ({ref})\nTrecho: \"{texto}\"")

    context_block = "\n\n".join(context_lines) if context_lines else "Nenhum trecho de contexto foi encontrado para esta pergunta."

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    for m in memoria[-3:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = m.get("content", "")
        if content:
            msgs.append({"role": role, "content": content})
            
    # A tarefa agora é um processo de 2 passos: selecionar e depois sintetizar.
    msgs.append({
        "role": "user",
        "content": (
            f"Com base nos trechos de contexto abaixo, responda à pergunta do usuário.\n\n"
            f"## Pergunta do Usuário:\n{pergunta}\n\n"
            f"## Contexto para Análise (Fontes):\n{context_block}\n\n"
            f"## Sua Tarefa:\n"
            "1. **Analise todas as fontes.** Ignore as que não são diretamente relevantes para a pergunta do usuário (ex: licença maternidade, substituição de comando, etc., se a pergunta for sobre Licença Capacitação).\n"
            "2. **Sintetize uma resposta única e coesa** usando APENAS as informações das fontes relevantes que você selecionou.\n"
            "3. Organize a resposta em tópicos claros (ex: 'Como Solicitar', 'Condições', 'O que não é válido').\n"
            "4. Ao final de cada informação, cite a fonte no formato (Portaria XXX, art. Y).\n"
            "5. Se, após a análise, nenhuma fonte for relevante, responda: 'Não encontrei informações sobre \"{pergunta}\" nas portarias consultadas.'"
        )
    })
    return msgs

def gerar_resposta(pergunta: str, trechos: List[Any], memoria: List[Dict]) -> str:
    """
    Gera uma resposta da LLM.
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
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
