> *"O racismo é o crime perfeito que só a vítima vê. E quando se vê insatisfeita, e não guarda mais para si, vira ela o próprio suspeito acusado de mi-mi-mi."*
> — **Lázaro Ramos**, *Na minha pele* (2017)

> *"Racism is the perfect crime that only the victim sees. And when the victim grows dissatisfied and no longer keeps it to herself, she becomes the very suspect, accused of whining."*

---

# Discord-Dossie - Task Bar Hero

## Contexto

Este projeto é um ETL simples desenvolvido em pouco tempo com o objetivo de analisar comportamentos inadequados dentro de um servidor do Discord. O servidor em questão é o **Task Bar Hero (TBH)**, comunidade de um jogo RPG idle, cujo canal `#chat-polish` concentrou uma série de mensagens com conteúdo racista e xenofóbico, em sua maioria escritas em polonês, tornando a moderação especialmente difícil para administradores que não falam o idioma.

### Intenção

O projeto **não tem caráter retaliatório**. Toda análise foi feita com dados e ferramentas publicamente disponíveis: a API do Discord, modelos de IA acessíveis a qualquer pessoa e bibliotecas de código aberto. Qualquer membro do servidor poderia reproduzir este trabalho com os mesmos recursos.

A intenção é fornecer aos **moderadores do servidor** as ferramentas necessárias para:

- Validar mensagens em idiomas que não dominam, removendo a barreira linguística da moderação;
- Identificar padrões de comportamento ao longo do tempo, e não apenas mensagens isoladas;
- Agir com base em evidências concretas, reduzindo parcialidade e subjetividade nas decisões.

---

## Context

This project is a simple ETL pipeline built to analyze inappropriate behavior within a Discord server. The target server is **Task Bar Hero (TBH)**, the community of an idle RPG game, whose `#chat-polish` channel concentrated a series of messages with racist and xenophobic content, most of them written in Polish, making moderation especially difficult for administrators who do not speak the language.

### Intent

This project **has no retaliatory intent**. All analysis was done using publicly available data and tools: the Discord API, AI models accessible to anyone, and open-source libraries. Any member of the server could reproduce this work with the same resources.

The goal is to provide the **server moderators** with the tools they need to:

- Validate messages in languages they do not speak, removing the language barrier from moderation;
- Identify behavioral patterns over time, not just isolated messages;
- Act based on concrete evidence, reducing bias and subjectivity in decisions.

---

## Como funciona - Fluxo do ETL

O pipeline é composto por 8 etapas encadeadas. Cada uma produz arquivos intermediários que alimentam a próxima, permitindo pausar, inspecionar e retomar em qualquer ponto.

```
01_extract -> 02_translate -> 03_detect -> 04_deep_fetch
           -> 05_pre_filter -> 06_ai_review -> 07_context_review -> 08_report
```

### `01_extract.py` - Extração das mensagens

Baixa todas as mensagens dos canais-alvo via API pública do Discord. A extração é **resumível**: se interrompida, retoma do ponto onde parou. Em execuções subsequentes, busca apenas mensagens novas.

### `02_translate.py` - Tradução automática

Traduz cada mensagem para inglês e português brasileiro usando a API da Anthropic. O resultado é armazenado localmente para evitar reprocessamento, e execuções seguintes são instantâneas para mensagens já traduzidas.

### `03_detect.py` - Detecção por palavras-chave

Aplica uma peneira inicial baseada em listas de palavras-chave e expressões em múltiplos idiomas (polonês, inglês, português). Gera um ranking de usuários mais frequentes nos resultados e serve de pré-seleção barata para as etapas seguintes.

### `04_deep_fetch.py` - Busca profunda dos suspeitos

Com o ranking em mãos, busca o histórico completo de mensagens dos usuários que mais apareceram na etapa anterior, ampliando a amostra para além do canal principal e aumentando a cobertura das análises seguintes.

### `05_pre_filter.py` - Pré-filtro avançado

Aplica filtros com regex contextual, razão sinal/ruído e proximidade a mensagens já flagradas. Reduz drasticamente o volume a ser enviado para a IA, cortando custo e tempo sem perder casos relevantes.

### `06_ai_review.py` - Revisão por IA (primeira passagem)

Envia cada mensagem filtrada para classificação pela IA (Claude). Cada mensagem recebe um label (`racist`, `xenophobic`, `offensive`, `suspicious` ou `clean`) e um score de confiança. A IA analisa cada mensagem de forma **isolada** nesta etapa.

### `07_context_review.py` - Revisão com contexto (segunda passagem)

Pega as mensagens classificadas como `suspicious` ou `clean` na etapa anterior e as reenvia para a IA junto com as **5 mensagens anteriores e posteriores** do canal. Isso permite identificar cumplicidade, ironia e casos que só fazem sentido em contexto, reduzindo significativamente os falsos negativos.

### `08_report.py` - Geração do relatório (SPA)

Consolida tudo em um **Single Page Application** estático, pronto para ser hospedado em qualquer serviço (GitHub Pages, Netlify, etc.). O SPA inclui:

- Navegação por casos com print do Discord, tradução e classificação da IA;
- Ranking dos infratores por gravidade;
- Filtros por categoria e busca por texto;
- Badges copiáveis (ID da mensagem, canal, usuário) para facilitar denúncias;
- Botão de "Copiar como imagem" para compartilhar evidências diretamente.

---

## Curadoria manual

Após gerar o SPA, é possível fazer revisão manual com ferramentas dedicadas:

```bash
# Marcar como falso positivo (remove do SPA):
venv/bin/python tools/mark_clean.py <id1> <id2> ...

# Reclassificar manualmente:
venv/bin/python tools/reclassify.py <id1> <id2> ... --label racist

# Reclassificar via IA (re-analisa com contexto):
venv/bin/python tools/reclassify.py <id1> <id2> ... --ai

# Capturar prints das mensagens:
venv/bin/python tools/screenshot.py --all --workers 3

# Após qualquer curadoria, regenerar o SPA:
venv/bin/python etl/08_report.py
```

---

## Stack

| Camada | Tecnologia |
|---|---|
| Extração | Discord API (pública) |
| Tradução e classificação | Anthropic Claude (API) |
| Screenshots | Playwright (Chromium) |
| SPA | HTML + CSS + JS vanilla |
| Hospedagem | GitHub Pages |

---

## Aviso legal

Todo o conteúdo exibido no dossiê é composto por mensagens enviadas publicamente em canais de texto do Discord. Nenhuma informação privada foi acessada. O projeto não tem qualquer vínculo com os desenvolvedores do jogo Task Bar Hero nem com o Discord Inc.
