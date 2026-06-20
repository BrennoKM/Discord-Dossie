#!/usr/bin/env python3
"""
04_deep_fetch.py - Busca o historico completo dos suspeitos.

Dois modos:
  Padrao (rapido): usa search API do Discord, retorna so msgs do usuario.
    - Mesmo endpoint da busca da interface
    - ~5000 resultados por usuario
    - Nao preenche o historico completo dos canais

  --full-scan (lento): varre todos os canais mensagem por mensagem.
    - Preenche data/channels/*/messages.jsonl com tudo
    - Util para ter o historico completo dos canais para futuras analises

Resumivel em ambos os modos.

Uso:
  python etl/04_deep_fetch.py --top 10
  python etl/04_deep_fetch.py --user xbiedro
  python etl/04_deep_fetch.py --top 10 --full-scan
"""

import argparse
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    GUILD_ID, ALL_CHANNELS, DISCORD_TOKEN,
    fetch_batch, compact, compact_author,
    load_json, save_json, load_jsonl, append_jsonl,
    messages_path, authors_path, translations_path, meta_path,
    suspects_path, DATA_DIR,
    log, log_section, log_progress,
)

STATE_FILE    = DATA_DIR / "deep_fetch_state.json"
PROFILES_FILE = DATA_DIR / "suspect_profiles.json"
SEARCH_URL    = f"https://discord.com/api/v10/guilds/{GUILD_ID}/messages/search"
DISCORD_HEADERS = {
    "Authorization": DISCORD_TOKEN,
    "User-Agent":    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
}
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/t"
TR_HEADERS    = {"User-Agent": "Mozilla/5.0"}


# ── traducao ──────────────────────────────────────────────────────────────────

def _batch_translate(texts: list[str], target: str) -> list[str]:
    try:
        data = [("q", t[:500]) for t in texts]
        r = requests.post(
            TRANSLATE_URL,
            params={"client": "gtx", "sl": "auto", "tl": target},
            data=data, headers=TR_HEADERS, timeout=15,
        )
        r.raise_for_status()
        result = r.json()
        return [item if isinstance(item, str) else (item[0] if item else "") for item in result]
    except Exception:
        return [""] * len(texts)


def translate_msgs(msgs: list[dict], channel_id: str, username: str) -> int:
    trans_file = translations_path(channel_id)
    trans      = load_json(trans_file, {})
    pending    = [m for m in msgs if m["id"] not in trans and m.get("c", "").strip()]
    if not pending:
        return 0

    ch_name = ALL_CHANNELS.get(channel_id, channel_id)
    log(f"    Traduzindo {len(pending)} msgs de @{username} em #{ch_name}...")
    BATCH = 15
    translated = 0

    for i in range(0, len(pending), BATCH):
        batch = pending[i:i + BATCH]
        texts = [m["c"][:500] for m in batch]
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_en = ex.submit(_batch_translate, texts, "en")
            fut_pt = ex.submit(_batch_translate, texts, "pt")
            ens, pts = fut_en.result(), fut_pt.result()

        for m, en, pt in zip(batch, ens, pts):
            if en or pt:
                trans[m["id"]] = {"en": en, "pt": pt}
                translated += 1

        save_json(trans_file, trans)
        log_progress(min(i + BATCH, len(pending)), len(pending), f"traduzidas: {translated}")
        time.sleep(0.3)

    print(flush=True)
    return translated


# ── modo rapido: search API ───────────────────────────────────────────────────

