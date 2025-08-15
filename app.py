import os
import json
import logging
import threading
import re

from flask import Flask, request, Response
import requests
from openai import OpenAI

app = Flask(__name__)

# ---------------------------
# Logs
# ---------------------------
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = app.logger

# ---------------------------
# Env vars
# ---------------------------
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN   = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID")

OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "350"))  # limite curto de saída

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------------------
# Graph API (WhatsApp)
# ---------------------------
GRAPH_VERSION = "v21.0"
GRAPH_BASE    = f"https://graph.facebook.com/{GRAPH_VERSION}"

def _headers():
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

def send_mark_read(message_id: str):
    """Marca a mensagem como lida (best-effort)."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    try:
        r = requests.post(url, headers=_headers(), json=payload, timeout=10)
        if r.status_code >= 400:
            logger.warning("mark_read falhou: %s - %s", r.status_code, r.text)
        return r.json() if r.ok else None
    except Exception as e:
        logger.warning("mark_read exception: %s", e)
        return None

def send_text(to: str, body: str):
    """Envia texto simples (um único bloco)."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": body}
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def send_text_chunks(to: str, body: str, chunk_size: int = 1200):
    """Quebra respostas longas em blocos seguros para o WhatsApp."""
    body = body or ""
    for i in range(0, len(body), chunk_size):
        part = body[i:i+chunk_size]
        send_text(to, part)

# ---------------------------
# Estilo conciso/operacional
# ---------------------------
SYSTEM_PROMPT = (
    "Você é o BotPMPR, assistente para policiais militares no WhatsApp. "
    "Responda SEM rodeios, em português do Brasil, no tom operacional.\n\n"
    "REGRAS DE ESTILO:\n"
    "1) No máximo 6 linhas (ou 5 passos numerados).\n"
    "2) Frases curtas, voz ativa, sem desculpas.\n"
    "3) Use *negrito* só para termos-chave.\n"
    "4) Quando útil, liste no máximo 3 pontos (•). Nada de parágrafos longos.\n"
    "5) Faça 1 pergunta de esclarecimento apenas se faltar algo ESSENCIAL.\n"
    "6) Se citar norma/procedimento, cite sigla/ato e artigo quando disponível no contexto.\n"
)

def compact_whatsapp(text: str, hard_limit: int = 900) -> str:
    """Compacta para caber bem no WhatsApp: remove excesso e limita linhas/tamanho."""
    if not text:
        return text
    t = text.strip()
    t = re.sub(r'\n{3,}', '\n\n', t)     # colapsa quebras
    lines = t.splitlines()
    if len(lines) > 8:
        lines = lines[:8] + ["…"]
    t = "\n".join(lines)
    if len(t) > hard_limit:
        t = t[:hard_limit-1].rstrip() + "…"
    return t

# ---------------------------
# OpenAI (estilo ChatGPT)
# ---------------------------
def ask_ai(user_text: str) -> str:
    """Consulta a OpenAI com instruções de concisão para WhatsApp."""
    if not OPENAI_API_KEY:
        return "A IA não está configurada (OPENAI_API_KEY ausente)."

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.1,                # mais assertivo
            max_tokens=OPENAI_MAX_TOKENS,   # resposta curta
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_text},
            ],
        )
        answer = completion.choices[0].message.content or ""
        return answer.strip()
    except Exception as e:
        logger.error("Erro OpenAI: %s", getattr(e, "message", str(e)))
        return "Não consegui consultar a IA no momento. Tente novamente em instantes."

# ---------------------------
# Health / Webhook
# ---------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "phone_number_id": PHONE_NUMBER_ID,
        "graph_version": GRAPH_VERSION,
        "openai_model": OPENAI_MODEL,
        "openai_on": bool(OPENAI_API_KEY),
        "max_tokens": OPENAI_MAX_TOKENS
    }

@app.get("/webhook")
def verify():
    """Verificação do webhook (Meta chama GET com hub.challenge)."""
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge or "", status=200, mimetype="text/plain")
    return Response(status=403)

def handle_incoming_message(msg: dict):
    """Processa mensagens fora do ciclo do webhook (<=10s)."""
    try:
        msg_id   = msg.get("id")
        from_    = msg.get("from")   # ex.: "55419..."
        msg_type = msg.get("type")

        if msg_id:
            send_mark_read(msg_id)

        if msg_type == "text":
            user_text = (msg.get("text") or {}).get("body", "").strip()
            if not user_text:
                send_text(from_, "Mensagem vazia. Pode repetir?")
                return

            ai_answer = ask_ai(user_text)
            ai_answer = compact_whatsapp(ai_answer)
            send_text_chunks(from_, ai_answer, chunk_size=1200)

        elif msg_type == "interactive":
            # Caso envie botões no futuro — responde pedindo descrição em texto
            btn = (msg.get("interactive") or {}).get("button_reply") or {}
            title = btn.get("title") or "opção"
            send_text(from_, f"Você selecionou: {title}. Descreva sua dúvida em texto, por favor.")

        else:
            send_text(from_, "Recebi seu conteúdo. Pode escrever sua dúvida em texto?")
    except Exception as e:
        logger.exception("Erro em handle_incoming_message: %s", e)

@app.post("/webhook")
def webhook():
    """Recebe eventos do WhatsApp (mensagens e status)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info("Incoming payload: %s", json.dumps(data, ensure_ascii=False))

        changes = (data.get("entry") or [{}])[0].get("changes") or []
        if not changes:
            return Response(status=200)

        value    = changes[0].get("value") or {}
        messages = value.get("messages") or []
        statuses = value.get("statuses") or []

        if messages:
            for msg in messages:
                threading.Thread(target=handle_incoming_message, args=(msg,), daemon=True).start()

        if statuses:
            st = statuses[0]
            logger.info("Status: %s  msgId: %s  dest: %s",
                        st.get("status"), st.get("id"), st.get("recipient_id"))

        return Response(status=200)
    except Exception as e:
        logger.exception("Erro no webhook: %s", e)
        return Response(status=200)
