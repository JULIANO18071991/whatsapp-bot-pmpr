from flask import Flask, request
import requests
import os

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")  # variável de ambiente no Railway
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")  # variável de ambiente no Railway
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")  # variável de ambiente no Railway

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return "Erro de verificação", 403
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()

    try:
        from_number = data["entry"][0]["changes"][0]["value"]["messages"][0]["from"]
        message_body = data["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]

        print(f"Mensagem recebida de {from_number}: {message_body}")

        reply_text = "Recebi sua mensagem! Em breve vou te enviar informações sobre a PMPR."
        send_message(from_number, reply_text)

    except Exception as e:
        print("Erro ao processar:", e)

    return "EVENT_RECEIVED", 200

def send_message(to, text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    requests.post(url, headers=headers, json=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
