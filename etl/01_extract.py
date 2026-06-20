#!/usr/bin/env python3
"""
01_extract.py — Baixa todas as mensagens de um canal.
Resumível: para onde parou e continua de onde saiu.

Uso:
  python etl/01_extract.py --channel 1510279576721428612
  python etl/01_extract.py --channel chat-polish        # pelo nome também
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, fetch_channel_info, fetch_batch,
    compact, compact_author,
    load_json, save_json, load_jsonl, append_jsonl,
    meta_path, messages_path, authors_path, channel_dir,
)


def resolve_channel(arg: str) -> tuple[str, str]:
    """Aceita ID ou nome do canal."""
    if arg in ALL_CHANNELS:
        return arg, ALL_CHANNELS[arg]
    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    if arg in by_name:
        return by_name[arg], arg
    # Tenta buscar info direto na API
    try:
        info = fetch_channel_info(arg)
        name = info.get("name", arg)
        ALL_CHANNELS[arg] = name
        return arg, name
    except Exception:
        print(f"Canal '{arg}' não encontrado.")
        sys.exit(1)


def extract(channel_id: str, channel_name: str, limit: int = 0):
    meta = load_json(meta_path(channel_id), {})
    msgs_file  = messages_path(channel_id)
    auths_file = authors_path(channel_id)

    authors = load_json(auths_file, {})
    existing = {m["id"] for m in load_jsonl(msgs_file)}

    # Estado de extração:
    # - fully_extracted: já chegamos ao início do canal
    # - oldest_id: ID mais antigo que temos (para continuar extração inicial)
    # - last_id: ID mais recente (para buscar novidades depois)
    fully_extracted = meta.get("fully_extracted", False)
    oldest_id = meta.get("oldest_id")
    last_id   = meta.get("last_id")

    total_new = 0
    print(f"Canal: #{channel_name} ({channel_id})")
    print(f"  Mensagens existentes: {len(existing):,}")

    if fully_extracted:
        # Modo delta: busca só o que é novo
        print(f"  Modo delta — buscando mensagens após ID {last_id}")
        new_msgs = []
        after = last_id
        while True:
            batch = fetch_batch(channel_id, after=after)
            if not batch:
                break
            batch.sort(key=lambda m: m["id"])
            for m in batch:
                if m["id"] not in existing:
                    cm = compact(m)
                    new_msgs.append(cm)
                    authors[m["author"]["id"]] = compact_author(m["author"])
                    existing.add(m["id"])
            after = batch[-1]["id"]
            print(f"  +{len(new_msgs)} novas...", end="\r")
            time.sleep(0.4)
            if len(batch) < 100:
                break

        if new_msgs:
            new_msgs.sort(key=lambda m: m["id"])
            appended = append_jsonl(msgs_file, new_msgs)
            total_new += appended
            meta["last_id"] = new_msgs[-1]["id"]

    else:
        # Extração inicial: vai do mais recente para o mais antigo
        before = oldest_id  # None na primeira vez → começa do fim
        print(f"  Extração inicial{' (continuando)' if oldest_id else ''}...")

        batch_count = 0
        while True:
            batch = fetch_batch(channel_id, before=before)
            if not batch:
                # Chegamos ao início
                meta["fully_extracted"] = True
                print(f"\n  Início do canal atingido.")
                break

            new_in_batch = []
            for m in batch:
                if m["id"] not in existing:
                    cm = compact(m)
                    new_in_batch.append(cm)
                    authors[m["author"]["id"]] = compact_author(m["author"])
                    existing.add(m["id"])

            if new_in_batch:
                append_jsonl(msgs_file, new_in_batch)
                total_new += len(new_in_batch)

            oldest_in_batch = min(m["id"] for m in batch)
            before = oldest_in_batch
            meta["oldest_id"] = oldest_in_batch

            if not meta.get("last_id"):
                meta["last_id"] = max(m["id"] for m in batch)

            batch_count += 1
            print(f"  {len(existing):,} mensagens ({total_new:,} novas)...", end="\r")
            time.sleep(0.4)

            if limit and total_new >= limit:
                print(f"\n  Limite de {limit} atingido.")
                break

        if not meta.get("last_id") and existing:
            meta["last_id"] = max(existing)

    # Salva autores e meta
    save_json(auths_file, authors)

    meta.update({
        "channel_id":   channel_id,
        "channel_name": channel_name,
        "guild_id":     "1427722762809770126",
        "total_msgs":   len(existing),
    })
    save_json(meta_path(channel_id), meta)

    print(f"\n  ✓ +{total_new} novas | total: {len(existing):,} | autores: {len(authors):,}")
    return total_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="ID ou nome do canal")
    ap.add_argument("--limit", type=int, default=0, help="Limite de msgs novas (0=sem limite)")
    args = ap.parse_args()

    ch_id, ch_name = resolve_channel(args.channel)
    extract(ch_id, ch_name, args.limit)


if __name__ == "__main__":
    main()
