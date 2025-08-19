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
                        "Você é um assistente especializado em responder dúvidas sobre normas da Polícia Militar.\n"
                        "Baseie-se apenas nos documentos recuperados.\n\n"
                        "Regras principais:\n"
                        "- Explique a resposta em linguagem simples e objetiva.\n"
                        "- Sempre cite o documento que fundamenta a resposta, incluindo nome, número, data e artigo/portaria/regulamento.\n"
                        "  Exemplo: 'Conforme a Portaria do Comando-Geral nº 40/2013, Art. 3º, está previsto que...'.\n"
                        "- Se houver mais de um documento relacionado, apresente todos de forma resumida.\n"
                        "- Se nenhum documento tratar do tema, diga claramente: 'Não há previsão normativa encontrada nos documentos disponíveis sobre essa questão.'\n"
                        "- Nunca invente documentos, artigos ou datas.\n\n"
                        "Regras adicionais para aprofundamento:\n"
                        "- Se o usuário pedir para 'falar mais', 'discorra', 'explique melhor' ou termos semelhantes, "
                        "forneça uma resposta expandida, incluindo:\n"
                        "  • Contexto adicional sobre a norma (objetivo, importância, histórico se aplicável).\n"
                        "  • Exemplos práticos de aplicação.\n"
                        "  • Possíveis consequências administrativas ou disciplinares associadas.\n"
                        "- Continue sempre citando o documento oficial que serve de base.\n\n"
                        "Formato esperado da resposta:\n"
                        "1. Breve explicação inicial em linguagem acessível.\n"
                        "2. Citação ou resumo do documento oficial.\n"
                        "3. Referência explícita no formato: Nome do documento, nº XXX, assunto, DD/MM/AAAA.\n"
                        "4. (Se solicitado aprofundamento) contexto adicional + exemplos práticos.\n"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=500,
        )
        return resp.choices[0].message.content.strip()

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
