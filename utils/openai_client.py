import os
from typing import List
from openai import OpenAI


class LLMClient:
    def __init__(self, settings):
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY ausente.")

        self.client = OpenAI(api_key=api_key)
        self.model = getattr(settings, "OPENAI_MODEL", None) or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        # Modelo de embeddings (pode mudar via env OPENAI_EMBED_MODEL)
        self.embed_model = getattr(settings, "OPENAI_EMBED_MODEL", None) or os.getenv(
            "OPENAI_EMBED_MODEL", "text-embedding-3-small"
        )

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

    def embed(self, text: str) -> List[float]:
        """
        Gera embedding para memória vetorial.
        Usa o modelo definido em OPENAI_EMBED_MODEL (default: text-embedding-3-small).
        """
        # Evita quebras de linha que podem prejudicar a normalização do embedding
        clean = (text or "").replace("\n", " ")
        resp = self.client.embeddings.create(
            model=self.embed_model,
            input=clean,
        )
        return resp.data[0].embedding
