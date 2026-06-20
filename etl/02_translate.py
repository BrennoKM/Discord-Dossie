#!/usr/bin/env python3
"""
02_translate.py - Traduz todas as mensagens de um canal para EN e PT-BR.

Resumivel:
  - Pula mensagens ja presentes em translations.json.
  - Checkpoint a cada 20 traducoes.

Usa o endpoint batch do Google Translate (sem chave, sem deep_translator):
  - 1 request por lote de N textos para EN
  - 1 request por lote de N textos para PT
  - Os dois em paralelo com threads

Uso:
  python etl/02_translate.py --channel chat-polish
  python etl/02_translate.py --channel chat-polish --batch 15
  python etl/02_translate.py --channel chat-polish --force
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    translations_path, messages_path, authors_path,
    log, log_section, ts, to_local, discord_link, GUILD_ID,
)

CHECKPOINT_EVERY = 20
TRANSLATE_URL    = "https://translate.googleapis.com/translate_a/t"
HEADERS          = {"User-Agent": "Mozilla/5.0"}


def _should_skip(text: str) -> str:
    """Retorna motivo de skip ou '' se deve traduzir."""
    s = text.strip()
    if not s or len(s) < 3:
        return "muito curto"
    if s.startswith("http"):
        return "link"
    if s.startswith("<@") or s.startswith("<:"):
        return "mencao/emoji"
    if len(s.encode("ascii", "ignore").decode().strip()) < 2 and len(s) < 5:
        return "so emoji"
    return ""


def _valid(result: str, original: str) -> bool:
    if not result:
        return False
    r = result.lower().strip()
    if r == original.lower().strip():
        return False
    garbage = ("error 500", "server error", "that's an error", "please try again later")
    if any(g in r for g in garbage):
        return False
    return True


def _batch_request(texts: list[str], target: str) -> list[str]:
    """
    Envia N textos em uma unica chamada HTTP e retorna N traducoes.
    Retorna lista de strings vazias em caso de erro.
    """
    try:
        data   = [("q", t[:500]) for t in texts]
        params = {"client": "gtx", "sl": "auto", "tl": target}
        r = requests.post(TRANSLATE_URL, params=params, data=data, headers=HEADERS, timeout=15)
        r.raise_for_status()
        result = r.json()
        # Formato: [["traducao1"], ["traducao2"], ...]
        # Resposta pode ser lista de strings ["tr1","tr2"] ou lista de listas [["tr1"],["tr2"]]
        out = []
        for item in result:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, list) and item:
                out.append(item[0])
            else:
                out.append("")
        return out
    except Exception as e:
        return [f"__ERR__:{e}"] * len(texts)


def translate_batch(messages: list[dict]) -> list[tuple[str, str, str]]:
    """
    Traduz um lote de mensagens.
    - Filtra skips instantaneamente
    - Envia os validos em 2 requests paralelos (EN + PT)
    Retorna lista de (en, pt, skip_reason) na mesma ordem de `messages`.
    """
    skips        = {}  # idx -> motivo
    to_translate = []  # [(idx, text), ...]

    for idx, m in enumerate(messages):
        text   = m.get("c", "")
        reason = _should_skip(text)
        if reason:
            skips[idx] = reason
        else:
            to_translate.append((idx, text.strip()))

    results = [("", "", "")] * len(messages)

    # Marca skips
    for idx, reason in skips.items():
        results[idx] = ("", "", reason)

    if to_translate:
        indices, texts = zip(*to_translate)

        # EN e PT em paralelo, cada um em 1 request com todos os textos
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_en = ex.submit(_batch_request, list(texts), "en")
            fut_pt = ex.submit(_batch_request, list(texts), "pt")
            ens = fut_en.result()
            pts = fut_pt.result()

        for i, (idx, text) in enumerate(zip(indices, texts)):
            en_raw = ens[i]
            pt_raw = pts[i]

            if en_raw.startswith("__ERR__:") or pt_raw.startswith("__ERR__:"):
                err = (en_raw + pt_raw).replace("__ERR__:", "")
                results[idx] = ("", "", f"erro: {err[:80]}")
                continue

            en = en_raw if _valid(en_raw, text) else ""
            pt = pt_raw if _valid(pt_raw, text) else ""

            if not en and not pt:
                results[idx] = ("", "", f"rejeitado [en='{en_raw[:30]}' pt='{pt_raw[:30]}']")
            else:
                results[idx] = (en, pt, "")

    return results


def run(channel_id: str, force: bool = False, batch_size: int = 10):
    log_section(f"ETAPA 2 - Traducao: canal {channel_id}")

    msgs    = load_jsonl(messages_path(channel_id))
    if not msgs:
        log("Nenhuma mensagem encontrada. Rode 01_extract.py primeiro.")
        return

    trans_file   = translations_path(channel_id)
    translations = load_json(trans_file, {})
    authors      = load_json(authors_path(), {})

    pending = [
        m for m in msgs
        if m.get("c", "").strip()
        and (force or m["id"] not in translations)
    ]

    total   = len(msgs)
    already = total - len(pending)

    log(f"Total de mensagens no canal: {total:,}")
    log(f"Ja traduzidas (pulando): {already:,}")
    log(f"Pendentes para traducao: {len(pending):,}")
    log(f"Tamanho do lote: {batch_size} mensagens por request (EN+PT em paralelo)")

    if not pending:
        log("Nada para traduzir.")
        return

    translated_now = 0
    skipped        = 0
    errors         = 0
    recent         = []
    skip_counts    = {}
    RECENT_MAX     = 5
    block_height   = 0
    start_time     = time.time()
    WINDOW         = 60
    tr_times       = []
    i              = 0

    def current_rate():
        now    = time.time()
        cutoff = now - WINDOW
        recent_t = [t for t in tr_times if t > cutoff]
        if len(recent_t) < 2:
            return 0
        return len(recent_t) / (now - recent_t[0])

    def eta_str():
        rate = current_rate()
        if rate < 0.01:
            return "--:--"
        skip_ratio    = skipped / max(i, 1)
        remaining     = (len(pending) - i) * (1 - skip_ratio)
        secs          = remaining / rate
        m_, s_        = divmod(int(secs), 60)
        h_, m_        = divmod(m_, 60)
        return f"{h_}h{m_:02d}m" if h_ else f"{m_}m{s_:02d}s"

    def redraw():
        nonlocal block_height
        if block_height:
            print(f"\033[{block_height}A\033[J", end="", flush=True)

        lines = 0
        rate     = current_rate()
        rate_str = f"{rate:.1f} tr/s" if rate > 0 else "calculando..."
        aviso    = "  [!] RATE LIMIT?" if errors > 5 and translated_now == 0 else ""
        done     = already + i + 1

        print(f"  Processadas: {done:,}/{total:,} | traduzidas: {translated_now:,} | puladas: {skipped:,} | erros: {errors} | {rate_str} | ETA: {eta_str()}{aviso}", flush=True)
        lines += 1

        if skip_counts:
            top = sorted(skip_counts.items(), key=lambda x: -x[1])[:2]
            for motivo, count in top:
                print(f"  -> {count}x {motivo[:100]}", flush=True)
                lines += 1

        print(flush=True)
        lines += 1

        for author_e, ts_e, orig_e, en_e, pt_e, link_e in recent:
            print(f"  @{author_e} | {ts_e} (UTC-3) | {link_e}", flush=True)
            print(f"  OR: {orig_e[:110].replace(chr(10), ' ')}", flush=True)
            print(f"  EN: {en_e[:110] if en_e else '-'}", flush=True)
            print(f"  PT: {pt_e[:110] if pt_e else '-'}", flush=True)
            print(flush=True)
            lines += 5

        pct    = done / total * 100
        filled = int(28 * done / total)
        bar    = "#" * filled + "-" * (28 - filled)
        print(f"  [{bar}] {pct:.1f}%", flush=True)
        lines += 1

        block_height = lines

    for batch_start in range(0, len(pending), batch_size):
        batch   = pending[batch_start:batch_start + batch_size]
        results = translate_batch(batch)

        for m, (en, pt, skip_reason) in zip(batch, results):
            if skip_reason:
                if skip_reason.startswith("erro") or skip_reason.startswith("rejeitado"):
                    errors += 1
                else:
                    translations[m["id"]] = {"skip": skip_reason}
                    skipped += 1
                skip_counts[skip_reason] = skip_counts.get(skip_reason, 0) + 1
            else:
                translations[m["id"]] = {"en": en, "pt": pt}
                translated_now += 1
                tr_times.append(time.time())
                author = authors.get(m.get("a", ""), {}).get("u", "?")
                link   = discord_link(channel_id, m["id"])
                recent.append((author, to_local(m.get("ts", "")), m["c"], en, pt, link))
                if len(recent) > RECENT_MAX:
                    recent.pop(0)
            i += 1

        redraw()

        if batch_start % (CHECKPOINT_EVERY * batch_size) < batch_size:
            save_json(trans_file, translations)

        time.sleep(0.3)

    save_json(trans_file, translations)
    print(flush=True)
    log(f"Concluido: {translated_now:,} traduzidas | {skipped:,} puladas | {errors} erros")
    log(f"Total em translations.json: {len(translations):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--force",   action="store_true")
    ap.add_argument("--batch",   type=int, default=10, help="Mensagens por request (padrao: 10)")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id, args.force, args.batch)


if __name__ == "__main__":
    main()
