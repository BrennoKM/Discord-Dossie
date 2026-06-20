#!/usr/bin/env python3
"""
01_extract.py - Baixa todas as mensagens de um canal.

Resumivel:
  - Primeira execucao: pagina do mais recente ao mais antigo, salva cada lote.
  - Se interrompido: retoma do ponto onde parou (oldest_id no meta.json).
  - Execucoes seguintes: busca so mensagens novas (after=last_id).

Uso:
  python etl/01_extract.py --channel chat-polish
  python etl/01_extract.py --channel 1510279576721428612
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
    meta_path, messages_path, authors_path,
    log, log_section, log_progress,
)


def resolve_channel(arg: str) -> tuple[str, str]:
    if arg in ALL_CHANNELS:
        return arg, ALL_CHANNELS[arg]
    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    if arg in by_name:
        return by_name[arg], arg
    try:
        info = fetch_channel_info(arg)
        name = info.get("name", arg)
        return arg, name
    except Exception:
        print(f"Canal '{arg}' nao encontrado.")
        sys.exit(1)


def extract(channel_id: str, channel_name: str, limit: int = 0):
    log_section(f"ETAPA 1 - Extracao: #{channel_name}")

    meta       = load_json(meta_path(channel_id), {})
    msgs_file  = messages_path(channel_id)
    auths_file = authors_path(channel_id)
    authors    = load_json(auths_file, {})

    # Carrega IDs ja existentes para deduplicacao
    existing_msgs = load_jsonl(msgs_file)
    existing_ids  = {m["id"] for m in existing_msgs}

    fully_extracted = meta.get("fully_extracted", False)
    oldest_id       = meta.get("oldest_id")
    last_id         = meta.get("last_id")

    log(f"Mensagens ja baixadas: {len(existing_ids):,}")
    log(f"Autores conhecidos: {len(authors):,}")

    if fully_extracted:
        log(f"Canal ja extraido por completo anteriormente.")
        log(f"Modo delta: buscando mensagens novas apos ID {last_id}")
    elif oldest_id:
        log(f"Retomando extracao inicial a partir do ID {oldest_id}")
    else:
        log(f"Iniciando extracao completa do canal (do mais recente ao mais antigo)...")

    total_new   = 0
    batch_count = 0

    if fully_extracted:
        # Modo delta: busca so o novo
        new_msgs = []
        after    = last_id
        while True:
            batch = fetch_batch(channel_id, after=after)
            if not batch:
                break
            batch.sort(key=lambda m: m["id"])
            for m in batch:
                if m["id"] not in existing_ids:
                    cm = compact(m)
                    new_msgs.append(cm)
                    authors[m["author"]["id"]] = compact_author(m["author"])
                    existing_ids.add(m["id"])
            after = batch[-1]["id"]
            batch_count += 1
            log(f"  Lote {batch_count}: +{len(batch)} msgs | novas acumuladas: {len(new_msgs)}")
            time.sleep(0.4)
            if len(batch) < 100:
                break

        if new_msgs:
            new_msgs.sort(key=lambda m: m["id"])
            append_jsonl(msgs_file, new_msgs)
            total_new = len(new_msgs)
            meta["last_id"] = new_msgs[-1]["id"]
            log(f"Delta salvo: +{total_new} mensagens novas")
        else:
            log(f"Nenhuma mensagem nova encontrada.")

    else:
        # Extracao inicial: pagina para tras
        before = oldest_id  # None na primeira vez
        while True:
            batch = fetch_batch(channel_id, before=before)
            if not batch:
                meta["fully_extracted"] = True
                log(f"\nInicio do canal atingido. Extracao completa!")
                break

            new_in_batch = []
            for m in batch:
                if m["id"] not in existing_ids:
                    cm = compact(m)
                    new_in_batch.append(cm)
                    authors[m["author"]["id"]] = compact_author(m["author"])
                    existing_ids.add(m["id"])

            if new_in_batch:
                append_jsonl(msgs_file, new_in_batch)
                total_new  += len(new_in_batch)

            oldest_in_batch  = min(m["id"] for m in batch)
            before           = oldest_in_batch
            meta["oldest_id"] = oldest_in_batch

            if not meta.get("last_id"):
                meta["last_id"] = max(m["id"] for m in batch)

            batch_count += 1
            log_progress(len(existing_ids), len(existing_ids), f"lote {batch_count} | +{len(new_in_batch)} novas")

            # Salva meta e autores a cada 10 lotes (checkpoint)
            if batch_count % 10 == 0:
                save_json(auths_file, authors)
                save_json(meta_path(channel_id), meta)

            time.sleep(0.4)

            if limit and total_new >= limit:
                log(f"\nLimite de {limit} mensagens atingido.")
                break

        if not meta.get("last_id") and existing_ids:
            meta["last_id"] = max(existing_ids)

    # Salva estado final
    save_json(auths_file, authors)
    meta.update({
        "channel_id":   channel_id,
        "channel_name": channel_name,
        "guild_id":     "1427722762809770126",
        "total_msgs":   len(existing_ids),
    })
    save_json(meta_path(channel_id), meta)

    print(flush=True)
    log(f"Concluido: +{total_new} novas | total: {len(existing_ids):,} | autores: {len(authors):,}")
    log(f"Estado salvo em: {meta_path(channel_id)}")
    return total_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="ID ou nome do canal")
    ap.add_argument("--limit",   type=int, default=0)
    args = ap.parse_args()
    ch_id, ch_name = resolve_channel(args.channel)
    extract(ch_id, ch_name, args.limit)


if __name__ == "__main__":
    main()
