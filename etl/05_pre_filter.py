#!/usr/bin/env python3
"""
05_pre_filter.py - Peneira avancada entre deep fetch e revisao IA.
Aplica regex contextual, razao sinal/ruido e proximidade a mensagens flagradas
para filtrar o que sera enviado para classificacao da IA.

Uso:
  python etl/05_pre_filter.py --channel chat-polish              # salva pre_filtered.json
  python etl/05_pre_filter.py --channel chat-polish --dry-run    # so testa (nao salva .json)
  python etl/05_pre_filter.py --channel chat-polish --top 5      # top N suspeitos
  python etl/05_pre_filter.py --channel chat-polish --show       # mostra msgs detectadas
"""

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path, pre_filtered_path,
    suspects_path,
    log, log_section,
)

# ── Padroes organizados por categoria ──────────────────────────────────────────

PATTERNS = {
    "xeno_go_back": [
        re.compile(r"go\s+back\s+to\s+(your|the|ur)\s+(country|home|place)", re.I),
        re.compile(r"go\s+(back\s+)?home", re.I),
    ],
    "xeno_speak_lang": [
        re.compile(r"speak\s+(english|american|proper|our\s+language)", re.I),
        re.compile(r"learn\s+(to\s+speak\s+)?english", re.I),
        re.compile(r"(in|this\s+is)\s+america,?\s+(speak|we\s+speak)", re.I),
    ],
    "xeno_not_welcome": [
        re.compile(r"(not|no(t|t?))\s+(welcome|wanted|needed)", re.I),
        re.compile(r"don'?t\s+(belong|fit\s+in)", re.I),
        re.compile(r"you\s+(don'?t|do\s+not)\s+(belong|fit)", re.I),
    ],
    "xeno_get_out": [
        re.compile(r"get\s+(out|lost)", re.I),
        re.compile(r"(go\s+away|fuck\s+off|piss\s+off)", re.I),
    ],
    "xeno_third_world": [
        re.compile(r"third\s*world", re.I),
        re.compile(r"3rd\s*world", re.I),
    ],
    "xeno_your_kind": [
        re.compile(r"your\s+(kind|race|people|country|place|sort)", re.I),
    ],
    "xeno_these_people": [
        re.compile(r"(these|those)\s+(people|guys|folks|immigrants)", re.I),
    ],
    "racist_animal": [
        re.compile(r"\bmonkey\b", re.I),
        re.compile(r"\bape\b", re.I),
        re.compile(r"\bgorilla\b", re.I),
        re.compile(r"\bchimp\b", re.I),
    ],
    "racist_macaco": [
        re.compile(r"\bmacaco\b", re.I),
        re.compile(r"\bmacaca\b", re.I),
    ],
    "racist_slur": [
        re.compile(r"\bnegro\b", re.I),
        re.compile(r"\bnegra\b", re.I),
        re.compile(r"\bnegros\b", re.I),
        re.compile(r"murzyn", re.I),
        re.compile(r"czarnuch", re.I),
        re.compile(r"bambus", re.I),
        re.compile(r"\bescravo\b", re.I),
        re.compile(r"\bescrava\b", re.I),
        re.compile(r"\bbanana\b", re.I),
    ],
    "racist_comparison": [
        re.compile(r"\b(like|as)\s+(a\s+)?(monkey|ape|animal|dog|pig)", re.I),
        re.compile(r"you('?re| are| r)\s+(a\s+|an\s+)?(monkey|ape|dog|pig)", re.I),
    ],
    "xeno_polish": [
        re.compile(r"wynos", re.I),
        re.compile(r"spadaj", re.I),
        re.compile(r"nie (rozumie|mowi|nalezysz)", re.I),
    ],
    "xeno_portuguese": [
        re.compile(r"volta\s+(pra|para)", re.I),
        re.compile(r"vai\s+(embora|pra)", re.I),
    ],
}

CONTEXT_WINDOW = 3

