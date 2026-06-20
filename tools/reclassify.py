#!/usr/bin/env python3
"""
reclassify.py — Reclassifica mensagens manualmente ou via IA.

Uso:
  # Reclassificacao manual:
  venv/bin/python tools/reclassify.py <msg_id> [<msg_id> ...] --label racist
  venv/bin/python tools/reclassify.py <msg_id> [<msg_id> ...] --label xenophobic
  venv/bin/python tools/reclassify.py <msg_id> [<msg_id> ...] --label offensive
  venv/bin/python tools/reclassify.py <msg_id> [<msg_id> ...] --label suspicious

  # Reclassificacao via IA (re-analisa com contexto):
  venv/bin/python tools/reclassify.py <msg_id> [<msg_id> ...] --ai

Apos rodar, execute: venv/bin/python etl/08_report.py
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from etl.common import (
    ALL_CHANNELS, GUILD_ID, load_jsonl, load_json, save_json,
    messages_path, channel_dir, authors_path, translations_path,
    ai_review_path,
)

VALID_LABELS = {"racist", "xenophobic", "offensive", "suspicious"}


def context_review_path(ch_id: str) -> Path:
    return channel_dir(ch_id) / "context_review.json"


def build_msg_index(msg_ids: list[str]) -> dict[str, tuple[str, dict]]:
    """
    Recebe uma lista de msg_ids e retorna {msg_id: (channel_id, msg)}.
    Lê cada canal no máximo uma vez.
    """
    # 1. Acha o canal de cada id via cases_index / ai_review (sem ler jsonl)
    index_path = Path(__file__).parent.parent / "web" / "data" / "cases_index.json"
    fast_map: dict[str, str] = {}  # msg_id → ch_id
    if index_path.exists():
        for c in load_json(index_path, []):
            if "channel_id" in c:
                fast_map[c["msg_id"]] = c["channel_id"]

    remaining = set(msg_ids) - set(fast_map)
    if remaining:
        for ch_id in ALL_CHANNELS:
            ai = load_json(ai_review_path(ch_id), {})
            cr = load_json(context_review_path(ch_id), {})
            for mid in list(remaining):
                if mid in ai or mid in cr:
                    fast_map[mid] = ch_id
                    remaining.discard(mid)
            if not remaining:
                break

    # 2. Agrupa por canal e lê cada jsonl uma única vez
    by_channel: dict[str, set[str]] = {}
    for mid in msg_ids:
        ch = fast_map.get(mid)
        if ch:
            by_channel.setdefault(ch, set()).add(mid)

    result: dict[str, tuple[str, dict]] = {}
    for ch_id, ids in by_channel.items():
        for m in load_jsonl(messages_path(ch_id)):
            if m["id"] in ids:
                result[m["id"]] = (ch_id, m)
                if len(result) == len(msg_ids):
                    break
    return result


def reclassify_manual(msg_ids: list[str], label: str, reason: str):
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    msg_index = build_msg_index(msg_ids)

    by_channel: dict[str, list[str]] = {}
    not_found = []
    for mid in msg_ids:
        if mid in msg_index:
            ch_id, _ = msg_index[mid]
            by_channel.setdefault(ch_id, []).append(mid)
            print(f"  ✓ {mid}  →  #{ALL_CHANNELS[ch_id]}")
        else:
            not_found.append(mid)
            print(f"  ✗ {mid}  →  não encontrado")

    for ch_id, ids in by_channel.items():
        cr = load_json(context_review_path(ch_id), {})
        ai = load_json(ai_review_path(ch_id), {})
        for mid in ids:
            old = (cr.get(mid) or ai.get(mid) or {}).get("label", "?")
            cr[mid] = {
                "label":      label,
                "confidence": 1.0,
                "manual":     True,
                "old_label":  old,
                "reason":     reason or f"reclassificado manualmente para {label}",
                "marked_at":  now,
            }
        save_json(context_review_path(ch_id), cr)

    print(f"\n{sum(len(v) for v in by_channel.values())} reclassificado(s) como '{label}'.")
    if not_found:
        print(f"AVISO: {len(not_found)} não encontrado(s): {not_found}")


def reclassify_ai(msg_ids: list[str]):
    import os
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    authors = load_json(authors_path(), {})
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    msg_index = build_msg_index(msg_ids)

    # Cache de jsonl por canal para não reler a cada mensagem
    channel_msgs_cache: dict[str, list] = {}

    for msg_id in msg_ids:
        if msg_id not in msg_index:
            print(f"  ✗ {msg_id}  →  não encontrado")
            continue
        ch_id, msg = msg_index[msg_id]

        if ch_id not in channel_msgs_cache:
            channel_msgs_cache[ch_id] = sorted(load_jsonl(messages_path(ch_id)), key=lambda m: m["id"])
        sorted_msgs = channel_msgs_cache[ch_id]
        idx = next((i for i, m in enumerate(sorted_msgs) if m["id"] == msg_id), None)
        trans = load_json(translations_path(ch_id), {})

        ctx_msgs = sorted_msgs[max(0, idx - 5): idx + 6] if idx is not None else [msg]
        ctx_text = ""
        for cm in ctx_msgs:
            author = authors.get(cm.get("a", ""), {}).get("u", "?")
            marker = ">>> " if cm["id"] == msg_id else "    "
            t = trans.get(cm["id"], {})
            en = t.get("en", "")
            line = f"{marker}[{author}]: {cm.get('c', '')}"
            if en and en.strip().lower() != cm.get("c", "").strip().lower():
                line += f"\n         (EN: {en})"
            ctx_text += line + "\n"

        prompt = f"""Analise esta mensagem do Discord e classifique-a.

