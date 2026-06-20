#!/usr/bin/env python3
"""
07_context_review.py - Revisao contextual de mensagens clean/suspicious.

Pega mensagens classificadas como 'clean' ou 'suspicious' de usuarios que ja
tem pelo menos uma mensagem 'racist' ou 'xenophobic' confirmada, e reenvia
para a IA com contexto ±N mensagens ao redor — permitindo detectar xenofobia
disfarçada, cumplicidade e omissao.

Modos:
  Padrao (Groq): envia cada alvo + contexto para Groq reclassificar.
  --local:       exporta lotes para classificacao manual.
  --local --resume: importa resultados.

Uso:
  python etl/07_context_review.py --channel chat-polish
  python etl/07_context_review.py --channel chat-polish --local
  python etl/07_context_review.py --channel chat-polish --local --resume
"""

import argparse
import json
import sys
import time
from pathlib import Path

from groq import Groq

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, GROQ_API_KEY, DATA_DIR,
    load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path, ai_review_path,
    suspects_path, channel_dir,
    log, log_section, log_progress,
)

client = Groq(api_key=GROQ_API_KEY)
LOCAL_DIR = DATA_DIR / "ai_pending"

SYSTEM_PROMPT = """Voce e um sistema de analise de conteudo especializado em detectar racismo e xenofobia em mensagens de Discord.

Voce recebera uma mensagem ALVO marcada com [ALVO] e mensagens de contexto ao redor para ajudar a entender a situacao.

Analise o ALVO considerando o contexto e classifique:
- "racist": racismo explicito (slurs, comparacoes animais, inferioridade racial)
- "xenophobic": xenofobia (expulsar, dizer que nao pertence, discriminar por nacionalidade/origem)
- "offensive": ofensivo mas nao necessariamente racista/xenofobico
- "suspicious": suspeito, precisa de revisao humana
- "clean": sem problemas mesmo no contexto

IMPORTANTE: Considere tambem cumplicidade/omissao. Um usuario que nao diz algo
explicitamente racista, mas que no contexto esta rindo, concordando, ou se omitindo
diante de racismo/xenofobia, deve ser classificado como racista/xenofobico/offensive
conforme o caso — com a explicacao na reason.

Responda APENAS com JSON:
{"id": "MSG_ID", "label": "LABEL", "confidence": 0.95, "reason_pt": "explicacao em portugues considerando o contexto", "reason_en": "explanation in English considering the context"}

Contexto: servidor de jogo online onde ocorreram denuncias de racismo contra jogadores brasileiros."""


def context_review_path(channel_id: str) -> Path:
    return channel_dir(channel_id) / "context_review.json"


def build_context_block(target: dict, context_msgs: list, trans: dict, authors: dict) -> str:
    lines = []
    for m in context_msgs:
        uid    = m.get("a", "")
        author = authors.get(uid, {}).get("u", "?")
        text   = m.get("c", "").strip()
        en     = trans.get(m["id"], {}).get("en", "")
        is_target = m["id"] == target["id"]

        prefix = "[ALVO]" if is_target else "      "
        line   = f'{prefix} @{author}: {text}'
        if en and not is_target:
            line += f' (EN: {en})'
        if is_target and en:
            line += f'\n       EN: {en}'
        lines.append(line)
    return "\n".join(lines)


def review_single(target: dict, context_block: str) -> dict | None:
    retries = 0
    while retries < 5:
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"ID alvo: {target['id']}\n\n{context_block}"},
                ],
                temperature=0.1,
                max_tokens=300,
            )
            raw = resp.choices[0].message.content.strip()
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return None
            return json.loads(raw[start:end])
        except Exception as e:
            err = str(e)
            if "rate_limit" in err.lower() or "429" in err:
                wait = 15 * (2 ** retries)
                log(f"\n  Rate limit Groq (tentativa {retries+1}), aguardando {wait}s...")
                time.sleep(wait)
                retries += 1
            else:
                log(f"  Erro Groq: {e}")
                return None
    return None


