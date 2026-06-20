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
    ALL_CHANNELS, DATA_DIR, fetch_channel_info, fetch_batch,
    compact, compact_author,
    load_json, save_json, load_jsonl, append_jsonl,
    meta_path, messages_path, authors_path,
    estimate_channel_msgs, save_meta,
    log, log_section, log_progress, ts,
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
    auths_file = authors_path()
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

    # Tenta estimar total de mensagens para ETA
    total_estimate = None if fully_extracted else estimate_channel_msgs(channel_id)
    if total_estimate:
        log(f"Total estimado no canal: {total_estimate:,} mensagens")

    # Janela deslizante para taxa de msgs/s (mesmo modelo do 02_translate)
    WINDOW    = 60
    msg_times = []  # timestamps de cada msg processada

    def current_rate():
        now    = time.time()
        cutoff = now - WINDOW
        recent = [t for t in msg_times if t > cutoff]
        if len(recent) < 2:
            return 0
        return len(recent) / (now - recent[0])

    def eta_str(remaining_msgs: int) -> str:
        rate = current_rate()
        if rate < 0.1:
            return "--:--"
        secs     = remaining_msgs / rate
        m_, s_   = divmod(int(secs), 60)
        h_, m_   = divmod(m_, 60)
        return f"{h_}h{m_:02d}m" if h_ else f"{m_}m{s_:02d}s"

    if fully_extracted:
        # Modo delta: busca mensagens novas (after=last_id, ordem cronologica)
        # Usa max(existing_ids) como cursor real, nao o meta (pode estar desatualizado)
        after         = max(existing_ids) if existing_ids else last_id
        known_total   = len(existing_ids)
        total_new     = 0
        total_estimate = estimate_channel_msgs(channel_id) or known_total
        log(f"Total estimado no canal: {total_estimate:,} mensagens")

        while True:
            batch = fetch_batch(channel_id, after=after)
            if not batch:
                break
            batch.sort(key=lambda m: m["id"])

            new_in_batch = []
            for m in batch:
                if m["id"] not in existing_ids:
                    cm = compact(m)
                    new_in_batch.append(cm)
                    authors[m["author"]["id"]] = compact_author(m["author"])
                    existing_ids.add(m["id"])

            if new_in_batch:
                append_jsonl(msgs_file, new_in_batch)
                total_new += len(new_in_batch)
                now = time.time()
                msg_times.extend([now] * len(new_in_batch))

            after = batch[-1]["id"]
            # Salva cursor a cada lote para retomar se cancelar
            meta["last_id"] = after
            batch_count += 1

            if batch_count % 10 == 0:
                save_json(auths_file, authors)
                save_meta(channel_id, meta)

            rate       = current_rate()
            rlabel     = f"{rate:.0f} msgs/s" if rate >= 1 else f"{rate:.1f} msgs/s"
            current    = known_total + total_new
            total_show = max(total_estimate, current)
            log_progress(current, total_show, f"lote {batch_count} | +{total_new} novas | {rlabel}")

            time.sleep(0.2)
            if len(batch) < 100:
                break

        print(flush=True)
        if total_new:
            meta["last_id"] = max(existing_ids)
            log(f"Delta concluido: +{total_new} mensagens novas | total: {len(existing_ids):,}")
        else:
            log(f"Nenhuma mensagem nova encontrada.")

    else:
        # Extracao inicial: pagina para tras
        # Usa o ID mais antigo REAL do arquivo, nao o meta (que pode estar desatualizado)
        before = min(existing_ids) if existing_ids else oldest_id
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
                now = time.time()
                msg_times.extend([now] * len(new_in_batch))

            oldest_in_batch  = min(m["id"] for m in batch)
            before           = oldest_in_batch
            meta["oldest_id"] = oldest_in_batch

            if not meta.get("last_id"):
                meta["last_id"] = max(m["id"] for m in batch)

            batch_count += 1

            current    = len(existing_ids)
            total_show = total_estimate or current
            remaining  = (total_estimate - current) if total_estimate else 0
            eta        = f" | ETA: {eta_str(remaining)}" if total_estimate else ""
            rate       = current_rate()
            rate_label = f"{rate:.0f} msgs/s" if rate >= 1 else f"{rate:.1f} msgs/s"
            log_progress(current, total_show,
                         f"lote {batch_count} | {rate_label}{eta}")

            # Salva meta e autores a cada 10 lotes (checkpoint)
            if batch_count % 10 == 0:
                save_json(auths_file, authors)
                save_meta(channel_id, meta)

            time.sleep(0.2)

            if limit and total_new >= limit:
                log(f"\nLimite de {limit} mensagens atingido.")
                break

        if not meta.get("last_id") and existing_ids:
            meta["last_id"] = max(existing_ids)

    # Salva estado final
    save_json(auths_file, authors)
    meta["total_msgs"] = len(existing_ids)
    save_meta(channel_id, meta)

    print(flush=True)
    log(f"Concluido: +{total_new} novas | total: {len(existing_ids):,} | autores: {len(authors):,}")
    log(f"Estado salvo em: {meta_path(channel_id)}")
    return total_new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=None, help="ID ou nome do canal (omitir = todos)")
    ap.add_argument("--limit",   type=int, default=0)
    args = ap.parse_args()

    if args.channel:
        ch_id, ch_name = resolve_channel(args.channel)
        extract(ch_id, ch_name, args.limit)
    else:
        skip   = set(load_json(DATA_DIR / "channels_skip.json", []))
        channels = [(ch_id, ch_name) for ch_id, ch_name in ALL_CHANNELS.items()
                    if ch_name not in skip]
        log(f"Modo todos os canais: {len(channels)} canais ({len(ALL_CHANNELS)-len(channels)} ignorados)")
        for i, (ch_id, ch_name) in enumerate(channels, 1):
            log(f"\n[{i}/{len(channels)}] #{ch_name}")
            extract(ch_id, ch_name, args.limit)


if __name__ == "__main__":
    main()
