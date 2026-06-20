#!/usr/bin/env python3
"""
06_ai_review.py - Classifica mensagens dos suspeitos.

Modos:
  Padrao (Groq):  envia lotes para Groq (Llama 3) classificar.
  --local:        exporta lotes para classificacao manual.
  --local --resume: importa resultados da classificacao manual.

O classification salva rotulo + motivo em PT e EN.

Uso:
  python etl/06_ai_review.py --channel chat-polish
  python etl/06_ai_review.py --channel chat-polish --local
  python etl/06_ai_review.py --channel chat-polish --local --resume
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
    messages_path, authors_path, translations_path, ai_review_path, pre_filtered_path,
    suspects_path,
    log, log_section, log_progress,
)

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Voce e um sistema de analise de conteudo especializado em detectar racismo e xenofobia em mensagens de Discord.

Analise cada mensagem e classifique com um dos rotulos:
- "racist": contem racismo explicito (slurs, comparacoes com animais, inferioridade racial)
- "xenophobic": xenofobia explicita (mandando embora, dizendo que nao pertence, discriminacao por nacionalidade)
- "offensive": ofensivo mas nao necessariamente racista/xenofobico (insultos, menosprezo)
- "suspicious": suspeito, precisa de contexto humano para confirmar
- "clean": sem problemas

Responda APENAS com JSON valido no formato:
[{"id": "MSG_ID", "label": "LABEL", "confidence": 0.95, "reason_pt": "explicacao em portugues", "reason_en": "explanation in english"}]

Seja objetivo. Contexto: servidor de jogo online RPG idle onde ocorreram denuncias de racismo contra jogadores brasileiros."""

BATCH = 15


def review_batch(messages: list[dict], translations: dict, authors: dict) -> list[dict]:
    if not messages:
        return []

    lines = []
    for m in messages:
        uid    = m.get("a", "")
        author = authors.get(uid, {}).get("u", "?")
        text   = m.get("c", "").strip()
        en     = translations.get(m["id"], {}).get("en", "")
        pt     = translations.get(m["id"], {}).get("pt", "")

        line = f'ID:{m["id"]} | @{author} | ORIGINAL: {text}'
        if en and en.lower() != text.lower():
            line += f' | EN: {en}'
        if pt and pt.lower() != text.lower():
            line += f' | PT-BR: {pt}'
        lines.append(line)

    retries = 0
    while retries < 5:
        try:
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": "Classifique as mensagens abaixo:\n\n" + "\n".join(lines)},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            raw   = resp.choices[0].message.content.strip()
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start == -1 or end == 0:
                return []
            return json.loads(raw[start:end])
        except Exception as e:
            err = str(e)
            if "rate_limit" in err.lower() or "429" in err:
                wait = 15 * (2 ** retries)
                log(f"\n  Rate limit Groq (tentativa {retries+1}), aguardando {wait}s...")
                time.sleep(wait)
                retries += 1
            else:
                log(f"Erro na chamada Groq: {e}")
                return []
    log("Rate limit persistente, pulando lote.")
    return []


