#!/usr/bin/env python3
"""
02_translate.py — Traduz todas as mensagens de um canal para EN e PT-BR.
Resumível: pula mensagens já traduzidas.

Uso:
  python etl/02_translate.py --channel 1510279576721428612
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    translations_path, messages_path,
)
from deep_translator import GoogleTranslator

_tr_en = GoogleTranslator(source="auto", target="en")
_tr_pt = GoogleTranslator(source="auto", target="pt")


def translate(text: str) -> tuple[str, str]:
    if not text or not text.strip() or len(text.strip()) < 3:
        return "", ""
    # Não traduz se for só emoji/link/menção
    stripped = text.strip()
    if stripped.startswith("http") or stripped.startswith("<"):
        return "", ""
    try:
        en = _tr_en.translate(stripped[:4999]) or ""
        time.sleep(0.1)
        pt = _tr_pt.translate(stripped[:4999]) or ""
        time.sleep(0.1)
        # Se a tradução for praticamente igual ao original, descarta
        if en.lower().strip() == stripped.lower().strip():
            en = ""
        return en, pt
    except Exception as e:
        return "", ""


def run(channel_id: str, force: bool = False):
    msgs = load_jsonl(messages_path(channel_id))
    if not msgs:
        print("Nenhuma mensagem encontrada. Rode 01_extract.py primeiro.")
        return

    trans_file = translations_path(channel_id)
    translations = load_json(trans_file, {})

    # Filtra só as que têm conteúdo e ainda não foram traduzidas
    pending = [
        m for m in msgs
        if m.get("c", "").strip()
        and (force or m["id"] not in translations)
    ]

    total = len(msgs)
    already = total - len(pending)
    print(f"Canal {channel_id}: {total:,} msgs | {already:,} já traduzidas | {len(pending):,} pendentes")

    if not pending:
        print("Nada para traduzir.")
        return

    checkpoint = 0
    for i, m in enumerate(pending):
        en, pt = translate(m["c"])
        if en or pt:
            translations[m["id"]] = {"en": en, "pt": pt}

        if (i + 1) % 50 == 0:
            save_json(trans_file, translations)
            checkpoint = i + 1
            pct = (already + i + 1) / total * 100
            print(f"  {already + i + 1:,}/{total:,} ({pct:.1f}%) traduzidas...", end="\r")

    save_json(trans_file, translations)
    print(f"\n  ✓ {len(pending):,} traduzidas | total no arquivo: {len(translations):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--force", action="store_true", help="Retraduiz mesmo as já traduzidas")
    args = ap.parse_args()

    ch_id = args.channel
    if ch_id in {v: k for k, v in ALL_CHANNELS.items()}:
        ch_id = {v: k for k, v in ALL_CHANNELS.items()}[ch_id]

    run(ch_id, args.force)


if __name__ == "__main__":
    main()
