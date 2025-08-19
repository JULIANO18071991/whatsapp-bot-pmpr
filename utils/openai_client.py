import os
from typing import List
from openai import OpenAI


class LLMClient:
    def __init__(self, settings):
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY ausente.")

        self.client = OpenAI(api_key=api_key)

        # Modelo de chat
        self.model = (
            getattr(settings, "OPENAI_MODEL", None)
            or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        )

        # Modelo de embeddings
        self.embed_model = (
            getattr(settings, "OPENAI_EMBED_MODEL", None)
            or os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        )

        # Parâmetros de geração
        self.temperature = float(
            getattr(settings, "OPENAI_TEMPERATURE", None)
            or os.getenv("OPENAI_TEMPERATURE", "0.2")
        )
        self.max_tokens = int(
            getattr(settings, "OPENAI_MAX_TOKENS", None)
            or os.getenv("OPENAI_MAX_TOKENS", "500")
        )

    def chat(self, prompt: str) -> str:
        """
        Recebe o prompt já montado por utils/prompt.build_prompt(...)
        e envia para o modelo. O formato de saída é definido em prompt.py.
        """
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Você é um assistente jurídico-normativo da PMPR. "
                            "Siga rigorosamente as instruções recebidas no prompt do usuário. "
                            "Nunca invente informações ou documentos."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                presence_penalty=0,
                frequency_penalty=0,
            )
            content = (resp.choices[0].message.content or "").strip()

            # Garante que inicie com "Resposta" caso o modelo esqueça
            if content and not content.lstrip().lower().startswith("resposta"):
                content = "Resposta\n\n" + content

            return content
        except Exception as e:
            return (
                "Resposta\n\n"
                "Não foi possível gerar a resposta no momento. "
                f"(Erro técnico: {type(e).__name__})"
            )

    def embed(self, text: str) -> List[float]:
        """
        Gera embedding para memória vetorial.
        Usa o modelo definido em OPENAI_EMBED_MODEL (default: text-embedding-3-small).
        """
        clean = (text or "").replace("\n", " ")
        resp = self.client.embeddings.create(
            model=self.embed_model,
            input=clean,
        )
        return resp.data[0].embedding