CATEGORY_LABELS = {
    "step3_keyword":     "ja flagrada por keyword na etapa 3",
    "context_window":    "proxima de msg flagrada (±3 msgs)",
    "xeno_go_back":      "'go back to your country'",
    "xeno_speak_lang":   "'speak english' / 'learn english'",
    "xeno_not_welcome":  "'not welcome' / 'dont belong'",
    "xeno_get_out":      "'get out' / 'go away' / 'fuck off'",
    "xeno_third_world":  "'third world' / '3rd world'",
    "xeno_your_kind":    "'your kind' / 'your people' / 'your race'",
    "xeno_these_people": "'these people' / 'those guys'",
    "racist_animal":     "monkey/ape/gorilla/chimp",
    "racist_macaco":     "macaco/macaca (pt-br)",
    "racist_slur":       "negro/murzyn/czarnuch/bambus/escravo/banana",
    "racist_comparison": "comparacao animalesca (like a monkey/pig)",
    "xeno_polish":       "wynos/spadaj/nie rozumie (polones, sem 'nie masz')",
    "xeno_portuguese":   "volta pra/vai embora (portugues)",
}


def match_patterns(text: str) -> dict:
    if not text:
        return {}
    results = {}
    for cat, regexes in PATTERNS.items():
        matches = []
        for rx in regexes:
            if rx.search(text):
                matches.append(rx.pattern)
        if matches:
            results[cat] = matches
    return results


def score_snr(suspect: dict) -> float:
    total = suspect.get("total_msgs", 1)
    signal = suspect.get("serious_count", 0) + suspect.get("secondary_count", 0)
    return signal / max(total, 1)