def _load_targets(channel_id: str, context_n: int, labels: list) -> tuple:
    msgs        = load_jsonl(messages_path(channel_id))
    authors     = load_json(authors_path(), {})
    trans       = load_json(translations_path(channel_id), {})
    ai_review   = load_json(ai_review_path(channel_id), {})
    ctx_review  = load_json(context_review_path(channel_id), {})

    confirmed_offenders = set()
    for m in msgs:
        r = ai_review.get(m["id"], {})
        if r.get("label") in ("racist", "xenophobic"):
            confirmed_offenders.add(m.get("a", ""))

    sorted_msgs = sorted(msgs, key=lambda m: m["id"])
    msg_index   = {m["id"]: i for i, m in enumerate(sorted_msgs)}

    targets = [
        m for m in sorted_msgs
        if m.get("a") in confirmed_offenders
        and ai_review.get(m["id"], {}).get("label") in labels
        and m["id"] not in ctx_review
        and m.get("c", "").strip()
    ]

    return targets, ctx_review, ai_review, sorted_msgs, msg_index, trans, authors


def run(channel_id: str, context_n: int = 5, labels: list = None):
    if labels is None:
        labels = ["clean", "suspicious"]

    log_section(f"ETAPA 7 - Revisao Contextual: #{ALL_CHANNELS.get(channel_id, channel_id)}")

    targets, ctx_review, ai_review, sorted_msgs, msg_index, trans, authors = \
        _load_targets(channel_id, context_n, labels)

    log(f"Ofensores confirmados (racist/xenophobic): {len({m.get('a','') for m in targets})}")
    log(f"Mensagens para revisao contextual: {len(targets):,} ({', '.join(labels)})")

    if not targets:
        log("Nada para revisar.")
        _print_summary(ai_review, ctx_review)
        return

    reclassified = 0
    errors       = 0
    block_height = 0
    recent       = []
    RECENT_MAX   = 4

    LABEL_ICON = {
        "racist":     "RACISTA   ",
        "xenophobic": "XENOFOBICO",
        "offensive":  "OFENSIVO  ",
        "suspicious": "SUSPEITO  ",
        "clean":      "limpo     ",
    }

    def redraw():
        nonlocal block_height
        if block_height:
            print(f"\033[{block_height}A\033[J", end="", flush=True)
        lines = 0
        done   = len(ctx_review)
        total  = len(targets) + done
        filled = int(28 * done / total) if total else 0
        bar    = "#" * filled + "-" * (28 - filled)
        print(f"  [{bar}] {done:,}/{total:,} ({done/total*100:.1f}%)  reclassificadas: {reclassified} | erros: {errors}", flush=True)
        lines += 1
        print(flush=True); lines += 1
        for entry in recent:
            author_r, old_r, new_r, orig_r, en_r, reason_r = entry
            changed = " (mudou!)" if old_r != new_r else ""
            print(f"  [{LABEL_ICON.get(new_r, new_r)}] @{author_r}  era: {old_r}{changed}", flush=True)
            print(f"  OR: {orig_r[:100].replace(chr(10),' ')}", flush=True)
            if en_r: print(f"  EN: {en_r[:100]}", flush=True)
            print(f"  >> {reason_r[:110]}", flush=True)
            print(flush=True)
            lines += 4 + bool(en_r)
        block_height = lines

    for target in targets:
        idx     = msg_index[target["id"]]
        ctx     = sorted_msgs[max(0, idx - context_n): idx + context_n + 1]
        block   = build_context_block(target, ctx, trans, authors)
        result  = review_single(target, block)

        if result:
            new_label = result.get("label", "unknown")
            old_label = ai_review.get(target["id"], {}).get("label", "?")
            reason_pt = result.get("reason_pt", result.get("reason", ""))
            reason_en = result.get("reason_en", "")

            ctx_review[target["id"]] = {
                "label":      new_label,
                "confidence": result.get("confidence", 0),
                "reason_pt":  reason_pt,
                "reason_en":  reason_en,
                "old_label":  old_label,
                "context_n":  context_n,
            }

            # Atualiza ai_review.json com flag de reclassificacao contextual
            entry = {
                "label":            new_label,
                "confidence":       result.get("confidence", 0),
                "reason_pt":        reason_pt,
                "reason_en":        reason_en,
                "context_reviewed": True,
            }
            if new_label != old_label:
                entry["context_old_label"] = old_label
            ai_review[target["id"]] = entry

            reclassified += 1
            author    = authors.get(target.get("a",""), {}).get("u", "?")
            en        = trans.get(target["id"], {}).get("en", "")
            recent.append((author, old_label, new_label, target.get("c",""), en, reason_pt))
            if len(recent) > RECENT_MAX:
                recent.pop(0)
        else:
            errors += 1

        save_json(context_review_path(channel_id), ctx_review)
        save_json(ai_review_path(channel_id), ai_review)
        redraw()
        time.sleep(0.3)

    print(flush=True)
    log(f"Concluido: {reclassified} reprocessadas | {errors} erros")
    _print_summary(ai_review, ctx_review)


