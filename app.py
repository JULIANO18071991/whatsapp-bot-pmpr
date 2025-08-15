import os
import json
import logging
import threading
from flask import Flask, request, Response
import requests

# === NEW: OpenAI ===
from openai import OpenAI

app = Flask(__name__)

# Logs
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = app.logger

# Env vars
VERIFY_TOKEN     = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN   = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID  = os.getenv("PHONE_NUMBER_ID")

# === NEW: OpenAI config ===
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=OPENAI_API_KEY)

# Graph API
GRAPH_VERSION = "v21.0"
GRAPH_BASE    = f"https://graph.facebook.com/{GRAPH_VERSION}"

def _headers():
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

def send_mark_read(message_id: str):
    """Marca a mensagem como lida (best-effort)."""
    url = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
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

# === NEW: quebra respostas longas em blocos seguros para WhatsApp ===
def send_text_chunks(to: str, body: str, chunk_size: int = 3500):
    body = body or ""
    # WhatsApp aceita ~4096 chars; usamos margem
    for i in range(0, len(body), chunk_size):
        part = body[i:i+chunk_size]
        send_text(to, part)

# === NEW: prompt e chamada à OpenAI ===
SYSTEM_PROMPT = (
    "Você é o BotPMPR, assistente para policiais militares no WhatsApp. "
    "Responda de forma clara, objetiva e em português do Brasil. "
    "Se a pergunta envolver legislação institucional, procedimentos padrão ou protocolos, "
    "explique passo a passo e destaque avisos de segurança quando necessário. "
    "Se não tiver certeza absoluta, diga o que você sabe e sugira verificar a norma/BO/POP aplicável. "
    "Nunca invente fatos. Seja conciso, mas completo."
)

def ask_ai(user_text: str, user_id: str = None) -> str:
    """
    Chama a OpenAI (estilo ChatGPT).
    - Por enquanto sem memória persistente; depois podemos plugar Redis/DB.
    """
    if not OPENAI_API_KEY:
        return "A IA não está configurada (OPENAI_API_KEY ausente)."

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.2,
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

@app.get("/health")
def health():
    return {
        "ok": True,
        "phone_number_id": PHONE_NUMBER_ID,
        "graph_version": GRAPH_VERSION,
        "openai_model": OPENAI_MODEL,
        "openai_on": bool(OPENAI_API_KEY)
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

# === NEW: processamento fora do ciclo do webhook para não estourar 10s ===
def handle_incoming_message(msg: dict):
    try:
        msg_id = msg.get("id")
        from_  = msg.get("from")  # ex.: "55419..."
        msg_type = msg.get("type")

        if msg_id:
            send_mark_read(msg_id)

        if msg_type == "text":
            user_text = (msg.get("text") or {}).get("body", "").strip()
            if not user_text:
                send_text(from_, "Mensagem vazia. Pode repetir?")
                return

            # chama a IA e manda a resposta em blocos
            ai_answer = ask_ai(user_text, user_id=from_)
            send_text_chunks(from_, ai_answer)

        elif msg_type == "interactive":
            # Se o usuário mandar botões/lists (caso venha a usar no futuro), trate aqui
            btn = (msg.get("interactive") or {}).get("button_reply") or {}
            title = btn.get("title") or "opção"
            send_text(from_, f"Você selecionou: {title}. Pode descrever sua dúvida?")

        else:
            # Outros tipos (imagem/áudio/documento) — responda gentilmente
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

        value = changes[0].get("value") or {}
        messages = value.get("messages") or []
        statuses = value.get("statuses") or []

        # Mensagens: processa em thread para responder 200 rápido
        if messages:
            for msg in messages:
                threading.Thread(target=handle_incoming_message, args=(msg,), daemon=True).start()

        # Status (sent/delivered/read/failed): só logar por enquanto
        if statuses:
            st = statuses[0]
            logger.info("Status: %s  msgId: %s  dest: %s",
                        st.get("status"), st.get("id"), st.get("recipient_id"))

        # Sempre 200 para a Meta não reenviar
        return Response(status=200)
    except Exception as e:
        logger.exception("Erro no webhook: %s", e)
        return Response(status=200)
