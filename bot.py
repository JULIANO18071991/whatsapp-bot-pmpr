import os, json, requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from memory import Memory
from topk_client import buscar_topk
from llm_client import gerar_resposta

load_dotenv()
app = Flask(__name__)

# ENV
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verify_token_padrao")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID_ENV = os.environ.get("WHATSAPP_PHONE_ID", "")  # opcional (usaremos o do payload se vier)
DEBUG = os.environ.get("DEBUG", "0") == "1"

# Memória (3 últimas mensagens por usuário)
memoria = Memory(max_msgs=3)

# -------------------- utilidades --------------------
def _safe_json(req) -> dict:
    """Garante dict mesmo se o body vier como string."""
    data = req.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = req.get_data(as_text=True)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _extract_wa(payload: dict):
    """
    Extrai (phone_number_id, from, text) do payload padrão da Cloud API.
    Suporta 'text' e 'interactive' (list/button).
    """
    entry = (payload.get("entry") or [])
    if not entry:
        return None, None, None
    changes = (entry[0].get("changes") or [])
    if not changes:
        return None, None, None

    value = changes[0].get("value") or {}
    phone_id = value.get("metadata", {}).get("phone_number_id")

    messages = value.get("messages") or []
    if not messages:
        return phone_id, None, None

    msg = messages[0]
    from_ = msg.get("from")
    text = ""

    mtype = msg.get("type")
    if mtype == "text":
        text = (msg.get("text") or {}).get("body", "") or ""
    elif mtype == "interactive":
        inter = msg.get("interactive") or {}
        if "list_reply" in inter:
            lr = inter["list_reply"]
            text = lr.get("title") or lr.get("id") or ""
        elif "button_reply" in inter:
            br = inter["button_reply"]
            text = br.get("title") or br.get("id") or ""
    else:
        # outros tipos (image, audio etc) -> sem texto
        text = ""

    return phone_id, from_, (text or "").strip()

def enviar_whatsapp(phone_id: str, to: str, body: str):
    """Envia texto via Graph API. Usa phone_id dinâmico do payload se disponível."""
    token = WHATSAPP_TOKEN
    if not token:
        print("[WARN] WHATSAPP_TOKEN ausente; não foi possível responder.")
        return

    pid = phone_id or WHATSAPP_PHONE_ID_ENV
    if not pid:
        print("[WARN] phone_number_id ausente; defina WHATSAPP_PHONE_ID ou permita que venha no payload.")
        return

    url = f"https://graph.facebook.com/v17.0/{pid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": (body or "")[:4096]},
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code >= 300:
            print("[ERRO WA]", r.status_code, r.text[:400])
    except Exception as e:
        print("[ERRO WA req]", e)

# -------------------- rotas --------------------
@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge or "", 200
    return "forbidden", 403

@app.post("/webhook")
def webhook():
    try:
        payload = _safe_json(request)
        if DEBUG:
            print("[DEBUG webhook payload]", json.dumps(payload, ensure_ascii=False)[:4000])

        phone_id, from_, text = _extract_wa(payload)
        if not from_:
            # pode ser entrega/ack/status (sem mensagem) -> confirme 200 para o Meta não reenviar
            return jsonify({"status": "ok (no from/message)"}), 200

        if not text:
            enviar_whatsapp(phone_id, from_, "Não entendi sua mensagem. Envie um texto, por favor.")
            return jsonify({"status": "ok"}), 200

        # memória → busca no TopK → LLM
        contexto = memoria.get_context(from_)
        resultados = buscar_topk(text)            # lista de docs/trechos
        resposta = gerar_resposta(text, contexto, resultados)

        # atualiza memória (após gerar a resposta)
        memoria.add_msg(from_, text)

        # envia
        enviar_whatsapp(phone_id, from_, resposta)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("[ERRO webhook]", repr(e))
        # retornamos 200 para evitar reentrega repetida do Meta
        return jsonify({"status": "error"}), 200

# -------------------- dev server --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
