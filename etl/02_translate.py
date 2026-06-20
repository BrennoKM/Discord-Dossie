#!/usr/bin/env python3
"""
02_translate.py - Traduz todas as mensagens de um canal para EN e PT-BR.

Resumivel:
  - Pula mensagens ja presentes em translations.json.
  - Salva checkpoint a cada 20 traducoes para nao perder progresso.

Uso:
  python etl/02_translate.py --channel chat-polish
  python etl/02_translate.py --channel chat-polish --force  # retraduzi tudo
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    translations_path, messages_path,
    log, log_section, log_progress,
)
from deep_translator import GoogleTranslator

_tr_en = GoogleTranslator(source="auto", target="en")
_tr_pt = GoogleTranslator(source="auto", target="pt")

CHECKPOINT_EVERY = 20


def translate(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped or len(stripped) < 3:
        return "", ""
    if stripped.startswith("http") or stripped.startswith("<@") or stripped.startswith("<:"):
        return "", ""
    try:
        en = _tr_en.translate(stripped[:4999]) or ""
        time.sleep(0.1)
        pt = _tr_pt.translate(stripped[:4999]) or ""
        time.sleep(0.1)
        # Descarta se identico ao original (ja era EN ou PT)
        if en.lower().strip() == stripped.lower().strip():
            en = ""
        return en, pt
    except Exception as e:
        log(f"Erro na traducao: {e}")
        return "", ""


def run(channel_id: str, force: bool = False):
    log_section(f"ETAPA 2 - Traducao: canal {channel_id}")

    msgs = load_jsonl(messages_path(channel_id))
    if not msgs:
        log("Nenhuma mensagem encontrada. Rode 01_extract.py primeiro.")
        return

    trans_file   = translations_path(channel_id)
    translations = load_json(trans_file, {})

    # Pendentes: tem conteudo e ainda nao foram traduzidas (ou force=True)
    pending = [
        m for m in msgs
        if m.get("c", "").strip()
        and (force or m["id"] not in translations)
    ]

    total   = len(msgs)
    already = total - len(pending)

    log(f"Total de mensagens no canal: {total:,}")
    log(f"Ja traduzidas (pulando): {already:,}")
    log(f"Pendentes para traducao: {len(pending):,}")

    if not pending:
        log("Nada para traduzir. Tudo ja esta atualizado.")
        return

    translated_now = 0
    errors         = 0

    for i, m in enumerate(pending):
        en, pt = translate(m["c"])

        if en or pt:
            translations[m["id"]] = {"en": en, "pt": pt}
            translated_now += 1
        else:
            errors += 1

        # Checkpoint a cada N traducoes
        if (i + 1) % CHECKPOINT_EVERY == 0:
            save_json(trans_file, translations)
            log_progress(
                already + i + 1, total,
                f"traduzidas: {translated_now} | erros: {errors}"
            )

    # Salva final
    save_json(trans_file, translations)
    print(flush=True)
    log(f"Concluido: {translated_now} novas traducoes | erros: {errors}")
    log(f"Total em translations.json: {len(translations):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--force", action="store_true", help="Retraduzi mesmo as ja traduzidas")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id, args.force)


if __name__ == "__main__":
    main()
