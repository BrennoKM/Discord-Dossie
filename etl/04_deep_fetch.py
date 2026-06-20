#!/usr/bin/env python3
"""
04_deep_fetch.py — Busca TODO o histórico dos top suspeitos em TODOS os canais.
Mensagens são salvas nos arquivos do canal correspondente (sem duplicação).
Na próxima extração completa daquele canal, essas msgs já estarão lá.

Uso:
  python etl/04_deep_fetch.py --top 10          # top 10 do suspects.json
  python etl/04_deep_fetch.py --user xbiedro    # usuário específico
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
    suspects_path, channel_dir,
)
from deep_translator import GoogleTranslator

_tr_en = GoogleTranslator(source="auto", target="en")
_tr_pt = GoogleTranslator(source="auto", target="pt")


def translate(text: str) -> tuple[str, str]:
    if not text or len(text.strip()) < 3:
        return "", ""
    try:
        en = _tr_en.translate(text[:4999]) or ""
        time.sleep(0.15)
        pt = _tr_pt.translate(text[:4999]) or ""
        time.sleep(0.15)
        return en, pt
    except Exception:
        return "", ""


def fetch_all_in_channel(channel_id: str, user_ids: set[str]) -> dict[str, list[dict]]:
    """
    Varre o canal inteiro e filtra mensagens dos user_ids.
    Salva tudo no arquivo do canal (não duplica o que já existe).
    Retorna {user_id: [compact_msgs]}.
    """
    msgs_file  = messages_path(channel_id)
    auths_file = authors_path(channel_id)
    meta       = load_json(meta_path(channel_id), {})

    existing_ids = {m["id"] for m in load_jsonl(msgs_file)}
    authors = load_json(auths_file, {})

    result = defaultdict(list)
    before = None
    total_scanned = 0
    new_saved = 0

    ch_name = ALL_CHANNELS.get(channel_id, channel_id)
    print(f"    #{ch_name}: varrendo...", end="", flush=True)

    while True:
        batch = fetch_batch(channel_id, before=before)
        if not batch:
            break

        new_batch = []
        for m in batch:
            uid = m["author"]["id"]
            authors[uid] = compact_author(m["author"])
            cm = compact(m)

            if cm["id"] not in existing_ids:
                new_batch.append(cm)
                existing_ids.add(cm["id"])
                new_saved += 1

            if uid in user_ids:
                result[uid].append(cm)

        if new_batch:
            append_jsonl(msgs_file, new_batch)

        before = min(m["id"] for m in batch)
        total_scanned += len(batch)
        print(f"\r    #{ch_name}: {total_scanned:,} msgs varridas...", end="", flush=True)
        time.sleep(0.4)

        if len(batch) < 100:
            # Chegamos ao início
            meta["fully_extracted"] = True
            meta["oldest_id"] = before
            break

    # Atualiza meta e autores
    if existing_ids:
        meta["last_id"] = max(existing_ids)
    meta["total_msgs"] = len(existing_ids)
    save_json(meta_path(channel_id), meta)
    save_json(auths_file, authors)

    found = sum(len(v) for v in result.values())
    print(f"\r    #{ch_name}: {total_scanned:,} varridas | {new_saved} salvas | {found} dos suspeitos")
    return dict(result)


def deep_fetch_user(user_id: str, username: str, channels: list[str]) -> dict:
    """Coleta todas as mensagens de um usuário em todos os canais e traduz."""
    all_msgs = {}  # channel_id → [msgs]

    print(f"\n  @{username} ({user_id})")
    for ch_id in channels:
        msgs_file = messages_path(ch_id)
        existing  = load_jsonl(msgs_file)
        # Mensagens desse usuário já no arquivo
        user_msgs = [m for m in existing if m["a"] == user_id]

        if user_msgs:
            ch_name = ALL_CHANNELS.get(ch_id, ch_id)
            print(f"    #{ch_name}: {len(user_msgs)} msgs já no arquivo (sem re-busca)")
            all_msgs[ch_id] = user_msgs
        else:
            # Canal não extraído ainda — só busca msgs do usuário varrendo
            fetched = fetch_all_in_channel(ch_id, {user_id})
            all_msgs[ch_id] = fetched.get(user_id, [])

    # Traduz as que ainda não têm tradução
    total_translated = 0
    for ch_id, msgs in all_msgs.items():
        if not msgs:
            continue
        trans_file = translations_path(ch_id)
        trans = load_json(trans_file, {})
        changed = False
        for m in msgs:
            if m["id"] not in trans and m.get("c", "").strip():
                en, pt = translate(m["c"])
                if en or pt:
                    trans[m["id"]] = {"en": en, "pt": pt}
                    changed = True
                    total_translated += 1
        if changed:
            save_json(trans_file, trans)

    total = sum(len(v) for v in all_msgs.values())
    print(f"    Total: {total} msgs em {len([c for c in all_msgs if all_msgs[c]])} canais | {total_translated} traduzidas agora")
    return all_msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10, help="Top N suspeitos do suspects.json")
    ap.add_argument("--user", help="Username específico")
    ap.add_argument("--channels", help="Canais separados por vírgula (padrão: todos)")
    args = ap.parse_args()

    suspects = load_json(suspects_path(), [])
    if not suspects:
        print("suspects.json vazio. Rode 03_detect.py primeiro.")
        sys.exit(1)

    # Seleciona suspeitos alvo
    if args.user:
        targets = [s for s in suspects if s["username"].lower() == args.user.lower()]
        if not targets:
            print(f"Usuário @{args.user} não encontrado em suspects.json")
            sys.exit(1)
    else:
        targets = suspects[:args.top]

    # Canais para varrer
    if args.channels:
        ch_ids = []
        for c in args.channels.split(","):
            c = c.strip()
            by_name = {v: k for k, v in ALL_CHANNELS.items()}
            ch_ids.append(by_name.get(c, c))
    else:
        ch_ids = list(ALL_CHANNELS.keys())

    print(f"Deep fetch: {len(targets)} suspeitos × {len(ch_ids)} canais")
    print(f"Suspeitos: {', '.join('@' + s['username'] for s in targets)}\n")

    # Resultado consolidado por usuário
    profile_path = Path(__file__).parent.parent / "data" / "suspect_profiles.json"
    profiles = load_json(profile_path, {})

    for s in targets:
        uid  = s["user_id"]
        user = s["username"]
        all_msgs = deep_fetch_user(uid, user, ch_ids)

        # Salva perfil consolidado do suspeito
        profiles[uid] = {
            "user_id":  uid,
            "username": user,
            "display":  s["display"],
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
        save_json(profile_path, profiles)

    print(f"\n✓ Perfis salvos em data/suspect_profiles.json")
    print(f"  Próxima etapa: python etl/05_ai_review.py")


if __name__ == "__main__":
    main()