def run(channel_id: str, top_n: int = 0, user_id: str = ""):
    log_section(f"ETAPA 6 - Revisao IA (Groq): canal {channel_id}")

    msgs     = load_jsonl(messages_path(channel_id))
    authors  = load_json(authors_path(), {})
    trans    = load_json(translations_path(channel_id), {})
    review   = load_json(ai_review_path(channel_id), {})
    suspects = load_json(suspects_path(), [])
    ch_name  = ALL_CHANNELS.get(channel_id, channel_id)

    log(f"Canal: #{ch_name}")
    log(f"Mensagens no canal: {len(msgs):,}")
    log(f"Ja revisadas pela IA: {len(review):,}")

    # Usa pre_filtered.json se existir, senao cai no comportamento antigo
    pf_path = pre_filtered_path(channel_id)
    if pf_path.exists():
        pre_filtered = load_json(pf_path, {})
        log(f"pre_filtered.json: {len(pre_filtered):,} mensagens selecionadas")
        msg_by_id = {m["id"]: m for m in msgs}
        to_review = [
            msg_by_id[mid] for mid in pre_filtered
            if mid in msg_by_id and mid not in review
        ]
    else:
        log("pre_filtered.json nao encontrado, usando todos os suspeitos do canal")
        if user_id:
            target_ids = {user_id}
        elif top_n:
            ch_suspects = [s for s in suspects if s["channel_id"] == channel_id][:top_n]
            target_ids  = {s["user_id"] for s in ch_suspects}
        else:
            ch_suspects = [s for s in suspects if s["channel_id"] == channel_id]
            target_ids  = {s["user_id"] for s in ch_suspects}
        to_review = [
            m for m in msgs
            if m.get("a") in target_ids
            and m.get("c", "").strip()
            and m["id"] not in review
        ]

    if not to_review:
        log("Nada para revisar. Todos ja foram classificados.")
        # Resumo mesmo assim
        _print_summary(review)
        return

    log(f"Mensagens pendentes de classificacao: {len(to_review):,}")
    log(f"Processando em lotes de {BATCH}...\n")

    classified  = 0
    errors      = 0
    batch_num   = 0
    recent      = []  # ultimas classificacoes para exibir
    RECENT_MAX  = 5
    block_height = 0
    total       = len(to_review)

    LABEL_ICON = {
        "racist":      "RACISTA   ",
        "xenophobic":  "XENOFOBICO",
        "offensive":   "OFENSIVO  ",
        "suspicious":  "SUSPEITO  ",
        "clean":       "limpo     ",
    }

    def redraw():
        nonlocal block_height
        if block_height:
            print(f"\033[{block_height}A\033[J", end="", flush=True)

        lines = 0
        done  = already_done + classified + errors
        pct   = done / (total + already_done) * 100 if (total + already_done) else 0
        filled = int(28 * done / (total + already_done)) if (total + already_done) else 0
        bar   = "#" * filled + "-" * (28 - filled)
        print(f"  [{bar}] {done:,}/{total + already_done:,} ({pct:.1f}%)  classificadas: {classified} | erros: {errors} | lote {batch_num}", flush=True)
        lines += 1

        print(flush=True)
        lines += 1

        for author_r, label_r, reason_pt_r, reason_en_r, orig_r, en_r, pt_r in recent:
            icon = LABEL_ICON.get(label_r, label_r)
            print(f"  [{icon}] @{author_r}", flush=True)
            print(f"  OR: {orig_r[:100].replace(chr(10),' ')}", flush=True)
            if en_r: print(f"  EN: {en_r[:100]}", flush=True)
            if pt_r: print(f"  PT: {pt_r[:100]}", flush=True)
            if reason_pt_r: print(f"  >> PT: {reason_pt_r[:100]}", flush=True)
            if reason_en_r: print(f"  >> EN: {reason_en_r[:100]}", flush=True)
            print(flush=True)
            lines += 3 + bool(en_r) + bool(pt_r) + bool(reason_pt_r) + bool(reason_en_r)

        block_height = lines

    already_done = len(review)

    for i in range(0, total, BATCH):
        batch      = to_review[i:i + BATCH]
        results    = review_batch(batch, trans, authors)
        batch_num += 1

        if results:
            for r in results:
                mid = r.get("id", "")
                if mid:
                    label = r.get("label", "unknown")
                    review[mid] = {
                        "label":      label,
                        "confidence": r.get("confidence", 0),
                        "reason_pt":  r.get("reason_pt", r.get("reason", "")),
                        "reason_en":  r.get("reason_en", ""),
                    }
                    classified += 1
                    # Adiciona ao historico recente
                    m_obj  = next((m for m in batch if m["id"] == mid), None)
                    author = authors.get(m_obj.get("a",""), {}).get("u", "?") if m_obj else "?"
                    orig   = m_obj.get("c", "") if m_obj else ""
                    en     = trans.get(mid, {}).get("en", "")
                    pt     = trans.get(mid, {}).get("pt", "")
                    recent.append((author, label, r.get("reason_pt", ""), r.get("reason_en", ""), orig, en, pt))
                    if len(recent) > RECENT_MAX:
                        recent.pop(0)
        else:
            errors += len(batch)

        save_json(ai_review_path(channel_id), review)

        redraw()
        time.sleep(0.5)

    save_json(ai_review_path(channel_id), review)
    print(flush=True)
    log(f"Concluido: {classified} classificadas | {errors} erros")
    _print_summary(review)


def _print_summary(review: dict):
    labels = {}
    for v in review.values():
        lb = v.get("label", "?")
        labels[lb] = labels.get(lb, 0) + 1

    if not labels:
        return

    log(f"\nResumo das classificacoes:")
    print(f"\n  {'label':<15} {'count':>6}")
    print(f"  {'-'*22}")
    for label, count in sorted(labels.items(), key=lambda x: -x[1]):
        marker = " <--" if label in ("racist", "xenophobic") else ""
        print(f"  {label:<15} {count:>6}{marker}")


