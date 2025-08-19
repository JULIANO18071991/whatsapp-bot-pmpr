import requests

class WhatsAppClient:
    def __init__(self, settings):
        self.settings = settings
        self._base = f"{settings.GRAPH_BASE_URL}/{settings.PHONE_NUMBER_ID}"
        self._hdr = {
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        }

    def is_own_message(self, body: dict) -> bool:
        try:
            statuses = body["entry"][0]["changes"][0]["value"].get("statuses", [])
            return len(statuses) > 0
        except Exception:
            return False

    def mark_read(self, message_id: str):
        url = f"{self._base}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        r = requests.post(url, headers=self._hdr, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def send_text(self, to: str, text: str):
        url = f"{self._base}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        r = requests.post(url, headers=self._hdr, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()
