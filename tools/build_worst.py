#!/usr/bin/env python3
"""
build_worst.py - Monta worst.json a partir dos info.json em cards/.
Nao duplica conteudo: worst.json contem apenas o ranking e referencias as pastas.

Uso:
  python tools/build_worst.py
  python tools/build_worst.py --top 20
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CARDS = Path(__file__).parent.parent / "cards"
OUT   = Path(__file__).parent.parent / "data" / "worst.json"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    if not CARDS.exists():
        print("Pasta cards/ nao encontrada. Rode tools/screenshot.py primeiro.")
        return

    # Le todos os info.json
    infos = []
    for info_file in sorted(CARDS.glob("*/*/info.json")):
        try:
            info = load_json(info_file)
            # Adiciona o caminho relativo da pasta do card
            card_folder = str(info_file.parent.relative_to(CARDS))
            info["card_folder"] = card_folder
            infos.append(info)
        except Exception as e:
            print(f"  Erro ao ler {info_file}: {e}")

    if not infos:
        print("Nenhum info.json encontrado em cards/.")
        return

    print(f"{len(infos)} cards encontrados")

    # Agrupa por autor
    by_author = defaultdict(list)
    for info in infos:
        by_author[info["author_username"]].append(info)

    # Monta ranking
    offenders = []
    for username, msgs in by_author.items():
        racist_count     = sum(1 for m in msgs if m.get("ai_label") == "racist")
        xenophobic_count = sum(1 for m in msgs if m.get("ai_label") == "xenophobic")
        offensive_count  = sum(1 for m in msgs if m.get("ai_label") == "offensive")

        offenders.append({
            "username":         username,
            "display":          msgs[0].get("author_display", username),
            "author_id":        msgs[0].get("author_id", ""),
            "racist_count":     racist_count,
            "xenophobic_count": xenophobic_count,
            "offensive_count":  offensive_count,
            "total_cards":      len(msgs),
            # So referencia as pastas, sem duplicar conteudo
            "cards":            sorted(m["card_folder"] for m in msgs),
        })

    offenders.sort(key=lambda o: -(o["racist_count"] * 3 + o["xenophobic_count"] * 2 + o["offensive_count"]))

    result = {
        "generated":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_cards":  len(infos),
        "top_offenders": [
            {"rank": i + 1, **o}
            for i, o in enumerate(offenders[:args.top])
        ],
    }

    save_json(OUT, result)
    print(f"worst.json salvo em data/")
    print(f"\n  {'#':>3}  {'@usuario':<22} {'racista':>8} {'xenofobia':>10} {'ofensivo':>9} {'total':>6}")
    print(f"  {'-'*65}")
    for o in result["top_offenders"]:
        print(f"  {o['rank']:>3}. @{o['username']:<21} {o['racist_count']:>8} {o['xenophobic_count']:>10} {o['offensive_count']:>9} {o['total_cards']:>6}")


if __name__ == "__main__":
    main()