# ── modo local: eu (DeepSeek) classifico ───────────────────────────────────────

LOCAL_DIR = DATA_DIR / "ai_pending"

def run_local(channel_id: str, resume: bool = False):
    """Exporta lotes pra eu classificar manualmente."""
    log_section(f"ETAPA 6 - Revisao Local: canal {channel_id}")

    msgs     = load_jsonl(messages_path(channel_id))
    trans    = load_json(translations_path(channel_id), {})
    authors  = load_json(authors_path(), {})
    review   = load_json(ai_review_path(channel_id), {})
    suspects = load_json(suspects_path(), [])
    ch_name  = ALL_CHANNELS.get(channel_id, channel_id)

    log(f"Canal: #{ch_name}")
    log(f"Mensagens no canal: {len(msgs):,}")
    log(f"Ja revisadas: {len(review):,}")

    pf_path = pre_filtered_path(channel_id)
    if pf_path.exists():
        pre_filtered = load_json(pf_path, {})
        log(f"pre_filtered.json: {len(pre_filtered):,} mensagens")
        msg_by_id = {m["id"]: m for m in msgs}
        to_review = [msg_by_id[mid] for mid in pre_filtered if mid in msg_by_id and mid not in review]
    else:
        log("Sem pre_filtered.json, exportando todas dos suspeitos.")
        ch_suspects = [s for s in suspects if s["channel_id"] == channel_id]
        target_ids  = {s["user_id"] for s in ch_suspects}
        to_review   = [m for m in msgs if m.get("a") in target_ids and m.get("c","").strip() and m["id"] not in review]

    if not to_review:
        log("Nada pendente.")
        return

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    total = len(to_review)
    log(f"{total} mensagens pendentes em { (total + BATCH - 1) // BATCH } lotes de {BATCH}")

    manifest = []
    for i in range(0, total, BATCH):
        batch = to_review[i:i + BATCH]
        batch_n = i // BATCH
        out_path = LOCAL_DIR / f"batch_{channel_id}_{batch_n:04d}.json"

        items = []
        for m in batch:
            uid   = m.get("a", "")
            t     = trans.get(m["id"], {})
            items.append({
                "id":       m["id"],
                "author":   authors.get(uid, {}).get("u", uid),
                "original": m.get("c", ""),
                "en":       t.get("en", ""),
                "pt":       t.get("pt", ""),
            })

        save_json(out_path, {"batch": batch_n, "items": items})
        manifest.append(str(out_path))
        log(f"  Lote {batch_n:4d}/{total//BATCH}: {len(items)} msgs → {out_path.name}")

    save_json(LOCAL_DIR / f"manifest_{channel_id}.json", {
        "channel_id": channel_id,
        "total":      total,
        "batches":    manifest,
    })
    log(f"\nManifesto: {LOCAL_DIR}/manifest_{channel_id}.json")
    log("Classifique cada batch e salve o resultado como:")
    log("  data/ai_pending/batch_{channel}_{n}_result.json")
    log("Depois re-execute com --local --resume para importar.")


def import_local(channel_id: str):
    """Importa resultados das classificacoes locais e monta ai_review.json."""
    manifest = load_json(LOCAL_DIR / f"manifest_{channel_id}.json", {})
    if not manifest:
        log("Manifesto nao encontrado. Execute --local primeiro.")
        return

    review = load_json(ai_review_path(channel_id), {})
    imported = 0

    for batch_path in manifest.get("batches", []):
        result_path = batch_path.replace(".json", "_result.json")
        results = load_json(result_path, [])
        if not results:
            log(f"  Pulando {batch_path} (sem resultado)")
            continue
        for r in results:
            mid = r.get("id", "")
            if mid:
                review[mid] = {
                    "label":      r.get("label", "unknown"),
                    "confidence": r.get("confidence", 0),
                    "reason_pt":  r.get("reason_pt", ""),
                    "reason_en":  r.get("reason_en", ""),
                }
                imported += 1

    save_json(ai_review_path(channel_id), review)
    log(f"Importadas: {imported} novas classificacoes | total: {len(review)}")
    _print_summary(review)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--top",     type=int, default=0)
    ap.add_argument("--user",    help="User ID especifico")
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
        run(ch_id, args.top, args.user)


if __name__ == "__main__":
    main()