def run_local(channel_id: str):
    """Exporta lotes com contexto para classificacao manual."""
    log_section(f"ETAPA 7 - Revisao Contextual (Local): #{ALL_CHANNELS.get(channel_id, channel_id)}")

    context_n = 5
    labels    = ["clean", "suspicious"]
    targets, ctx_review, ai_review, sorted_msgs, msg_index, trans, authors = \
        _load_targets(channel_id, context_n, labels)

    log(f"Mensagens pendentes: {len(targets):,}")

    if not targets:
        log("Nada pendente.")
        return

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    BATCH = 10
    total = len(targets)
    log(f"Exportando {total} alvos em {(total + BATCH - 1) // BATCH} lotes de {BATCH}")

    manifest = []
    for i in range(0, total, BATCH):
        batch     = targets[i:i + BATCH]
        batch_n   = i // BATCH
        out_path  = LOCAL_DIR / f"ctx_{channel_id}_{batch_n:04d}.json"

        items = []
        for target in batch:
            idx = msg_index[target["id"]]
            ctx = sorted_msgs[max(0, idx - context_n): idx + context_n + 1]

            ctx_items = []
            for cm in ctx:
                uid2  = cm.get("a", "")
                t2    = trans.get(cm["id"], {})
                ctx_items.append({
                    "id":       cm["id"],
                    "author":   authors.get(uid2, {}).get("u", uid2[:10]),
                    "ts":       cm.get("ts", "")[:16],
                    "original": cm.get("c", ""),
                    "en":       t2.get("en", ""),
                    "pt":       t2.get("pt", ""),
                })

            t = trans.get(target["id"], {})
            items.append({
                "id":        target["id"],
                "author":    authors.get(target.get("a",""), {}).get("u", "?"),
                "ts":        target.get("ts", "")[:16],
                "original":  target.get("c", ""),
                "en":        t.get("en", ""),
                "pt":        t.get("pt", ""),
                "old_label": ai_review.get(target["id"], {}).get("label", "?"),
                "context":   ctx_items,
            })

        save_json(out_path, {"batch": batch_n, "items": items})
        manifest.append(str(out_path))
        log(f"  Lote {batch_n:4d}/{total//BATCH}: {len(items)} alvos -> {out_path.name}")

    save_json(LOCAL_DIR / f"ctx_manifest_{channel_id}.json", {
        "channel_id": channel_id,
        "context_n":  context_n,
        "total":      total,
        "batches":    manifest,
    })
    log(f"\nManifesto: {LOCAL_DIR}/ctx_manifest_{channel_id}.json")
    log("Classifique cada alvo e salve o resultado como:")
    log("  data/ai_pending/ctx_{channel}_{n}_result.json")
    log("Depois re-execute com --local --resume para importar.")


