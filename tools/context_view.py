#!/usr/bin/env python3
"""
Visualiza o contexto de uma mensagem ou lista reclassificacoes.

Uso:
  python tools/context_view.py --id 1517551192668897431
  python tools/context_view.py --id 1517551192668897431 --channel chat-polish --context 5
  python tools/context_view.py --reclassified
  python tools/context_view.py --reclassified --channel chat-polish
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, load_jsonl,
    messages_path, authors_path, translations_path,
    ai_review_path, channel_dir,
    to_local,
)


CONTEXT_REVIEW_FILENAME = "context_review.json"


def view_context(msg_id: str, channel_id: str, context_n: int = 10):
    msgs    = load_jsonl(messages_path(channel_id))
    authors = load_json(authors_path(), {})
    trans   = load_json(translations_path(channel_id), {})
    ai_rev  = load_json(ai_review_path(channel_id), {})
    ch_name = ALL_CHANNELS.get(channel_id, channel_id)

    sorted_msgs = sorted(msgs, key=lambda m: m["id"])
    msg_index   = {m["id"]: i for i, m in enumerate(sorted_msgs)}

    if msg_id not in msg_index:
        print(f"Erro: mensagem {msg_id} nao encontrada em {ch_name}")
        return

    idx = msg_index[msg_id]
    start = max(0, idx - context_n)
    end   = min(len(sorted_msgs), idx + context_n + 1)

    target_msg = sorted_msgs[idx]
    target_author = authors.get(target_msg.get("a", ""), {}).get("d", "?")
    target_ts     = to_local(target_msg.get("ts", ""))

    print(f"Canal: #{ch_name}")
    print(f"Mensagem alvo: {msg_id}")
    if msg_id in ai_rev:
        r = ai_rev[msg_id]
        print(f"Classificacao atual: {r.get('label', '?')} (conf: {r.get('confidence', '?')})")
        print(f"  reason_pt: {r.get('reason_pt', '')}")
        print(f"  reason_en: {r.get('reason_en', '')}")
    print(f"Mensagens: {len(sorted_msgs)} | Contexto: {context_n} antes, {context_n} depois")
    print()

    for i in range(start, end):
        m = sorted_msgs[i]
        uid = m.get("a", "")
        author = authors.get(uid, {}).get("d", uid[:10])
        ts = to_local(m.get("ts", ""))

        orig = m.get("c", "")
        if len(orig) > 200:
            orig = orig[:200] + "..."

        t = trans.get(m["id"], {})
        en = t.get("en", "")
        pt = t.get("pt", "")

        is_target = m["id"] == msg_id
        prefix = "═══ [ALVO] " if is_target else "         "

        print(f"{prefix}ID: {m['id']}")
        print(f"         @{author}  |  {ts}")
        print(f"         ORIG: {orig}")
        if en:
            print(f"         EN:   {en[:200]}")
        if pt:
            print(f"         PTBR: {pt[:200]}")
        if is_target:
            print("         " + "─" * 50)
        print()


def list_reclassified(channel_id: str):
    ai_rev  = load_json(ai_review_path(channel_id), {})
    trans   = load_json(translations_path(channel_id), {})
    authors = load_json(authors_path(), {})
    msgs    = load_jsonl(messages_path(channel_id))
    ch_name = ALL_CHANNELS.get(channel_id, channel_id)
    msg_map = {m["id"]: m for m in msgs}

    changed = [(k, v) for k, v in ai_rev.items()
               if "context_old_label" in v and v.get("context_old_label") != v.get("label")]
    changed.sort(key=lambda x: x[0])

    if not changed:
        print(f"Nenhuma reclassificacao contextual em #{ch_name}.")
        return

    print(f"Canal: #{ch_name}")
    print(f"Reclassificacoes contextuais: {len(changed)}")
    print()

    for mid, v in changed:
        old = v.get("context_old_label", "?")
        new = v.get("label", "?")
        m = msg_map.get(mid, {})
        uid = m.get("a", "")
        author = authors.get(uid, {}).get("d", uid[:10]) if uid else "?"
        ts = to_local(m.get("ts", ""))
        orig = m.get("c", "")
        t = trans.get(mid, {})
        en = t.get("en", "")
        pt = t.get("pt", "")

        print(f"ID: {mid}")
        print(f"  @{author}  |  {ts}")
        print(f"  {old} -> {new}  (conf: {v.get('confidence', '?')})")
        print(f"  ORIG: {orig[:200]}")
        if en:
            print(f"  EN:   {en[:200]}")
        if pt:
            print(f"  PTBR: {pt[:200]}")
        print(f"  reason_pt: {v.get('reason_pt', '')}")
        print(f"  reason_en: {v.get('reason_en', '')}")
        print()


def main():
    by_name = {v: k for k, v in ALL_CHANNELS.items()}

    ap = argparse.ArgumentParser(description="Visualiza contexto de mensagem ou lista reclassificacoes")
    ap.add_argument("--id",      help="ID da mensagem alvo")
    ap.add_argument("--channel", default="chat-polish", help="Nome ou ID do canal")
    ap.add_argument("--context", type=int, default=10, help="Msgs antes/depois (padrao: 10)")
    ap.add_argument("--reclassified", action="store_true", help="Lista mensagens reclassificadas pelo contexto")
    args = ap.parse_args()

    ch_id = by_name.get(args.channel, args.channel)

    if args.reclassified:
        list_reclassified(ch_id)
    elif args.id:
        view_context(args.id, ch_id, args.context)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