def run(channel_id: str, top_n: int = 0, dry_run: bool = False, show: bool = False):
    log_lines: list[str] = []
    lbl = "DRY-RUN" if dry_run else "SALVANDO"
    log_section(f"ETAPA 5 - Pre-Filter [{lbl}]")

    msgs     = load_jsonl(messages_path(channel_id))
    authors  = load_json(authors_path(), {})
    trans    = load_json(translations_path(channel_id), {})
    suspects = load_json(suspects_path(), [])
    ch_name  = ALL_CHANNELS.get(channel_id, channel_id)

    def _l(msg: str = ""):
        log(msg)
        log_lines.append(msg)

    _l(f"Canal: #{ch_name}")
    _l(f"Mensagens no canal: {len(msgs):,}")

    ch_suspects = [s for s in suspects if s["channel_id"] == channel_id]
    if top_n:
        ch_suspects = ch_suspects[:top_n]

    if not ch_suspects:
        _l("Nenhum suspeito neste canal.")
        return

    target_ids = {s["user_id"] for s in ch_suspects}
    _l(f"Suspeitos: {len(ch_suspects)} | Targets: {len(target_ids)}")

    _l(f"\nLegenda das colunas:")
    _l(f"  total    - mensagens do usuario neste canal")
    _l(f"  step3    - ja flagradas por keyword na etapa 3 (passam direto)")
    _l(f"  SNR      - sinal/ruido: (step3_count / total_msgs)")
    _l(f"  padroes  - novas mensagens detectadas pelos regex")
    _l(f"  ctx      - contexto: proximas (±{CONTEXT_WINDOW}) de alguma flagrada")
    _l(f"  pra IA   = step3 + padroes + ctx (total que iria pro Groq)\n")

    # Indexa msgs por autor (dict e set de IDs)
    msgs_by_author: dict[str, list[dict]] = defaultdict(list)
    user_msg_ids: dict[str, set] = defaultdict(set)
    for m in msgs:
        if m.get("a") in target_ids and m.get("c", "").strip():
            msgs_by_author[m["a"]].append(m)
            user_msg_ids[m["a"]].add(m["id"])

    # flagged_ids por autor
    flagged_by: dict[str, set] = {
        s["user_id"]: set(s.get("flagged_ids", []))
        for s in ch_suspects
    }

    msg_by_id: dict[str, dict] = {m["id"]: m for m in msgs}
    matched: dict[str, dict] = {}
    stats = {}

    # ── Passada 1: Step 3 flagged passam direto ──
    for s in ch_suspects:
        uid = s["user_id"]
        for mid in flagged_by.get(uid, set()):
            if mid not in matched:
                matched[mid] = {
                    "categories": ["step3_keyword"],
                    "reason": "Flagrada na deteccao inicial (step 3)",
                }

    # ── Passada 2: Padroes nas mensagens dos suspeitos ──
    pattern_matched = 0
    for s in ch_suspects:
        uid = s["user_id"]
        user_msgs = msgs_by_author.get(uid, [])
        snr = score_snr(s)
        stats[uid] = {
            "username": s["username"],
            "total": len(user_msgs),
            "flagged": len(flagged_by.get(uid, set())),
            "snr": snr,
            "pattern": 0,
        }

        for m in user_msgs:
            mid = m["id"]
            if mid in matched:
                continue

            text = m.get("c", "")
            en = trans.get(mid, {}).get("en", "")
            pt = trans.get(mid, {}).get("pt", "")
            combined = f"{text}  {en}  {pt}"

            result = match_patterns(combined)
            if not result:
                continue

            cats = list(result.keys())
            reasons = [f"{cat}({', '.join(result[cat])})" for cat in cats]
            matched[mid] = {
                "categories": cats,
                "reason": "; ".join(reasons),
            }
            pattern_matched += 1
            stats[uid]["pattern"] += 1

    # ── Passada 3: Context window ──
    msgs_sorted = sorted(msgs, key=lambda m: m["id"])
    expand_from = set(matched.keys())
    context_added = 0

    for i, m in enumerate(msgs_sorted):
        if m["id"] in matched or m.get("a") not in target_ids:
            continue
        if not m.get("c", "").strip():
            continue

        lo = max(0, i - CONTEXT_WINDOW)
        hi = min(len(msgs_sorted), i + CONTEXT_WINDOW + 1)
        for j in range(lo, hi):
            if msgs_sorted[j]["id"] in expand_from:
                matched[m["id"]] = {
                    "categories": ["context_window"],
                    "reason": f"A {abs(i-j)} msgs de msg flagrada (pos {j})",
                }
                context_added += 1
                break

    # ── Estatisticas ──
    total_suspect_msgs = sum(st["total"] for st in stats.values())
    total_to_ai = len(matched)

    _l()
    header = f"{'@usuario':<22} {'total':>6} {'step3':>6} {'SNR':>7} {'padroes':>8} {'ctx':>5} {'pra IA':>7}"
    _l(header)
    _l("-" * len(header.expandtabs()))

    for s in ch_suspects:
        uid = s["user_id"]
        st = stats[uid]
        uids = user_msg_ids.get(uid, set())
        user_to_ai = sum(1 for mid in uids if mid in matched)
        user_ctx = sum(
            1 for mid, info in matched.items()
            if mid in uids and info["categories"] == ["context_window"]
        )
        user_pat = sum(
            1 for mid, info in matched.items()
            if mid in uids
            and info["categories"] != ["step3_keyword"]
            and info["categories"] != ["context_window"]
        )
        snr_s = f"{st['snr']:.4f}" if st['snr'] > 0 else "0"
        line = f"  @{st['username']:<20} {st['total']:>6} {st['flagged']:>6} {snr_s:>7} {user_pat:>8} {user_ctx:>5} {user_to_ai:>7}"
        print(line)
        log_lines.append(line)

    _l()
    _l(f"Total msgs dos suspeitos: {total_suspect_msgs:,}")
    if total_suspect_msgs:
        _l(f"Passariam pra IA:         {total_to_ai:,} ({total_to_ai/total_suspect_msgs*100:.1f}%)")
        _l(f"Reducao:                  {(1-total_to_ai/total_suspect_msgs)*100:.1f}%")
    _l(f"  step3_keyword:           {sum(1 for info in matched.values() if 'step3_keyword' in info['categories'])}")
    _l(f"  padroes novos:           {pattern_matched}")
    _l(f"  context_window:          {context_added}")
    _l(f"  total unicos:            {len(matched)}")

    # Categorias
    cat_counts = defaultdict(int)
    for info in matched.values():
        for cat in info["categories"]:
            cat_counts[cat] += 1
    if cat_counts:
        _l(f"\nMatch por categoria:")
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            label = CATEGORY_LABELS.get(cat, cat)
            _l(f"  {cat:<25} {count:>4}   {label}")

    # ── Mensagens detectadas: monta estrutura (sempre) ──
    by_cat = defaultdict(list)
    for mid, info in matched.items():
        for cat in info["categories"]:
            if cat in CATEGORY_LABELS and cat not in ("step3_keyword", "context_window"):
                m = msg_by_id.get(mid)
                if m:
                    by_cat[cat].append((mid, m, info))

    if by_cat:
        log_lines.append("")
        log_lines.append("=" * 60)
        log_lines.append("MENSAGENS DETECTADAS PELOS PADROES (passada 2)")
        log_lines.append("=" * 60)

        for cat in sorted(by_cat.keys()):
            items = by_cat[cat]
            label = CATEGORY_LABELS.get(cat, cat)
            log_lines.append(f"\n  [{cat}] {label} ({len(items)} msgs)")
            log_lines.append(f"  {'-'*60}")
            for mid, m, info in items:
                uid = m["a"]
                author = authors.get(uid, {}).get("u", "?")
                ts = m.get("ts", "?")[:16]
                text = m.get("c", "")
                en = trans.get(mid, {}).get("en", "")
                pt = trans.get(mid, {}).get("pt", "")
                log_lines.append(f"    @{author:<20} {ts}")
                log_lines.append(f"    ORIG: {text}")
                if en:
                    log_lines.append(f"    EN:   {en}")
                if pt:
                    log_lines.append(f"    PT:   {pt}")
                log_lines.append(f"    motivo: {info['reason']}")
                log_lines.append("")

        # ── Show: imprime no console os 5 primeiros de cada categoria ──
        if show and pattern_matched:
            print(f"\n{'='*60}", flush=True)
            print(f"MENSAGENS DETECTADAS PELOS PADROES (passada 2)", flush=True)
            print(f"{'='*60}", flush=True)
            for cat in sorted(by_cat.keys()):
                items = by_cat[cat]
                label = CATEGORY_LABELS.get(cat, cat)
                print(f"\n  [{cat}] {label} ({len(items)} msgs)", flush=True)
                print(f"  {'-'*60}", flush=True)
                for mid, m, info in items[:5]:
                    uid = m["a"]
                    author = authors.get(uid, {}).get("u", "?")
                    ts = m.get("ts", "?")[:16]
                    text = m.get("c", "")[:150]
                    en = trans.get(mid, {}).get("en", "")[:150]
                    pt = trans.get(mid, {}).get("pt", "")[:150]
                    print(f"    @{author:<20} {ts}", flush=True)
                    print(f"    ORIG: {text}", flush=True)
                    if en: print(f"    EN:   {en}", flush=True)
                    if pt: print(f"    PT:   {pt}", flush=True)
                    print(f"    motivo: {info['reason']}", flush=True)
                    print(flush=True)
                if len(items) > 5:
                    print(f"    ... e mais {len(items)-5} mensagens nesta categoria ({len(items)} total)", flush=True)
                    print(flush=True)

    # ── Salva pre_filtered.json ──
    if not dry_run:
        out = pre_filtered_path(channel_id)
        save_json(out, matched)
        _l(f"\nSalvo: {out}")
        _l(f"Proxima etapa: python etl/06_ai_review.py --channel {ch_name}")

    # ── Salva log (sempre) ──
    ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pre_filter_{ch_name}_{ts_now}.log"
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    log(f"Log salvo: {log_file}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--show", action="store_true",
                    help="Mostra exemplos das mensagens detectadas")
    ap.add_argument("--dry-run", action="store_true",
                    help="So testa (nao salva pre_filtered.json, mas salva log)")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id = by_name.get(args.channel, args.channel)
    run(ch_id, args.top, args.dry_run, args.show)


if __name__ == "__main__":
    main()
