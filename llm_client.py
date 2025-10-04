# llm_client.py
# -*- coding: utf-8 -*-
import os
from typing import Any, Dict, List
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definido.")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=os.getenv("OPENAI_BASE_URL") or None,
)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1536"))

SYSTEM_PROMPT = """\
Você é um assistente que responde de forma objetiva, confiável e didática.
Use os TRECHOS recuperados quando houver (normas/leis). Se não souber, diga o que falta.
Fale em português do Brasil. Em respostas longas, finalize com um resumo de 1–2 linhas.
"""

def _compactar_trechos(trechos: List[Dict[str, Any]], max_chars: int = 3500) -> str:
    if not trechos:
        return ""
    linhas: List[str] = []
    for i, t in enumerate(trechos, 1):
        doc_id = t.get("doc_id", "-")
        artigo = t.get("artigo_numero", "-")
        titulo = t.get("titulo", "-")
        excerto = t.get("trecho", "") or ""
        score = t.get("score")
        meta = f" (score {score:.3f})" if isinstance(score, (int, float)) else ""
        linhas.append(f"[{i}] doc_id={doc_id} | artigo={artigo} | título={titulo}{meta}\n→ {excerto}")
    bloco = "\n\n".join(linhas)
    return bloco if len(bloco) <= max_chars else bloco[:max_chars] + "\n…(trechos truncados)…"

def _coerce_mem(mem: Any) -> List[Dict[str, str]]:
    """Aceita lista de {role,content} OU string antiga da Memory e converte."""
    if isinstance(mem, list) and mem and isinstance(mem[0], dict) and "role" in mem[0]:
        return mem  # já no formato certo
    if isinstance(mem, str) and mem.strip():
        return [{"role": "user", "content": mem.strip()}]
    return []

def _build_messages(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # histórico
    for m in _coerce_mem(memoria)[-6:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        content = str(m.get("content", "")).strip()
        if content:
            msgs.append({"role": role, "content": content})
    # trechos
    bloco = _compactar_trechos(trechos)
    if bloco:
        msgs.append({"role": "system", "content": "TRECHOS RECUPERADOS:\n" + bloco})
    # pergunta
    msgs.append({"role": "user", "content": pergunta})
    return msgs

def gerar_resposta(pergunta: str, trechos: List[Dict[str, Any]], memoria: Any) -> str:
    try:
        messages = _build_messages(pergunta, trechos, memoria)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
        )
        return (resp.choices[0].message.content or "").strip() or "Não consegui responder agora."
    except Exception as e:
        print(f"[ERRO gerar_resposta] {e}")
        return "Desculpe, ocorreu um erro interno ao processar sua solicitação."
