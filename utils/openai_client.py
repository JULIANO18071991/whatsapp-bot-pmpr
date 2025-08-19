import os
from openai import OpenAI

class LLMClient:
    def __init__(self, settings):
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY ausente.")
        self.client = OpenAI(api_key=api_key)
        self.model = settings.OPENAI_MODEL

    def chat(self, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você atende policiais militares via WhatsApp. "
                        "Responda de forma curta, objetiva e impessoal. "
                        "Só use informações presentes nos trechos fornecidos. "
                        "Se não houver base suficiente, diga que não encontrou no acervo atual. "
                        "Finalize SEMPRE com a citação no formato: "
                        "Nome do documento, nº XXX, assunto, DD/MM/AAAA."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=350,
        )
        return resp.choices[0].message.content.strip()