def import_local(channel_id: str):
    """Importa resultados da classificacao manual."""
    log_section(f"ETAPA 7 - Revisao Contextual (Importacao): #{ALL_CHANNELS.get(channel_id, channel_id)}")

    manifest = load_json(LOCAL_DIR / f"ctx_manifest_{channel_id}.json", {})
    if not manifest:
        log("Manifesto nao encontrado. Execute --local primeiro.")
        return

    ctx_review = load_json(context_review_path(channel_id), {})
    ai_review  = load_json(ai_review_path(channel_id), {})
    imported   = 0

    for batch_path in manifest.get("batches", []):
        result_path = batch_path.replace(".json", "_result.json")
        results = load_json(result_path, [])
        if not results:
            log(f"  Pulando {batch_path} (sem resultado)")
            continue
        for r in results:
            mid = r.get("id", "")
            if mid:
                old_label = ai_review.get(mid, {}).get("label", "?")
                new_label = r.get("label", "unknown")
                reason_pt = r.get("reason_pt", r.get("reason", ""))
                reason_en = r.get("reason_en", "")

                ctx_review[mid] = {
                    "label":      new_label,
                    "confidence": r.get("confidence", 0),
                    "reason_pt":  reason_pt,
                    "reason_en":  reason_en,
                    "old_label":  old_label,
                    "context_n":  manifest.get("context_n", 5),
                }

                # Atualiza ai_review.json com flag
                entry = {
                    "label":            new_label,
                    "confidence":       r.get("confidence", 0),
                    "reason_pt":        reason_pt,
                    "reason_en":        reason_en,
                    "context_reviewed": True,
                }
                if new_label != old_label:
                    entry["context_old_label"] = old_label
                ai_review[mid] = entry

                imported += 1

    save_json(context_review_path(channel_id), ctx_review)
    save_json(ai_review_path(channel_id), ai_review)
    log(f"Importadas: {imported} novas revisoes | total: {len(ctx_review)}")
    _print_summary(ai_review, ctx_review)


def _print_summary(ai_review: dict, ctx_review: dict):
    changed = {mid: r for mid, r in ctx_review.items()
               if r.get("label") != r.get("old_label")}

    # Atualiza ai_review com flags para entradas reclassificadas
    # (channel_id precisa ser passado externamente ou inferido)
    # O run() e import_local() ja fazem isso em tempo real

    log(f"\nMensagens que mudaram de classificacao: {len(changed)}")
    by_change = {}
    for mid, r in changed.items():
        key = f"{r['old_label']} -> {r['label']}"
        by_change[key] = by_change.get(key, 0) + 1
    for change, count in sorted(by_change.items(), key=lambda x: -x[1]):
        print(f"  {change:<30} {count}")

    escalated = [mid for mid, r in changed.items()
                 if r["label"] in ("racist", "xenophobic") and r["old_label"] in ("clean", "suspicious")]
    log(f"\nEscalados para racist/xenophobic: {len(escalated)}")
    if escalated:
        log(f"Proxima etapa: python etl/08_report.py --channel {ALL_CHANNELS.get(list(ctx_review.keys())[0][:5] if ctx_review else '', '')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--context", type=int, default=5, help="Msgs contexto cada lado (padrao: 5)")
    ap.add_argument("--labels",  nargs="+", default=["clean", "suspicious"],
                    help="Labels a reprocessar (padrao: clean suspicious)")
    ap.add_argument("--local",   action="store_true",
                    help="Modo local: exporta lotes pra classificacao manual")
    ap.add_argument("--resume",  action="store_true",
                    help="Importa resultados da classificacao local")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)

    if args.resume:
        import_local(ch_id)
    elif args.local:
        run_local(ch_id)
    else:
        run(ch_id, args.context, args.labels)


if __name__ == "__main__":
    main()
