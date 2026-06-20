#!/usr/bin/env python3
"""
03_detect.py — Peneira inicial por keywords. Gera ranking de suspeitos.
Não marca como infração — apenas identifica quem merece investigação profunda.

Uso:
  python etl/03_detect.py --channel 1510279576721428612
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path, suspects_path,
    discord_link,
)

# Keywords sérias (slur racial, xenofobia explícita)
SERIOUS = {
    "murzyn", "murzyni", "murzynka",
    "czarnuch", "czarnuchy",
    "małpa", "małpy",
    "bambus", "bambusy",
    "monkey", "ape",
    "brazylij", "brasil",
    "racist", "racism", "rasist",
    "go back", "speak english", "third world",
    "spadaj", "wynoś",
    "też jestem rasistą",
    "negro", "negros",
}

# Keywords secundárias (precisam de contexto para confirmar)
SECONDARY = {
    "nie rozumie", "nie mówi",   # "não entende/fala"
    "wynosić",
    "nie masz tu czego", "nie należysz",  # "não tem nada a fazer aqui"
}

TARGET = "mogamett"


def flag_msg(content: str, translation_en: str = "") -> dict:
    """Retorna {'serious': [...], 'secondary': [...]} com keywords encontradas."""
    low_orig = content.lower()
    low_en   = translation_en.lower()
    combined = low_orig + " " + low_en

    serious   = [kw for kw in SERIOUS   if kw in combined]
    secondary = [kw for kw in SECONDARY if kw in combined]

    if TARGET in combined:
        serious.append(f"@{TARGET}")

    return {"serious": serious, "secondary": secondary}


def run(channel_id: str):
    msgs   = load_jsonl(messages_path(channel_id))
    authors = load_json(authors_path(channel_id), {})
    trans  = load_json(translations_path(channel_id), {})

    if not msgs:
        print("Nenhuma mensagem. Rode 01_extract.py e 02_translate.py primeiro.")
        return

    ch_name = ALL_CHANNELS.get(channel_id, channel_id)
    print(f"Canal: #{ch_name} — {len(msgs):,} mensagens")

    stats = defaultdict(lambda: {
        "serious_count": 0, "secondary_count": 0, "total_msgs": 0,
        "serious_keywords": set(), "secondary_keywords": set(),
        "flagged_ids": [],
    })

    flagged_total = 0

    for m in msgs:
        uid  = m["a"]
        mid  = m["id"]
        text = m.get("c", "")
        en   = trans.get(mid, {}).get("en", "")

        stats[uid]["total_msgs"] += 1

        result = flag_msg(text, en)
        if result["serious"] or result["secondary"]:
            flagged_total += 1
            stats[uid]["flagged_ids"].append(mid)
            if result["serious"]:
                stats[uid]["serious_count"] += len(result["serious"])
                stats[uid]["serious_keywords"].update(result["serious"])
            if result["secondary"]:
                stats[uid]["secondary_count"] += len(result["secondary"])
                stats[uid]["secondary_keywords"].update(result["secondary"])

    # Monta ranking
    suspects = []
    for uid, s in stats.items():
        if s["serious_count"] == 0 and s["secondary_count"] == 0:
            continue
        author = authors.get(uid, {"u": "?", "d": "?"})
        suspects.append({
            "user_id":           uid,
            "username":          author["u"],
            "display":           author["d"],
            "channel_id":        channel_id,
            "channel_name":      ch_name,
            "total_msgs":        s["total_msgs"],
            "serious_count":     s["serious_count"],
            "secondary_count":   s["secondary_count"],
            "serious_keywords":  sorted(s["serious_keywords"]),
            "secondary_keywords":sorted(s["secondary_keywords"]),
            "flagged_ids":       s["flagged_ids"],
        })

    suspects.sort(key=lambda x: (-x["serious_count"], -x["secondary_count"]))

    # Merge com suspects existentes de outros canais
    existing = load_json(suspects_path(), [])
    existing_by_uid_ch = {(s["user_id"], s["channel_id"]): i for i, s in enumerate(existing)}

    for s in suspects:
        key = (s["user_id"], s["channel_id"])
        if key in existing_by_uid_ch:
            existing[existing_by_uid_ch[key]] = s
        else:
            existing.append(s)

    # Reordena global
    existing.sort(key=lambda x: (-x["serious_count"], -x["secondary_count"]))
    save_json(suspects_path(), existing)

    # Exibe ranking
    print(f"\n  {flagged_total:,} msgs flagradas | {len(suspects)} suspeitos identificados\n")
    print(f"  {'#':>3}  {'@username':<22} {'grave':>6} {'2ário':>6} {'total':>7}  keywords principais")
    print(f"  {'-'*80}")
    for i, s in enumerate(suspects[:20], 1):
        kws = ", ".join(s["serious_keywords"][:4])
        print(f"  {i:>3}. @{s['username']:<21} {s['serious_count']:>6} {s['secondary_count']:>6} {s['total_msgs']:>7}  {kws}")

    print(f"\n  suspects.json atualizado com {len(existing)} entradas")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    args = ap.parse_args()

    ch_id = args.channel
    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    if ch_id in by_name:
        ch_id = by_name[ch_id]

    run(ch_id)


if __name__ == "__main__":
    main()
