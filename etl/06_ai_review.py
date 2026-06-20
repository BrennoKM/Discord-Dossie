#!/usr/bin/env python3
"""
06_ai_review.py - Envia mensagens dos suspeitos para o Groq (Llama 3) classificar.

Resumivel:
  - Pula mensagens ja classificadas em ai_review.json.
  - Checkpoint a cada 3 lotes para nao perder progresso.

Uso:
  python etl/05_ai_review.py --channel chat-polish
  python etl/05_ai_review.py --channel chat-polish --top 5
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
    suspects_path,
    log, log_section, log_progress,
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

Seja objetivo. Contexto: servidor de jogo online RPG idle onde ocorreram denuncias de racismo contra jogadores brasileiros."""

BATCH = 15


def review_batch(messages: list[dict], translations: dict, authors: dict) -> list[dict]:
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

    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": "Classifique as mensagens abaixo:\n\n" + "\n".join(lines)},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        raw   = resp.choices[0].message.content.strip()
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        return json.loads(raw[start:end])
    except Exception as e:
        log(f"Erro na chamada Groq: {e}")
        return []


def run(channel_id: str, top_n: int = 0, user_id: str = ""):
    log_section(f"ETAPA 6 - Revisao IA (Groq): canal {channel_id}")

    msgs     = load_jsonl(messages_path(channel_id))
    authors  = load_json(authors_path(), {})
    trans    = load_json(translations_path(channel_id), {})
    review   = load_json(ai_review_path(channel_id), {})
    suspects = load_json(suspects_path(), [])
    ch_name  = ALL_CHANNELS.get(channel_id, channel_id)

    log(f"Canal: #{ch_name}")
    log(f"Mensagens no canal: {len(msgs):,}")
    log(f"Ja revisadas pela IA: {len(review):,}")

    if user_id:
        target_ids = {user_id}
    elif top_n:
        ch_suspects = [s for s in suspects if s["channel_id"] == channel_id][:top_n]
        target_ids  = {s["user_id"] for s in ch_suspects}
    else:
        ch_suspects = [s for s in suspects if s["channel_id"] == channel_id]
        target_ids  = {s["user_id"] for s in ch_suspects}

    log(f"Usuarios-alvo: {len(target_ids)}")

    to_review = [
        m for m in msgs
        if m.get("a") in target_ids
        and m.get("c", "").strip()
        and m["id"] not in review
    ]

    if not to_review:
        log("Nada para revisar. Todos ja foram classificados.")
        # Resumo mesmo assim
        _print_summary(review)
        return

    log(f"Mensagens pendentes de classificacao: {len(to_review):,}")
    log(f"Processando em lotes de {BATCH}...\n")

    classified = 0
    errors     = 0
    batch_num  = 0

    for i in range(0, len(to_review), BATCH):
        batch      = to_review[i:i + BATCH]
        results    = review_batch(batch, trans, authors)
        batch_num += 1

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
            errors += len(batch)
            log(f"  Lote {batch_num}: sem resposta valida ({len(batch)} msgs perdidas)")

        # Checkpoint a cada 3 lotes
        if batch_num % 3 == 0:
            save_json(ai_review_path(channel_id), review)

        log_progress(
            i + len(batch), len(to_review),
            f"classificadas: {classified} | erros: {errors} | lote {batch_num}"
        )
        time.sleep(0.5)

    save_json(ai_review_path(channel_id), review)
    print(flush=True)
    log(f"Concluido: {classified} classificadas | {errors} erros")
    _print_summary(review)


def _print_summary(review: dict):
    labels = {}
    for v in review.values():
        lb = v.get("label", "?")
        labels[lb] = labels.get(lb, 0) + 1

    if not labels:
        return

    log(f"\nResumo das classificacoes:")
    print(f"\n  {'label':<15} {'count':>6}")
    print(f"  {'-'*22}")
    for label, count in sorted(labels.items(), key=lambda x: -x[1]):
        marker = " <--" if label in ("racist", "xenophobic") else ""
        print(f"  {label:<15} {count:>6}{marker}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--top",  type=int, default=0)
    ap.add_argument("--user", help="User ID especifico")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id, args.top, args.user)


if __name__ == "__main__":
    main()
