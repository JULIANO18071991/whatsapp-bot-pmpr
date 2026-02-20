# bot.py
# -*- coding: utf-8 -*-

import os
import io
import re
import json
import base64
import unicodedata
import logging
import tempfile
import requests
import pathlib
import importlib.util
import threading
import time
from contextlib import redirect_stdout
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from topk_client import buscar_topk_multi
from llm_client import gerar_resposta
from dedup import Dedup
from synonyms import expand_query

# ========= GOOGLE DRIVE =========
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
except Exception:
    service_account = None
    build = None
    MediaIoBaseDownload = None

DEBUG = os.getenv("DEBUG", "0") == "1"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("googleapiclient").setLevel(logging.WARNING)
logging.getLogger("google.auth").setLevel(logging.WARNING)

log = logging.getLogger("bot")
app = Flask(__name__)
dedup = Dedup(ttl=600)

WA_MAX = 4096
WA_SAFE = 3900

_jobs_lock = threading.Lock()
_jobs_running = {}

def _job_start(key: str, ttl: int = 300) -> bool:
    now = time.time()
    with _jobs_lock:
        ts = _jobs_running.get(key)
        if ts and (now - ts) < ttl:
            return False
        _jobs_running[key] = now
        return True

def _job_end(key: str):
    with _jobs_lock:
        _jobs_running.pop(key, None)

# =========================
# WHATSAPP
# =========================

def _wa_post(phone_id: str, payload: dict):
    token = os.getenv("WHATSAPP_TOKEN")
    api_version = os.getenv("WHATSAPP_API_VERSION", "v22.0")
    url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)

    try:
        log.info(f"[WA] status={r.status_code} resp={r.json()}")
    except Exception:
        log.info(f"[WA] status={r.status_code} resp_text={r.text}")

    return r


def enviar_whatsapp_texto(phone_id: str, to: str, text: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    return _wa_post(phone_id, payload)


def enviar_whatsapp(phone_id: str, to: str, text: str):
    r = enviar_whatsapp_texto(phone_id, to, text)
    if r.ok:
        return

# =========================
# NORMALIZAÇÃO DE COMANDO
# =========================

def _strip_accents(s: str) -> str:
    s = s or ""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s)
        if unicodedata.category(ch) != "Mn"
    )

def _norm_cmd(s: str) -> str:
    s = _strip_accents((s or "").strip()).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# =========================
# RELATÓRIO
# =========================

def gerar_relatorio_cavalaria_texto() -> str:
    return "RELATÓRIO GERADO (exemplo)"

def _rodar_e_enviar_relatorio_cavalaria(phone_id: str, to: str):
    try:
        relatorio = gerar_relatorio_cavalaria_texto()
        enviar_whatsapp(phone_id, to, relatorio)
    except Exception as e:
        enviar_whatsapp(phone_id, to, f"Erro: {e}")
    finally:
        _job_end(to)

# =========================
# WEBHOOK
# =========================

@app.post("/webhook")
def webhook():
    data = request.get_json(force=True)

    try:
        value = data["entry"][0]["changes"][0]["value"]
        if "messages" not in value:
            return jsonify({"ok": True}), 200

        msg = value["messages"][0]
        phone_id = value["metadata"]["phone_number_id"]
        from_ = msg["from"]
        text = msg.get("text", {}).get("body", "")
        message_id = msg.get("id")

        if not message_id or not text:
            return jsonify({"ok": True}), 200

    except Exception:
        return jsonify({"ok": True}), 200

    if dedup.seen(message_id):
        return jsonify({"ok": True}), 200

    cmd = _norm_cmd(text)

    # ✅ ALTERAÇÃO AQUI
    if "relatorio" in cmd and "cavalaria" in cmd:

        if not _job_start(from_, ttl=300):
            enviar_whatsapp(phone_id, from_, "⏳ Já estou gerando seu relatório.")
            return jsonify({"ok": True}), 200

        enviar_whatsapp(phone_id, from_, "⏳ Gerando relatório cavalaria...")

        t = threading.Thread(
            target=_rodar_e_enviar_relatorio_cavalaria,
            args=(phone_id, from_),
            daemon=True
        )
        t.start()

        return jsonify({"ok": True}), 200

    resposta = gerar_resposta(text, [])
    enviar_whatsapp(phone_id, from_, resposta)

    return jsonify({"ok": True}), 200

# =========================
# SIMULATE
# =========================

@app.post("/simulate-message")
def simulate_message():
    data = request.get_json(force=True)
    from_ = data.get("from")
    text = data.get("text", "")

    phone_id = os.getenv("WHATSAPP_PHONE_ID")
    cmd = _norm_cmd(text)

    # ✅ ALTERAÇÃO AQUI
    if "relatorio" in cmd and "cavalaria" in cmd:

        if not _job_start(from_, ttl=300):
            enviar_whatsapp(phone_id, from_, "⏳ Já estou gerando seu relatório.")
            return jsonify({"success": True}), 200

        enviar_whatsapp(phone_id, from_, "⏳ Gerando relatório cavalaria...")

        t = threading.Thread(
            target=_rodar_e_enviar_relatorio_cavalaria,
            args=(phone_id, from_),
            daemon=True
        )
        t.start()

        return jsonify({"success": True}), 200

    enviar_whatsapp(phone_id, from_, "Comando não reconhecido.")
    return jsonify({"success": True}), 200

# =========================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=DEBUG)
