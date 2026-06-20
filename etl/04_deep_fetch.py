#!/usr/bin/env python3
"""
04_deep_fetch.py - Busca TODO o historico dos top suspeitos em TODOS os canais.

Resumivel:
  - Rastreia quais (usuario, canal) ja foram varridos em data/deep_fetch_state.json.
  - Nao re-varre o que ja foi processado.
  - Mensagens salvas diretamente nos arquivos do canal (sem duplicacao).
  - Traduz automaticamente as mensagens novas dos suspeitos.

Uso:
  python etl/04_deep_fetch.py --top 10
  python etl/04_deep_fetch.py --user xbiedro
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, fetch_batch, compact, compact_author,
    load_json, save_json, load_jsonl, append_jsonl,
    messages_path, authors_path, translations_path, meta_path,
    suspects_path, DATA_DIR,
    log, log_section, log_progress,
)
from deep_translator import GoogleTranslator

_tr_en = GoogleTranslator(source="auto", target="en")
_tr_pt = GoogleTranslator(source="auto", target="pt")

STATE_FILE = DATA_DIR / "deep_fetch_state.json"


def translate(text: str) -> tuple[str, str]:
    if not text or len(text.strip()) < 3:
        return "", ""
    try:
        en = _tr_en.translate(text[:4999]) or ""
        time.sleep(0.12)
        pt = _tr_pt.translate(text[:4999]) or ""
        time.sleep(0.12)
        return en, pt
    except Exception:
        return "", ""


def scan_channel_for_users(channel_id: str, user_ids: set) -> dict:
    """
    Varre o canal inteiro, salva todas as msgs no arquivo do canal
    e retorna {user_id: [msgs]} so para os usuarios alvo.
    """
    ch_name   = ALL_CHANNELS.get(channel_id, channel_id)
    msgs_file = messages_path(channel_id)
    meta      = load_json(meta_path(channel_id), {})
    authors   = load_json(authors_path(channel_id), {})

    existing_ids = {m["id"] for m in load_jsonl(msgs_file)}
    result       = defaultdict(list)
    before       = None
    scanned      = 0
    saved        = 0

    log(f"  Varrendo #{ch_name} ({channel_id})...")

    while True:
        batch = fetch_batch(channel_id, before=before)
        if not batch:
            meta["fully_extracted"] = True
            log(f"  #{ch_name}: fim do canal atingido.")
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
                result[uid].append(cm)

        if new_batch:
            append_jsonl(msgs_file, new_batch)

        before   = min(m["id"] for m in batch)
        scanned += len(batch)

        log_progress(scanned, scanned, f"#{ch_name}: {scanned:,} varridas | {saved} salvas | {sum(len(v) for v in result.values())} dos suspeitos")

        # Checkpoint a cada 50 lotes
        if (scanned // 100) % 50 == 0:
            save_json(authors_path(channel_id), authors)
            meta["oldest_id"]  = before
            meta["total_msgs"] = len(existing_ids)
            save_json(meta_path(channel_id), meta)

        time.sleep(0.4)
        if len(batch) < 100:
            meta["fully_extracted"] = True
            log(f"\n  #{ch_name}: extracao completa.")
            break

    if existing_ids:
        meta["last_id"]    = max(existing_ids)
    meta["total_msgs"]     = len(existing_ids)
    save_json(meta_path(channel_id), meta)
    save_json(authors_path(channel_id), authors)

    found = sum(len(v) for v in result.values())
    print(flush=True)
    log(f"  #{ch_name}: {scanned:,} varridas | {saved} novas salvas | {found} msgs dos suspeitos")
    return dict(result)


def translate_user_msgs(user_msgs: list, channel_id: str, username: str) -> int:
    """Traduz mensagens de um usuario que ainda nao tem traducao."""
    trans_file = translations_path(channel_id)
    trans      = load_json(trans_file, {})
    changed    = 0

    pending = [m for m in user_msgs if m["id"] not in trans and m.get("c", "").strip()]
    if not pending:
        return 0

    log(f"    Traduzindo {len(pending)} mensagens de @{username} em {channel_id}...")
    for i, m in enumerate(pending):
        en, pt = translate(m["c"])
        if en or pt:
            trans[m["id"]] = {"en": en, "pt": pt}
            changed += 1
        if (i + 1) % 20 == 0:
            save_json(trans_file, trans)
            log_progress(i + 1, len(pending), f"traduzidas: {changed}")

    save_json(trans_file, trans)
    print(flush=True)
    return changed


def deep_fetch_user(uid: str, username: str, channels: list, state: dict) -> dict:
    log(f"\n  @{username} ({uid})")
    all_msgs     = {}
    total_trans  = 0

    for ch_id in channels:
        ch_name  = ALL_CHANNELS.get(ch_id, ch_id)
        state_key = f"{uid}:{ch_id}"

        if state.get(state_key) == "done":
            existing = [m for m in load_jsonl(messages_path(ch_id)) if m["a"] == uid]
            log(f"    #{ch_name}: ja varrido ({len(existing)} msgs do usuario) [pulando]")
            all_msgs[ch_id] = existing
            continue

        # Verifica se o canal ja foi totalmente extraido
        meta = load_json(meta_path(ch_id), {})
        if meta.get("fully_extracted"):
            existing = [m for m in load_jsonl(messages_path(ch_id)) if m["a"] == uid]
            log(f"    #{ch_name}: canal ja extraido, filtrando ({len(existing)} msgs do usuario)")
            all_msgs[ch_id] = existing
        else:
            fetched = scan_channel_for_users(ch_id, {uid})
            all_msgs[ch_id] = fetched.get(uid, [])

        state[state_key] = "done"
        save_json(STATE_FILE, state)

        # Traduz as mensagens deste usuario neste canal
        if all_msgs[ch_id]:
            translated = translate_user_msgs(all_msgs[ch_id], ch_id, username)
            total_trans += translated

    total = sum(len(v) for v in all_msgs.values())
    log(f"    Total @{username}: {total} msgs em {len([c for c in all_msgs if all_msgs[c]])} canais | {total_trans} traduzidas agora")
    return all_msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top",      type=int, default=10)
    ap.add_argument("--user",     help="Username especifico")
    ap.add_argument("--channels", help="Canais separados por virgula (padrao: todos)")
    args = ap.parse_args()

    log_section("ETAPA 4 - Deep Fetch: historico completo dos suspeitos")

    suspects = load_json(suspects_path(), [])
    if not suspects:
        log("suspects.json vazio. Rode 03_detect.py primeiro.")
        sys.exit(1)

    state = load_json(STATE_FILE, {})

    if args.user:
        targets = [s for s in suspects if s["username"].lower() == args.user.lower()]
        if not targets:
            log(f"Usuario @{args.user} nao encontrado em suspects.json")
            sys.exit(1)
    else:
        targets = suspects[:args.top]

    if args.channels:
        by_name = {v: k for k, v in ALL_CHANNELS.items()}
        ch_ids  = [by_name.get(c.strip(), c.strip()) for c in args.channels.split(",")]
    else:
        ch_ids = list(ALL_CHANNELS.keys())

    log(f"Suspeitos: {len(targets)} | Canais a varrer: {len(ch_ids)}")
    log(f"Usuarios: {', '.join('@' + s['username'] for s in targets)}")

    profiles_path = DATA_DIR / "suspect_profiles.json"
    profiles      = load_json(profiles_path, {})

    for s in targets:
        uid      = s["user_id"]
        username = s["username"]

        # Quantos (uid, canal) ja estao marcados como done
        done_count = sum(1 for ch in ch_ids if state.get(f"{uid}:{ch}") == "done")
        log(f"\n  @{username}: {done_count}/{len(ch_ids)} canais ja processados")

        all_msgs = deep_fetch_user(uid, username, ch_ids, state)

        profiles[uid] = {
            "user_id":    uid,
            "username":   username,
            "display":    s["display"],
            "channels": {
                ch_id: {
                    "name":      ALL_CHANNELS.get(ch_id, ch_id),
                    "msg_count": len(msgs),
                    "msg_ids":   [m["id"] for m in msgs],
                }
                for ch_id, msgs in all_msgs.items() if msgs
            },
            "total_msgs": sum(len(v) for v in all_msgs.values()),
        }
        save_json(profiles_path, profiles)
        log(f"  Perfil de @{username} salvo.")

    log(f"\nConcluido. Perfis em data/suspect_profiles.json")
    log(f"Proxima etapa: python etl/05_ai_review.py --channel <canal>")


if __name__ == "__main__":
    main()
