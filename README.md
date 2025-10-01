# pmpr-bot-wa

Bot de WhatsApp (Cloud API) que busca **Portarias** no **TopK** e usa **OpenAI** para compor a resposta final. Inclui memória de curto prazo (3 últimas mensagens por usuário).

## Variáveis de ambiente

Veja `.env.example` e defina no Railway:
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `VERIFY_TOKEN`
- `TOPK_API_KEY`, `TOPK_REGION`, `TOPK_COLLECTION`
- `OPENAI_API_KEY`

## Rodar local

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # e edite os valores
python bot.py
```

Exponha seu `/webhook` (via Railway) e configure no Meta Developers:
- URL: https://SEU-APP.up.railway.app/webhook
- Verify Token: igual ao `VERIFY_TOKEN`

## Arquitetura

- `bot.py`: recebe mensagens do WhatsApp, consulta TopK, chama OpenAI, envia resposta.
- `topk_client.py`: consulta híbrida (semântica 70% + BM25 30%) no TopK.
- `llm_client.py`: gera resposta a partir dos trechos.
- `memory.py`: memória curta por usuário (3 mensagens).
- `Procfile`: comando para o Railway.
- `requirements.txt`: dependências.

## Observações

- A memória é em processo (não persiste entre deploys). Para produção, considere Redis.
- Limite de 4096 caracteres por mensagem no envio WhatsApp (aplicado no código).
