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
        except Exception:
            return ""

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_en = ex.submit(_tr, "en")
            fut_pt = ex.submit(_tr, "pt")
            en = fut_en.result()
            pt = fut_pt.result()

        if not _valid(en, stripped): en = ""
        if not _valid(pt, stripped): pt = ""

        if not en and not pt:
            return "", "", "sem traducao util"
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


def run(channel_id: str, force: bool = False):
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

    def redraw():
        nonlocal block_height
        if block_height:
            print(f"\033[{block_height}A\033[J", end="", flush=True)

        lines = 0

        # Cabecalho com stats atualizados
        done_so_far = already + i + 1
        print(f"  Processadas: {done_so_far:,}/{total:,} | traduzidas: {translated_now:,} | puladas: {skipped:,} | erros: {errors}", flush=True)
        lines += 1

        # Bloco das ultimas traducoes
        if recent:
            print(flush=True); lines += 1
            for author_e, ts_e, orig_e, en_e, pt_e in recent:
                print(f"  @{author_e} | {ts_e[:16]}", flush=True);                lines += 1
                print(f"  OR: {orig_e[:110].replace(chr(10), ' ')}", flush=True); lines += 1
                if en_e: print(f"  EN: {en_e[:110]}", flush=True);                lines += 1
                if pt_e: print(f"  PT: {pt_e[:110]}", flush=True);                lines += 1
                print(flush=True);                                                 lines += 1

        pct    = (already + i + 1) / total * 100
        filled = int(28 * (already + i + 1) / total)
        bar    = "#" * filled + "-" * (28 - filled)
        print(f"  [{bar}] {pct:.1f}%", flush=True)
        lines += 1

        block_height = lines

    for i, m in enumerate(pending):
        en, pt, skip_reason = translate(m["c"])

        if skip_reason:
            if skip_reason.startswith("erro"):
                errors += 1
            else:
                translations[m["id"]] = {"skip": skip_reason}
                skipped += 1
            skip_counts[skip_reason] = skip_counts.get(skip_reason, 0) + 1
        else:
            translations[m["id"]] = {"en": en, "pt": pt}
            translated_now += 1
            author = authors.get(m.get("a", ""), {}).get("u", "?")
            recent.append((author, m.get("ts", ""), m["c"], en, pt))
            if len(recent) > RECENT_MAX:
                recent.pop(0)

        redraw()

        if (i + 1) % CHECKPOINT_EVERY == 0:
            save_json(trans_file, translations)

    save_json(trans_file, translations)
    print(flush=True)  # quebra a linha da barra
    log(f"Concluido: {translated_now} novas traducoes | erros: {errors}")
    log(f"Total em translations.json: {len(translations):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--force", action="store_true", help="Retraduzi mesmo as ja traduzidas")
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id, args.force)


if __name__ == "__main__":
    main()
