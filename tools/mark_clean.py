#!/usr/bin/env python3
"""
mark_clean.py — Marca mensagens como "clean" manualmente, excluindo-as do SPA.

Uso:
  venv/bin/python tools/mark_clean.py <msg_id> [<msg_id> ...]

Exemplos:
  venv/bin/python tools/mark_clean.py 128582138128321 18238128381238
  venv/bin/python tools/mark_clean.py $(cat ids.txt)

O que faz:
  - Encontra o canal de cada msg_id varrendo os messages.jsonl
  - Grava label="clean" + manual=true no context_review.json do canal
  - Na proxima execucao de 08_report.py o caso e excluido automaticamente
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import ALL_CHANNELS, load_jsonl, load_json, save_json, messages_path, channel_dir, ai_review_path
from dotenv import load_dotenv
load_dotenv()


def context_review_path(ch_id: str) -> Path:
    return channel_dir(ch_id) / "context_review.json"


def find_channel(msg_id: str, index_map: dict) -> str | None:
    """Acha o canal: index → ai_review → context_review → jsonl (fallback lento)."""
    if msg_id in index_map:
        return index_map[msg_id]
    for ch_id in ALL_CHANNELS:
        ai  = load_json(ai_review_path(ch_id), {})
        cr  = load_json(context_review_path(ch_id), {})
        if msg_id in ai or msg_id in cr:
            return ch_id
    for ch_id in ALL_CHANNELS:
        if any(m["id"] == msg_id for m in load_jsonl(messages_path(ch_id))):
            return ch_id
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("msg_ids", nargs="+", help="IDs das mensagens a marcar como clean")
    parser.add_argument("--reason", default="validacao manual", help="Motivo (para registro)")
    args = parser.parse_args()

    msg_ids = [m.strip() for m in args.msg_ids if m.strip()]
    print(f"Marcando {len(msg_ids)} mensagem(ns) como clean...\n")

    # Mapa rápido msg_id → channel_id via cases_index.json
    index_path = Path(__file__).parent.parent / "web" / "data" / "cases_index.json"
    index_map = {}
    if index_path.exists():
        for c in load_json(index_path, []):
            if "channel_id" in c:
                index_map[c["msg_id"]] = c["channel_id"]

    by_channel: dict[str, list[str]] = {}
    not_found = []

    for msg_id in msg_ids:
        ch_id = find_channel(msg_id, index_map)
        if ch_id:
            by_channel.setdefault(ch_id, []).append(msg_id)
            print(f"  ✓ {msg_id}  →  #{ALL_CHANNELS[ch_id]}")
        else:
            not_found.append(msg_id)
            print(f"  ✗ {msg_id}  →  não encontrado em nenhum canal")

    if not_found:
        print(f"\nAVISO: {len(not_found)} id(s) não encontrado(s) — verifique se o canal foi coletado.")

    # Grava nos context_review.json
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0
    for ch_id, ids in by_channel.items():
        cr_path = context_review_path(ch_id)
        cr = load_json(cr_path, {})
        for mid in ids:
            cr[mid] = {
                "label":    "clean",
                "manual":   True,
                "reason":   args.reason,
                "marked_at": now,
            }
        save_json(cr_path, cr)
        saved += len(ids)

    print(f"\n{saved} marcado(s). Rode 08_report.py para atualizar o SPA.")


if __name__ == "__main__":
    main()
