#!/usr/bin/env python3
"""
02_translate.py - Traduz todas as mensagens de um canal para EN e PT-BR.

Resumivel:
  - Pula mensagens ja presentes em translations.json.
  - Salva checkpoint a cada 20 traducoes para nao perder progresso.

Uso:
  python etl/02_translate.py --channel chat-polish
  python etl/02_translate.py --channel chat-polish --force  # retraduzi tudo
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    translations_path, messages_path, authors_path,
    log, log_section, log_progress, ts,
)
from deep_translator import GoogleTranslator

_tr_en = GoogleTranslator(source="auto", target="en")
_tr_pt = GoogleTranslator(source="auto", target="pt")

CHECKPOINT_EVERY = 20


def translate(text: str) -> tuple[str, str, str]:
    """Retorna (en, pt, motivo_skip). EN e PT sao feitos em paralelo."""
    stripped = text.strip()
    if not stripped or len(stripped) < 3:
        return "", "", "muito curto"
    if stripped.startswith("http"):
        return "", "", "link"
    if stripped.startswith("<@") or stripped.startswith("<:"):
        return "", "", "mencao/emoji"
    if len(stripped.encode("ascii", "ignore").decode().strip()) < 2 and len(stripped) < 5:
        return "", "", "so emoji"

    def _tr(target):
        try:
            return GoogleTranslator(source="auto", target=target).translate(stripped[:4999]) or ""
        except Exception as e:
            return f"__ERR__:{e}"

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_en = ex.submit(_tr, "en")
            fut_pt = ex.submit(_tr, "pt")
            en_raw = fut_en.result()
            pt_raw = fut_pt.result()

        # Captura erros reais
        en_err = en_raw if en_raw.startswith("__ERR__:") else ""
        pt_err = pt_raw if pt_raw.startswith("__ERR__:") else ""
        if en_err or pt_err:
            err_msg = (en_err or pt_err).replace("__ERR__:", "")
            return "", "", f"erro: {err_msg}"

        en = en_raw if _valid(en_raw, stripped) else ""
        pt = pt_raw if _valid(pt_raw, stripped) else ""

        if not en and not pt:
            # Mostra o que veio para diagnostico
            return "", "", f"rejeitado [en='{en_raw[:40]}' pt='{pt_raw[:40]}']"
        return en, pt, ""
    except Exception as e:
        return "", "", f"erro: {e}"


def _valid(result: str, original: str) -> bool:
    """Descarta traducoes que sao identicas ao original ou lixo do Google."""
    if not result:
        return False
    r = result.lower().strip()
    if r == original.lower().strip():
        return False
    # Resposta de erro do Google Translate
    garbage = ("error 500", "server error", "that's an error", "please try again later")
    if any(g in r for g in garbage):
        return False
    return True


def run(channel_id: str, force: bool = False, workers: int = 2):
    log_section(f"ETAPA 2 - Traducao: canal {channel_id}")

    msgs = load_jsonl(messages_path(channel_id))
    if not msgs:
        log("Nenhuma mensagem encontrada. Rode 01_extract.py primeiro.")
        return

    trans_file   = translations_path(channel_id)
    translations = load_json(trans_file, {})
    authors      = load_json(authors_path(), {})

    # Pendentes: tem conteudo e ainda nao foram traduzidas (ou force=True)
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
    log(f"Workers paralelos: {workers} mensagens simultaneas (EN+PT cada = {workers*2} threads)")

    if not pending:
        log("Nada para traduzir. Tudo ja esta atualizado.")
        return

    translated_now = 0
    skipped        = 0
    errors         = 0
    recent         = []
    skip_counts    = {}
    RECENT_MAX     = 5
    # linhas por entrada: @autor, OR, EN, PT, em branco = 5
    # mais 1 da barra de progresso
    block_height   = 0  # quantas linhas o bloco atual ocupa

    # Janela deslizante para calcular taxa real de traducoes (exclui skips instantaneos)
    WINDOW   = 30  # segundos
    tr_times = []  # timestamps de cada traducao real concluida

    def current_rate():
        now    = time.time()
        cutoff = now - WINDOW
        recent_times = [t for t in tr_times if t > cutoff]
        if len(recent_times) < 2:
            return 0
        return len(recent_times) / (now - recent_times[0]) if now > recent_times[0] else 0

    def eta_str():
        rate = current_rate()
        if rate < 0.01:
            return "--:--"
        remaining = (len(pending) - (i + 1))
        # Estima pendentes reais (descontando proporcao de skips observada)
        skip_ratio = skipped / (i + 1) if i > 0 else 0
        remaining_real = remaining * (1 - skip_ratio)
        secs  = remaining_real / rate
        m_, s_ = divmod(int(secs), 60)
        h_, m_ = divmod(m_, 60)
        if h_:
            return f"{h_}h{m_:02d}m"
        return f"{m_}m{s_:02d}s"

    def redraw():
        nonlocal block_height
        if block_height:
            print(f"\033[{block_height}A\033[J", end="", flush=True)

        lines = 0

        lines = 0
        done_so_far = already + i + 1
        rate        = current_rate()
        rate_str    = f"{rate:.1f} tr/s" if rate > 0 else "calculando..."
        aviso = "  [!] RATE LIMIT?" if errors > 10 and translated_now == 0 else ""
        print(f"  Processadas: {done_so_far:,}/{total:,} | traduzidas: {translated_now:,} | puladas: {skipped:,} | erros: {errors} | {rate_str} | ETA: {eta_str()}{aviso}", flush=True)
        lines += 1
        if skip_counts:
            top = sorted(skip_counts.items(), key=lambda x: -x[1])[:2]
            for motivo, count in top:
                print(f"  -> {count}x {motivo[:100]}", flush=True)
                lines += 1
        print(flush=True)
        lines += 1

        for author_e, ts_e, orig_e, en_e, pt_e in recent:
            print(f"  @{author_e} | {ts_e[:16]}", flush=True)
            print(f"  OR: {orig_e[:110].replace(chr(10), ' ')}", flush=True)
            print(f"  EN: {en_e[:110] if en_e else '-'}", flush=True)
            print(f"  PT: {pt_e[:110] if pt_e else '-'}", flush=True)
            print(flush=True)
            lines += 5

        pct    = (already + i + 1) / total * 100
        filled = int(28 * (already + i + 1) / total)
        bar    = "#" * filled + "-" * (28 - filled)
        print(f"  [{bar}] {pct:.1f}%", flush=True)
        lines += 1

        block_height = lines

    def process_one(m):
        en, pt, skip_reason = translate(m["c"])
        return m, en, pt, skip_reason

    i = 0
    with ThreadPoolExecutor(max_workers=workers) as outer:
        for batch_start in range(0, len(pending), workers):
            batch   = pending[batch_start:batch_start + workers]
            futures = [outer.submit(process_one, m) for m in batch]

            for fut in futures:
                m, en, pt, skip_reason = fut.result()
                if skip_reason:
                    if skip_reason.startswith("erro") or skip_reason == "sem traducao util":
                        # Nao salva: pode ser rate limit, deve retentar
                        errors += 1
                    else:
                        # Skip definitivo (link, curto, emoji): salva pra nao reprocessar
                        translations[m["id"]] = {"skip": skip_reason}
                        skipped += 1
                    skip_counts[skip_reason] = skip_counts.get(skip_reason, 0) + 1
                else:
                    translations[m["id"]] = {"en": en, "pt": pt}
                    translated_now += 1
                    tr_times.append(time.time())
                    author = authors.get(m.get("a", ""), {}).get("u", "?")
                    recent.append((author, m.get("ts", ""), m["c"], en, pt))
                    if len(recent) > RECENT_MAX:
                        recent.pop(0)
                i += 1

            redraw()

            if (batch_start + workers) % (CHECKPOINT_EVERY * workers) < workers:
                save_json(trans_file, translations)

    save_json(trans_file, translations)
    print(flush=True)  # quebra a linha da barra
    log(f"Concluido: {translated_now} novas traducoes | erros: {errors}")
    log(f"Total em translations.json: {len(translations):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel",  required=True)
    ap.add_argument("--force",    action="store_true", help="Retraduzi mesmo as ja traduzidas")
    ap.add_argument("--workers",  type=int, default=2, help="Mensagens em paralelo (padrao: 2, experimente 4-8)")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id, args.force, args.workers)


if __name__ == "__main__":
    main()
