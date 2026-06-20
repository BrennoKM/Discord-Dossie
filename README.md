# Discord Dossie

Ferramenta de coleta e analise de mensagens do Discord para documentar racismo e xenofobia.
Funciona com qualquer servidor e qualquer canal.

## Configuracao

```bash
python -m venv venv
venv/bin/pip install requests deep-translator jinja2 groq python-dotenv beautifulsoup4 playwright
venv/bin/playwright install chromium

cp .env.example .env
# Edite .env com seu token do Discord e chave do Groq
```

## Arquivo .env

```
DISCORD_TOKEN=seu_token_aqui
GROQ_API_KEY=sua_chave_groq_aqui
```

## Pipeline ETL

```bash
# 1. Extrai todas as mensagens de um canal (resumivel)
python etl/01_extract.py --channel CHANNEL_ID

# 2. Traduz tudo para EN e PT-BR (resumivel, pula ja traduzidas)
python etl/02_translate.py --channel CHANNEL_ID

# 3. Peneira inicial por keywords, gera ranking de suspeitos
python etl/03_detect.py --channel CHANNEL_ID

# 4. Busca historico completo dos top suspeitos em TODOS os canais
#    Nao duplica mensagens que ja existem em outros canais
python etl/04_deep_fetch.py --top 10

# 5. IA (Groq/Llama 3) classifica mensagens dos suspeitos em lotes
python etl/05_ai_review.py --channel CHANNEL_ID

# 6. Gera relatorio HTML e cards de exposicao prontos
python etl/06_report.py --channel CHANNEL_ID
```

## Uso para outros servidores

Edite `etl/common.py`:
- `GUILD_ID`: ID do servidor
- `ALL_CHANNELS`: dicionario de canais do servidor

O resto do pipeline funciona igual.

## Estrutura de dados

```
data/
  channels/
    CHANNEL_ID/
      meta.json          <- estado da extracao (ultimo ID, etc)
      messages.jsonl     <- mensagens compactas, uma por linha
      authors.json       <- {user_id: {u: username, d: display}}
      translations.json  <- {msg_id: {en, pt}}
      ai_review.json     <- {msg_id: {label, confidence, reason}}
  suspects.json          <- ranking de suspeitos por canal
  suspect_profiles.json  <- historico completo dos investigados
  expose_CHANNEL_ID.json <- cards prontos para exposicao
```

## Screenshots

```bash
# Tira prints reais do Discord para as mensagens confirmadas
python screenshot.py --limit 20
```
Na primeira execucao abre o browser para voce fazer login. Salva a sessao automaticamente.
