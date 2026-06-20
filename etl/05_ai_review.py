#!/usr/bin/env python3
"""
05_ai_review.py - Envia mensagens dos suspeitos para o Groq (Llama 3) classificar.
Resumivel: pula msgs ja classificadas. Processa em lotes de 15 msgs por chamada.

Uso:
  python etl/05_ai_review.py --channel 1510279576721428612
  python etl/05_ai_review.py --channel chat-polish --top 10
"""

import argparse
import json
import sys
import time
from pathlib import Path

from groq import Groq

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, GROQ_API_KEY,
    load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path, ai_review_path,
    suspects_path, discord_link,
)

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Voce e um sistema de analise de conteudo especializado em detectar racismo e xenofobia em mensagens de Discord.

Analise cada mensagem e classifique com um dos rotulos:
- "racist": contem racismo explicito (slurs, comparacoes com animais, inferioridade racial)
- "xenophobic": xenofobia explicita (mandando embora, dizendo que nao pertence, discriminacao por nacionalidade)
- "offensive": ofensivo mas nao necessariamente racista/xenofobico (insultos, menosprezo)
- "suspicious": suspeito, precisa de contexto humano para confirmar
- "clean": sem problemas

Responda APENAS com JSON valido no formato:
[{"id": "MSG_ID", "label": "LABEL", "confidence": 0.95, "reason": "breve explicacao em portugues"}]

Seja objetivo. Contexto: servidor de jogo online onde ocorreram denuncias de racismo contra jogadores brasileiros."""


def review_batch(messages: list[dict], translations: dict, authors: dict) -> list[dict]:
    """Envia um lote de mensagens para o Groq classificar."""
    if not messages:
        return []

    lines = []
    for m in messages:
        uid    = m.get("a", "")
        author = authors.get(uid, {}).get("u", "?")
        text   = m.get("c", "").strip()
        en     = translations.get(m["id"], {}).get("en", "")
        pt     = translations.get(m["id"], {}).get("pt", "")

        line = f'ID:{m["id"]} | @{author} | ORIGINAL: {text}'
        if en and en.lower() != text.lower():
            line += f' | EN: {en}'
        if pt and pt.lower() != text.lower():
            line += f' | PT-BR: {pt}'
        lines.append(line)

    user_msg = "Classifique as mensagens abaixo:\n\n" + "\n".join(lines)

    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content.strip()

        # Extrai o JSON da resposta
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        return json.loads(raw[start:end])

    except Exception as e:
        print(f"\n    Erro na chamada Groq: {e}")
        return []


def run(channel_id: str, top_n: int = 0, user_id: str = ""):
    msgs     = load_jsonl(messages_path(channel_id))
    authors  = load_json(authors_path(channel_id), {})
    trans    = load_json(translations_path(channel_id), {})
    review   = load_json(ai_review_path(channel_id), {})
    suspects = load_json(suspects_path(), [])

    ch_name = ALL_CHANNELS.get(channel_id, channel_id)
    print(f"Canal: #{ch_name} | {len(msgs):,} msgs | {len(review):,} ja revisadas")

    # Define quais usuarios revisar
    if user_id:
        target_ids = {user_id}
    elif top_n:
        ch_suspects = [s for s in suspects if s["channel_id"] == channel_id][:top_n]
        target_ids  = {s["user_id"] for s in ch_suspects}
    else:
        # Todos os suspeitos do canal
        ch_suspects = [s for s in suspects if s["channel_id"] == channel_id]
        target_ids  = {s["user_id"] for s in ch_suspects}

    # Filtra mensagens: so dos suspeitos, so com conteudo, so as nao revisadas
    to_review = [
        m for m in msgs
        if m.get("a") in target_ids
        and m.get("c", "").strip()
        and m["id"] not in review
    ]

    if not to_review:
        print("Nada para revisar.")
        return

    print(f"  {len(to_review):,} mensagens para classificar ({len(target_ids)} usuarios)")
    print(f"  Processando em lotes de 15...\n")

    BATCH = 15
    classified = 0
    skipped    = 0

    for i in range(0, len(to_review), BATCH):
        batch  = to_review[i:i + BATCH]
        results = review_batch(batch, trans, authors)

        if results:
            for r in results:
                mid = r.get("id", "")
                if mid:
                    review[mid] = {
                        "label":      r.get("label", "unknown"),
                        "confidence": r.get("confidence", 0),
                        "reason":     r.get("reason", ""),
                    }
                    classified += 1
        else:
            skipped += len(batch)

        # Checkpoint a cada 5 lotes
        if (i // BATCH + 1) % 5 == 0:
            save_json(ai_review_path(channel_id), review)

        pct = min(100, (i + BATCH) / len(to_review) * 100)
        print(f"  {i + len(batch):,}/{len(to_review):,} ({pct:.0f}%) | classificadas: {classified} | erros: {skipped}", end="\r")
        time.sleep(0.5)  # respeita rate limit do Groq

    save_json(ai_review_path(channel_id), review)

    # Resumo por label
    labels = {}
    for v in review.values():
        l = v.get("label", "?")
        labels[l] = labels.get(l, 0) + 1

    print(f"\n  Classificacao concluida:")
    for label, count in sorted(labels.items(), key=lambda x: -x[1]):
        print(f"    {label:<15} {count:>5}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--top", type=int, default=0, help="Top N suspeitos")
    ap.add_argument("--user", help="User ID especifico")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)

    run(ch_id, args.top, args.user)


if __name__ == "__main__":
    main()
