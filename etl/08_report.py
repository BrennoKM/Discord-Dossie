#!/usr/bin/env python3
"""
08_report.py - Gera web/ completo para o SPA de exposicao.

Estrutura gerada:
  web/
    index.html
    app.js
    style.css
    data/
      meta.json
      suspects.json
      cases_index.json
      cases/{msg_id}.json
    screenshots/{msg_id}.png   (copiado de cards/ se existir)

Uso:
  python etl/08_report.py
  python etl/08_report.py --channel chat-polish   (so um canal)
  python etl/08_report.py --min-confidence 0.7
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, GUILD_ID, DATA_DIR,
    load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path,
    ai_review_path, channel_dir,
    suspects_path, discord_link, to_local,
    log, log_section,
)

ROOT       = Path(__file__).parent.parent
WEB_DIR    = ROOT / "web"
CARDS_DIR  = ROOT / "cards"
LABELS_SERIOUS = {"racist", "xenophobic"}
LABELS_ALL     = {"racist", "xenophobic", "offensive", "suspicious"}


def context_review_path(ch_id: str) -> Path:
    return channel_dir(ch_id) / "context_review.json"


def find_screenshot(msg_id: str) -> Path | None:
    """Procura screenshot em cards/*/*/screenshot.png pelo msg_id no nome da pasta."""
    if not CARDS_DIR.exists():
        return None
    for author_dir in CARDS_DIR.iterdir():
        for case_dir in author_dir.iterdir():
            if msg_id in case_dir.name:
                sc = case_dir / "screenshot.png"
                if sc.exists():
                    return sc
    return None


def collect_cases(channel_ids: list, min_confidence: float) -> tuple[list, dict]:
    """
    Coleta todos os casos graves de todos os canais.
    Retorna (cases, suspects_map).
    """
    authors    = load_json(authors_path(), {})
    suspects   = load_json(suspects_path(), [])
    suspects_map = {s["user_id"]: s for s in suspects}

    cases = []

    for ch_id in channel_ids:
        ch_name    = ALL_CHANNELS.get(ch_id, ch_id)
        msgs       = load_jsonl(messages_path(ch_id))
        trans      = load_json(translations_path(ch_id), {})
        ai_review  = load_json(ai_review_path(ch_id), {})
        ctx_review = load_json(context_review_path(ch_id), {})

        msg_by_id  = {m["id"]: m for m in msgs}
        sorted_msgs = sorted(msgs, key=lambda m: m["id"])
        msg_index   = {m["id"]: i for i, m in enumerate(sorted_msgs)}

        for msg_id, review in {**ai_review, **ctx_review}.items():
            label      = review.get("label", "")
            confidence = review.get("confidence", 0)

            if label not in LABELS_ALL:
                continue
            if confidence < min_confidence:
                continue

            m = msg_by_id.get(msg_id)
            if not m or not m.get("c", "").strip():
                continue

            uid    = m.get("a", "")
            author = authors.get(uid, {}).get("u", "?")
            t      = trans.get(msg_id, {})
            orig   = m.get("c", "")
            en     = t.get("en", "")
            pt     = t.get("pt", "")

            # Contexto ±5
            idx  = msg_index.get(msg_id)
            ctx  = []
            if idx is not None:
                for cm in sorted_msgs[max(0, idx-5): idx+6]:
                    if cm["id"] == msg_id:
                        continue
                    ct = trans.get(cm["id"], {})
                    ctx.append({
                        "id":     cm["id"],
                        "author": authors.get(cm.get("a",""), {}).get("u", "?"),
                        "ts":     to_local(cm.get("ts", "")),
                        "orig":   cm.get("c", ""),
                        "en":     ct.get("en", ""),
                        "pt":     ct.get("pt", ""),
                        "label":  (ctx_review.get(cm["id"]) or ai_review.get(cm["id"]) or {}).get("label", ""),
                    })

            # Fonte da classificacao
            if msg_id in ctx_review:
                source     = "context_review"
                old_label  = ctx_review[msg_id].get("old_label", "")
            else:
                source     = "ai_review"
                old_label  = ""

            cases.append({
                "msg_id":       msg_id,
                "channel_id":   ch_id,
                "channel_name": ch_name,
                "author_id":    uid,
                "author":       author,
                "ts":           to_local(m.get("ts", "")),
                "orig":         orig,
                "en":           en if en.lower().strip() != orig.lower().strip() else "",
                "pt":           pt if pt.lower().strip() != orig.lower().strip() else "",
                "label":        label,
                "label_source": source,
                "old_label":    old_label,
                "confidence":   confidence,
                "reason":       review.get("reason", ""),
                "discord_link": discord_link(ch_id, msg_id),
                "context":      ctx,
                "screenshot":   None,  # preenchido depois
            })

    # Ordena: mais graves primeiro, depois por confianca
    label_order = {"racist": 0, "xenophobic": 1, "offensive": 2, "suspicious": 3}
    cases.sort(key=lambda c: (label_order.get(c["label"], 9), -c["confidence"]))

    return cases, suspects_map


def build_suspects(cases: list, suspects_map: dict) -> list:
    """Monta ranking de suspeitos a partir dos casos."""
    by_user: dict[str, dict] = {}

    for c in cases:
        uid = c["author_id"]
        if uid not in by_user:
            s = suspects_map.get(uid, {})
            by_user[uid] = {
                "user_id":  uid,
                "username": c["author"],
                "display":  s.get("display", c["author"]),
                "counts":   {"racist": 0, "xenophobic": 0, "offensive": 0, "suspicious": 0},
                "channels": set(),
                "total":    0,
            }
        by_user[uid]["counts"][c["label"]] = by_user[uid]["counts"].get(c["label"], 0) + 1
        by_user[uid]["channels"].add(c["channel_name"])
        by_user[uid]["total"] += 1

    result = []
    for u in by_user.values():
        u["channels"] = sorted(u["channels"])
        result.append(u)

    result.sort(key=lambda u: (
        -(u["counts"]["racist"] * 3 + u["counts"]["xenophobic"] * 2 + u["counts"]["offensive"])
    ))
    return result


def write_web(cases: list, suspects: list, channel_ids: list):
    """Escreve toda a estrutura web/."""
    web_data = WEB_DIR / "data"
    web_cases = web_data / "cases"
    web_sc    = WEB_DIR / "screenshots"

    for d in [web_data, web_cases, web_sc]:
        d.mkdir(parents=True, exist_ok=True)

    # Copia screenshots e preenche campo
    log("Procurando screenshots...")
    sc_count = 0
    for c in cases:
        sc = find_screenshot(c["msg_id"])
        if sc:
            dest = web_sc / f"{c['msg_id']}.png"
            shutil.copy2(sc, dest)
            c["screenshot"] = f"screenshots/{c['msg_id']}.png"
            sc_count += 1
    log(f"Screenshots copiados: {sc_count}/{len(cases)}")

    # meta.json
    save_json(web_data / "meta.json", {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "guild_id":     GUILD_ID,
        "guild_name":   "Task Bar Hero",
        "channels":     [ALL_CHANNELS.get(ch, ch) for ch in channel_ids],
        "total_cases":  len(cases),
        "total_suspects": len(suspects),
        "by_label": {
            label: sum(1 for c in cases if c["label"] == label)
            for label in ["racist", "xenophobic", "offensive", "suspicious"]
        },
    })

    # suspects.json
    save_json(web_data / "suspects.json", suspects)

    # cases_index.json — versao leve sem contexto e sem reason longa
    index = [{
        "msg_id":       c["msg_id"],
        "author":       c["author"],
        "author_id":    c["author_id"],
        "channel_name": c["channel_name"],
        "ts":           c["ts"],
        "label":        c["label"],
        "label_source": c["label_source"],
        "confidence":   c["confidence"],
        "screenshot":   c["screenshot"],
        "orig_preview": c["orig"][:80],
        "en_preview":   c["en"][:80] if c["en"] else "",
        "pt_preview":   c["pt"][:80] if c["pt"] else "",
    } for c in cases]
    save_json(web_data / "cases_index.json", index)

    # cases/{msg_id}.json — completo com contexto
    for c in cases:
        save_json(web_cases / f"{c['msg_id']}.json", c)

    log(f"Dados escritos em {web_data}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel",        default=None, help="Canal especifico (padrao: todos)")
    ap.add_argument("--min-confidence", type=float, default=0.6)
    args = ap.parse_args()

    log_section("ETAPA 8 - Gerando web/")

    if args.channel:
        by_name = {v: k for k, v in ALL_CHANNELS.items()}
        channel_ids = [by_name.get(args.channel, args.channel)]
    else:
        skip = set(load_json(DATA_DIR / "channels_skip.json", []))
        channel_ids = [
            ch_id for ch_id, ch_name in ALL_CHANNELS.items()
            if ch_name not in skip
            and (channel_dir(ch_id) / "ai_review.json").exists()
        ]

    log(f"Canais com ai_review: {len(channel_ids)}")

    cases, suspects_map = collect_cases(channel_ids, args.min_confidence)
    log(f"Casos coletados: {len(cases)}")

    suspects = build_suspects(cases, suspects_map)
    log(f"Suspeitos no ranking: {len(suspects)}")

    write_web(cases, suspects, channel_ids)

    log(f"\nweb/ gerado com sucesso.")
    log(f"Proxima etapa: construir web/index.html + app.js + style.css")


if __name__ == "__main__":
    main()
