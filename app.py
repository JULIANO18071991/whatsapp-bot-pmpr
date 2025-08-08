import os
import logging
from flask import Flask, request
import requests

app = Flask(__name__)

# Configuração do log para exibir no Railway
logging.basicConfig(level=logging.INFO)

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "meu_token")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        # Verificação do webhook
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            logging.info("Webhook verificado com sucesso!")
            return challenge, 200
        else:
            logging.warning("Falha na verificação do webhook.")
            return "Erro de verificação", 403

    if request.method == "POST":
        data = request.get_json()
        logging.info(f"📩 Payload recebido: {data}")

        try:
            if "messages" in data["entry"][0]["changes"][0]["value"]:
                message = data["entry"][0]["changes"][0]["value"]["messages"][0]
                from_number = message["from"]
                text = message.get("text", {}).get("body", "")

                logging.info(f"Mensagem recebida de {from_number}: {text}")

                send_whatsapp_message(from_number, f"Recebi sua mensagem: {text}")
        except Exception as e:
            logging.error(f"Erro ao processar mensagem: {e}")

        return "EVENT_RECEIVED", 200


def send_whatsapp_message(to, message):
    """Envia mensagem pelo WhatsApp Cloud API"""
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": message}
    }

    response = requests.post(url, headers=headers, json=payload)
    logging.info(f"📤 Enviando para {to}: {message}")
    logging.info(f"Status: {response.status_code} | Resposta: {response.text}")
    return response


@app.route("/", methods=["GET"])
def home():
    return "Bot do WhatsApp está ativo!", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
