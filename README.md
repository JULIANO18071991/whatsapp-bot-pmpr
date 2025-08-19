# PM WhatsApp Bot (Fluxo Enxuto)

Bot Flask que:
1) recebe mensagens do WhatsApp (Meta Webhook),  
2) consulta o **AutoRAG** para recuperar trechos,  
3) usa **OpenAI** para produzir resposta curta com **citação padronizada**,  
4) responde via **Graph API** e marca a mensagem como lida.

## Rotas

- `GET /health` – verificação simples  
- `GET /webhook` – verificação do `hub.verify_token` (Meta)  
- `POST /webhook` – recebe eventos e responde  
- `POST /admin/reindex` – (opcional) força reindex no AutoRAG (header `X-Admin-Token`)

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # e preencha as variáveis
gunicorn -w 2 -b 0.0.0.0:8000 app:app
```

## Configuração do Webhook (Meta)
Use a rota `GET /webhook` com o `VERIFY_TOKEN` definido no `.env`.  
No painel do WhatsApp Cloud API, aponte o webhook para `https://SEU_HOST/webhook`.

## Notas
- Este projeto **não** mantém base local; a recuperação é 100% via **AutoRAG**.  
- O formato de citação exigido ao final da resposta é:  
  `Nome do documento, nº XXX, assunto, DD/MM/AAAA.`