def search_user_messages(uid: str, username: str) -> dict[str, list]:
    """Busca mensagens do usuario via search API (rapido)."""
    by_channel: dict[str, list] = {}
    offset  = 0
    total   = None
    fetched = 0
    authors = load_json(authors_path(), {})

    log(f"  [search] @{username}...")

    while True:
        try:
            r = requests.get(
                SEARCH_URL,
                headers=DISCORD_HEADERS,
                params={"author_id": uid, "offset": offset},
                timeout=15,
            )
            if r.status_code == 429:
                wait = float(r.json().get("retry_after", 5)) + 0.5
                log(f"  Rate limit, aguardando {wait:.1f}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log(f"  Erro na busca: {e}")
            break

        if total is None:
            total = data.get("total_results", 0)
            log(f"  Total encontrado: {total:,} mensagens")
            if total == 0:
                break

        for context in data.get("messages", []):
            for raw in context:
                if raw.get("author", {}).get("id") != uid:
                    continue
                cm    = compact(raw)
                ch_id = raw.get("channel_id", "")
                authors[raw["author"]["id"]] = compact_author(raw["author"])
                by_channel.setdefault(ch_id, []).append(cm)
                fetched += 1

        log_progress(fetched, total, f"offset {offset}")

        if fetched >= total or not data.get("messages"):
            break

        offset += 25
        time.sleep(0.5)

    print(flush=True)
    save_json(authors_path(), authors)
    log(f"  @{username}: {fetched} msgs em {len(by_channel)} canais")
    return by_channel


# ── modo completo: varredura de canais ────────────────────────────────────────

def _load_skip_names() -> set:
    skip_file = DATA_DIR / "channels_skip.json"
    if skip_file.exists():
        return set(load_json(skip_file, []))
    return set()


def full_scan_all(targets: list, channels: list, state: dict) -> dict[str, dict[str, list]]:
    """
    Varre cada canal UMA vez e faz o match contra todos os suspeitos simultaneamente.
    Retorna {uid: {channel_id: [msgs]}}.
    """
    skip_names = _load_skip_names()
    user_ids   = {s["user_id"] for s in targets}
    active     = [ch for ch in channels if ALL_CHANNELS.get(ch, ch) not in skip_names]
    total_ch   = len(active)

    # Resultado acumulado por usuario
    by_user: dict[str, dict[str, list]] = {s["user_id"]: {} for s in targets}

    done_ch = sum(1 for ch in active if state.get(f"full_ch:{ch}") == "done")
    log(f"Canais: {done_ch}/{total_ch} ja varridos | {total_ch - done_ch} restantes | {len(channels)-total_ch} ignorados")

    for idx, ch_id in enumerate(active, 1):
        ch_name = ALL_CHANNELS.get(ch_id, ch_id)

        if state.get(f"full_ch:{ch_id}") == "done":
            # Canal ja varrido — filtra do arquivo existente
            existing = load_jsonl(messages_path(ch_id))
            for m in existing:
                if m["a"] in user_ids:
                    by_user[m["a"]].setdefault(ch_id, []).append(m)
            found = sum(1 for m in existing if m["a"] in user_ids)
            log(f"  [{idx}/{total_ch}] #{ch_name}: ja varrido | {found} msgs dos suspeitos [pulando]")
            continue

        # Varre o canal
        msgs_file    = messages_path(ch_id)
        meta         = load_json(meta_path(ch_id), {})
        authors      = load_json(authors_path(), {})
        existing_ids = {m["id"] for m in load_jsonl(msgs_file)}
        before       = meta.get("oldest_id")
        scanned      = 0
        saved        = 0
        found        = 0

        known_total = meta.get("total_msgs")
        total_str   = f"/{known_total:,}" if known_total else "/?"
        log(f"  [{idx}/{total_ch}] #{ch_name}: varrendo... (total conhecido: {known_total:,} msgs)" if known_total else f"  [{idx}/{total_ch}] #{ch_name}: varrendo... (total desconhecido)")

        while True:
            batch = fetch_batch(channel_id=ch_id, before=before)
            if not batch:
                meta["fully_extracted"] = True
                break

            new_batch = []
            for m in batch:
                uid = m["author"]["id"]
                authors[uid] = compact_author(m["author"])
                cm = compact(m)
                if cm["id"] not in existing_ids:
                    new_batch.append(cm)
                    existing_ids.add(cm["id"])
                    saved += 1
                if uid in user_ids:
                    by_user[uid].setdefault(ch_id, []).append(cm)
                    found += 1

            if new_batch:
                append_jsonl(msgs_file, new_batch)

            before   = min(m["id"] for m in batch)
            scanned += len(batch)
            meta["oldest_id"]  = before
            meta["total_msgs"] = len(existing_ids)

            print(f"\r  [{idx}/{total_ch}] #{ch_name}: {scanned:,}{total_str} varridas | {saved:,} salvas | {found} dos suspeitos", end="", flush=True)

            if scanned % 5000 < 100:
                save_json(authors_path(), authors)
                save_json(meta_path(ch_id), meta)

            time.sleep(0.4)
            if len(batch) < 100:
                meta["fully_extracted"] = True
                break

        if existing_ids:
            meta["last_id"] = max(existing_ids)
        meta["total_msgs"] = len(existing_ids)
        save_json(meta_path(ch_id), meta)
        save_json(authors_path(), authors)

        state[f"full_ch:{ch_id}"] = "done"
        save_json(STATE_FILE, state)
        print(f"\r  [{idx}/{total_ch}] #{ch_name}: concluido — {scanned:,} varridas | {found} dos suspeitos", flush=True)

    return by_user


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top",       type=int, default=10)
    ap.add_argument("--user",      help="Username especifico")
    ap.add_argument("--full-scan", action="store_true",
                    help="Varre todos os canais (lento, preenche historico completo)")
    args = ap.parse_args()

    mode = "varredura completa" if args.full_scan else "search API (rapido)"
    log_section(f"ETAPA 4 - Deep Fetch [{mode}]")

    suspects = load_json(suspects_path(), [])
    if not suspects:
        log("suspects.json vazio. Rode 03_detect.py primeiro.")
        sys.exit(1)

    state = load_json(STATE_FILE, {})

    if args.user:
        targets = [s for s in suspects if s["username"].lower() == args.user.lower()]
        if not targets:
            log(f"@{args.user} nao encontrado em suspects.json")
            sys.exit(1)
    else:
        targets = suspects[:args.top]

    channels = list(ALL_CHANNELS.keys())
    log(f"Suspeitos: {len(targets)} | Modo: {mode}")
    log(f"Usuarios: {', '.join('@' + s['username'] for s in targets)}")

    profiles = load_json(PROFILES_FILE, {})

    if args.full_scan:
        # Varre cada canal uma vez contra todos os suspeitos
        by_user = full_scan_all(targets, channels, state)

        # Traduz e salva perfis
        for s in targets:
            uid      = s["user_id"]
            username = s["username"]
            by_channel = by_user.get(uid, {})

            log(f"\n  Traduzindo msgs de @{username}...")
            total_trans = 0
            for ch_id, msgs in by_channel.items():
                if msgs:
                    total_trans += translate_msgs(msgs, ch_id, username)

            profiles[uid] = {
                "user_id":   uid,
                "username":  username,
                "display":   s["display"],
                "mode":      "full_scan",
                "channels": {
                    ch_id: {"name": ALL_CHANNELS.get(ch_id, ch_id), "msg_count": len(msgs)}
                    for ch_id, msgs in by_channel.items() if msgs
                },
                "total_msgs":  sum(len(v) for v in by_channel.values()),
                "total_trans": total_trans,
            }
            save_json(PROFILES_FILE, profiles)
            log(f"  @{username}: {profiles[uid]['total_msgs']} msgs em {len(by_channel)} canais | {total_trans} traduzidas")

    else:
        for s in targets:
            uid      = s["user_id"]
            username = s["username"]

            if state.get(f"search:{uid}") == "done":
                log(f"\n  @{username}: ja processado [pulando]")
                continue

            log(f"\n  @{username} ({uid})")
            by_channel = search_user_messages(uid, username)
            for ch_id, msgs in by_channel.items():
                if msgs:
                    append_jsonl(messages_path(ch_id), msgs)

            total_trans = 0
            for ch_id, msgs in by_channel.items():
                if msgs:
                    total_trans += translate_msgs(msgs, ch_id, username)

            profiles[uid] = {
                "user_id":   uid,
                "username":  username,
                "display":   s["display"],
                "mode":      "search",
                "channels": {
                    ch_id: {"name": ALL_CHANNELS.get(ch_id, ch_id), "msg_count": len(msgs)}
                    for ch_id, msgs in by_channel.items() if msgs
                },
                "total_msgs":  sum(len(v) for v in by_channel.values()),
                "total_trans": total_trans,
            }
            save_json(PROFILES_FILE, profiles)
            state[f"search:{uid}"] = "done"
            save_json(STATE_FILE, state)
            log(f"  @{username}: {profiles[uid]['total_msgs']} msgs | {total_trans} traduzidas")

    log(f"\nConcluido. Perfis em data/suspect_profiles.json")
    log(f"Proxima etapa: python etl/05_pre_filter.py --channel chat-polish --dry-run")


if __name__ == "__main__":
    main()