CONTEXTO DA CONVERSA (>>> indica a mensagem alvo):
{ctx_text}

Classifique a mensagem alvo em uma das categorias:
- racist: conteúdo racista explícito
- xenophobic: xenofobia, ataques a estrangeiros/imigrantes
- offensive: ofensivo mas não racial/xenofóbico
- suspicious: ambíguo, pode ser problemático
- clean: inofensivo, sem conteúdo de ódio

Responda APENAS com JSON: {{"label": "<categoria>", "confidence": <0.0-1.0>, "reason": "<explicacao breve em pt-BR>"}}"""

        print(f"  Analisando {msg_id} via IA...")
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m2 = re.search(r'\{.*\}', raw, re.DOTALL)
            result = json.loads(m2.group()) if m2 else {}

        new_label = result.get("label", "suspicious")
        confidence = result.get("confidence", 0.7)
        reason = result.get("reason", "")

        ai_rev = load_json(ai_review_path(ch_id), {})
        old_label = (load_json(context_review_path(ch_id), {}).get(msg_id)
                     or ai_rev.get(msg_id) or {}).get("label", "?")

        cr = load_json(context_review_path(ch_id), {})
        cr[msg_id] = {
            "label":      new_label,
            "confidence": confidence,
            "manual":     False,
            "ai_recheck": True,
            "old_label":  old_label,
            "reason":     reason,
            "marked_at":  now,
        }
        save_json(context_review_path(ch_id), cr)
        print(f"  ✓ {msg_id}  →  {old_label} → {new_label} ({int(confidence*100)}%)  {reason[:60]}")

    print(f"\nConcluído. Rode 08_report.py para atualizar o SPA.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("msg_ids", nargs="+")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--label", choices=list(VALID_LABELS), help="Reclassificar manualmente")
    group.add_argument("--ai", action="store_true", help="Re-analisar via IA")
    parser.add_argument("--reason", default="", help="Motivo (para reclassificacao manual)")
    args = parser.parse_args()

    msg_ids = [m.strip() for m in args.msg_ids if m.strip()]
    print(f"Processando {len(msg_ids)} mensagem(ns)...\n")

    if args.ai:
        reclassify_ai(msg_ids)
    else:
        reclassify_manual(msg_ids, args.label, args.reason)


if __name__ == "__main__":
    main()
