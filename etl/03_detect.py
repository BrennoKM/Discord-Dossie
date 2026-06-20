#!/usr/bin/env python3
"""
03_detect.py - Peneira inicial por keywords. Gera ranking de suspeitos.

Sempre re-roda sobre todos os dados (rapido, so leitura local).
Atualiza suspects.json sem perder entradas de outros canais.

Uso:
  python etl/03_detect.py --channel chat-polish
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path, suspects_path,
    log, log_section, log_progress,
)

SERIOUS = {
    "murzyn", "murzyni", "murzynka",
    "czarnuch", "czarnuchy",
    "malpa", "malpy",
    "bambus", "bambusy",
    "monkey", "ape",
    "brazylij", "brasil",
    "racist", "racism", "rasist",
    "go back", "speak english", "third world",
    "spadaj", "wynos",
    "tez jestem rasista",
    "negro", "negros",
}

SECONDARY = {
    "nie rozumie", "nie mowi",
    "wynosic",
    "nie masz tu czego", "nie nalezysz",
}

TARGET = "mogamett"


def flag_msg(content: str, en: str = "") -> dict:
    combined = (content + " " + en).lower()
    serious   = [kw for kw in SERIOUS   if kw in combined]
    secondary = [kw for kw in SECONDARY if kw in combined]
    if TARGET in combined:
        serious.append(f"@{TARGET}")
    return {"serious": serious, "secondary": secondary}


def run(channel_id: str):
    log_section(f"ETAPA 3 - Deteccao: canal {channel_id}")

    msgs    = load_jsonl(messages_path(channel_id))
    authors = load_json(authors_path(channel_id), {})
    trans   = load_json(translations_path(channel_id), {})
    ch_name = ALL_CHANNELS.get(channel_id, channel_id)

    if not msgs:
        log("Nenhuma mensagem. Rode 01_extract.py primeiro.")
        return

    log(f"Canal: #{ch_name}")
    log(f"Mensagens para analisar: {len(msgs):,}")
    log(f"Com traducao disponivel: {len(trans):,}")
    log(f"Analisando...")

    stats       = defaultdict(lambda: {
        "serious_count": 0, "secondary_count": 0, "total_msgs": 0,
        "serious_keywords": set(), "secondary_keywords": set(),
        "flagged_ids": [],
    })
    flagged_total = 0

    for i, m in enumerate(msgs):
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
                stats[uid]["serious_count"]   += len(result["serious"])
                stats[uid]["serious_keywords"].update(result["serious"])
            if result["secondary"]:
                stats[uid]["secondary_count"]   += len(result["secondary"])
                stats[uid]["secondary_keywords"].update(result["secondary"])

        if (i + 1) % 5000 == 0:
            log_progress(i + 1, len(msgs), f"flagradas ate agora: {flagged_total}")

    print(flush=True)
    log(f"Analise concluida: {flagged_total:,} mensagens flagradas")

    # Monta lista de suspeitos
    suspects = []
    for uid, s in stats.items():
        if s["serious_count"] == 0 and s["secondary_count"] == 0:
            continue
        author = authors.get(uid, {"u": "?", "d": "?"})
        suspects.append({
            "user_id":            uid,
            "username":           author["u"],
            "display":            author["d"],
            "channel_id":         channel_id,
            "channel_name":       ch_name,
            "total_msgs":         s["total_msgs"],
            "serious_count":      s["serious_count"],
            "secondary_count":    s["secondary_count"],
            "serious_keywords":   sorted(s["serious_keywords"]),
            "secondary_keywords": sorted(s["secondary_keywords"]),
            "flagged_ids":        s["flagged_ids"],
        })

    suspects.sort(key=lambda x: (-x["serious_count"], -x["secondary_count"]))

    # Merge com suspects de outros canais
    existing     = load_json(suspects_path(), [])
    existing_map = {(s["user_id"], s["channel_id"]): i for i, s in enumerate(existing)}

    novos = 0
    for s in suspects:
        key = (s["user_id"], s["channel_id"])
        if key in existing_map:
            existing[existing_map[key]] = s
        else:
            existing.append(s)
            novos += 1

    existing.sort(key=lambda x: (-x["serious_count"], -x["secondary_count"]))
    save_json(suspects_path(), existing)

    log(f"suspects.json: {len(suspects)} neste canal | {novos} novos | {len(existing)} total")
    log(f"\nTop 15 suspeitos em #{ch_name}:")
    print(f"\n  {'#':>3}  {'@usuario':<22} {'grave':>6} {'2ario':>6} {'total':>7}  keywords")
    print(f"  {'-'*75}")
    for i, s in enumerate(suspects[:15], 1):
        kws = ", ".join(s["serious_keywords"][:4])
        print(f"  {i:>3}. @{s['username']:<21} {s['serious_count']:>6} {s['secondary_count']:>6} {s['total_msgs']:>7}  {kws}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id)


if __name__ == "__main__":
    main()
