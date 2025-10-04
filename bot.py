import os, json, traceback, requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from memory import Memory
from topk_client import buscar_topk
from llm_client import gerar_resposta

load_dotenv()
app = Flask(__name__)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "verify_token_padrao")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID_ENV = os.environ.get("WHATSAPP_PHONE_ID", "")
DEBUG = os.environ.get("DEBUG", "0") == "1"

memoria = Memory(max_msgs=3)

# ---------- helpers seguros (sem alterações) ----------
def _as_dict(x, fallback=None):
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            y = json.loads(x)
            return y if isinstance(y, dict) else (fallback or {})
        except Exception:
            return fallback or {}
    return fallback or {}

def _as_list(x, fallback=None):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            y = json.loads(x)
            return y if isinstance(y, list) else (fallback or [])
        except Exception:
            return fallback or []
    return fallback or []

def _safe_json(req) -> dict:
    data = req.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = req.get_data(as_text=True)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _preview(obj, n=400):
    try:
        s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
        return s[:n]
    except Exception:
        return str(type(obj))

def _extract_wa(payload: dict):
    """
    Retorna (phone_number_id, from, text). Tolerante a strings internas.
    """
    entry = _as_list(payload.get("entry"), [])
    if not entry:
        return None, None, None

    first_entry = _as_dict(entry[0], {})
    changes = _as_list(first_entry.get("changes"), [])
    if not changes:
        return None, None, None

    first_change = _as_dict(changes[0], {})
    value = _as_dict(first_change.get("value"), {})

    metadata = _as_dict(value.get("metadata"), {})
    phone_id = metadata.get("phone_number_id")

    messages = _as_list(value.get("messages"), [])
    if not messages:
        return phone_id, None, None

    msg = _as_dict(messages[0], {})
    from_ = msg.get("from")
    mtype = msg.get("type")
    text = ""

    if mtype == "text":
        text = _as_dict(msg.get("text"), {}).get("body", "") or ""
    elif mtype == "interactive":
        inter = _as_dict(msg.get("interactive"), {})
        if "list_reply" in inter:
            lr = _as_dict(inter.get("list_reply"), {})
            text = lr.get("title") or lr.get("id") or ""
        elif "button_reply" in inter:
            br = _as_dict(inter.get("button_reply"), {})
            text = br.get("title") or br.get("id") or ""
    else:
        text = ""

    if not text:
        text = _as_dict(msg.get("text"), {}).get("body", "") or ""

    return phone_id, from_, (text or "").strip()

def enviar_whatsapp(phone_id: str, to: str, body: str):
    token = WHATSAPP_TOKEN
    if not token:
        print("[WARN] WHATSAPP_TOKEN ausente; não foi possível responder.")
        return
    pid = phone_id or WHATSAPP_PHONE_ID_ENV
    if not pid:
        print("[WARN] phone_number_id ausente; defina WHATSAPP_PHONE_ID ou deixe vir no payload.")
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
        "text": {"body": (body or "" )[:4096]},
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code >= 300:
            print("[ERRO WA]", r.status_code, r.text[:400])
    except Exception as e:
        print("[ERRO WA req]", e)

# ---------- rotas (sem alterações) ----------
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
            print("[DEBUG payload type]", type(payload).__name__)
            print("[DEBUG payload preview]", _preview(payload))

        phone_id, from_, text = _extract_wa(payload)

        if DEBUG:
            print("[DEBUG phone_id]", phone_id)
            print("[DEBUG from_]", from_)
            print("[DEBUG text]", text)

        if not from_:
            return jsonify({"status": "ok (no from/message)"}), 200

        if not text:
            enviar_whatsapp(phone_id, from_, "Não entendi sua mensagem. Envie um texto, por favor.")
            return jsonify({"status": "ok"}), 200

        contexto = memoria.get_context(from_)
        resultados = buscar_topk(text)
        if DEBUG:
            print("[DEBUG topk resultados tipo/len]", type(resultados).__name__, len(resultados) if hasattr(resultados, "__len__") else "-")
            
            # --- CORREÇÃO ADICIONADA AQUI ---
            # Esta linha irá mostrar o conteúdo exato que está sendo enviado para a LLM.
            print("[DEBUG topk CONTEÚDO]", json.dumps(resultados, ensure_ascii=False, indent=2))
            # ---------------------------------

        resposta = gerar_resposta(text, contexto, resultados)
        memoria.add_msg(from_, text)
        enviar_whatsapp(phone_id, from_, resposta)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("[ERRO webhook]", repr(e))
        print(traceback.format_exc())
        return jsonify({"status": "error"}), 200

# ---------- dev (sem alterações) ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
