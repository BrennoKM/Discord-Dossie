#!/usr/bin/env python3
"""
screenshot.py — Tira prints do Discord para cada caso grave do pipeline.

Lê os casos de web/data/cases_index.json (gerado pelo 08_report.py).
Salva em cards/{author}/{msg_id}/screenshot.png

Viewport estreito (520px) para prints compactos e quadrados, compatíveis com o SPA.

Uso:
  venv/bin/python tools/screenshot.py                    # todos os racist/xenophobic
  venv/bin/python tools/screenshot.py --all              # inclui offensive/suspicious
  venv/bin/python tools/screenshot.py --user xbiedro    # só um usuario
  venv/bin/python tools/screenshot.py --limit 20        # limita quantidade
  venv/bin/python tools/screenshot.py --dry-run         # lista sem tirar prints
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import GUILD_ID, to_local

load_dotenv()

ROOT      = Path(__file__).parent.parent
WEB_DATA  = ROOT / "web" / "data"
CARDS     = ROOT / "cards"
CARDS.mkdir(exist_ok=True)

SESSION_FILE     = ROOT / "session.json"
CHROME_EXEC      = "/bin/google-chrome"
VIEWPORT_WIDTH   = 980   # largo o suficiente pro Discord abrir as duas sidebars
VIEWPORT_HEIGHT  = 900

SERIOUS_LABELS = {"racist", "xenophobic"}
ALL_LABELS     = {"racist", "xenophobic", "offensive", "suspicious"}


def load_json(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def already_done(msg_id: str) -> bool:
    """Verifica se já existe screenshot para este msg_id."""
    for author_dir in CARDS.iterdir():
        if not author_dir.is_dir():
            continue
        sc = author_dir / msg_id / "screenshot.png"
        if sc.exists():
            return True
    return False


def screenshot_message(page, channel_id: str, msg_id: str, out_path: Path) -> bool:
    url = f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{msg_id}"

    try:
        page.goto(url, wait_until="networkidle", timeout=25000)
    except PlaywrightTimeout:
        pass

    try:
        page.wait_for_selector('[class*="message"]', timeout=15000)
    except PlaywrightTimeout:
        print(f"    Timeout aguardando mensagem {msg_id}")
        page.screenshot(path=str(out_path.parent / f"debug_{msg_id}.png"))
        return False

    time.sleep(2)

    # Abre sidebar direita (lista de membros) para comprimir o chat e gerar prints mais estreitos
    try:
        btn = page.query_selector('[aria-label="Mostrar lista de membros"]')
        if btn:
            btn.click()
            time.sleep(0.8)
    except Exception:
        pass

    # Esconde barras flutuantes do Discord (jump to present, new messages bar, etc)
    try:
        page.evaluate("""() => {
            ['[class*="jumpToPresentBar"]','[class*="newMessagesBar"]',
             '[class*="unreadPill"]','[class*="jumpToPresent"]'].forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.style.display = 'none');
            });
        }""")
    except Exception:
        pass

    # Localiza o elemento da mensagem alvo
    msg_el = (
        page.query_selector(f'li[id*="{msg_id}"]') or
        page.query_selector(f'[id$="{msg_id}"]') or
        page.query_selector(f'[data-id="{msg_id}"]')
    )

    if not msg_el:
        print(f"    Mensagem nao encontrada")
        page.screenshot(path=str(out_path.parent / f"debug_{msg_id}.png"))
        return False

    PADDING = 8

    try:
        # Encontra cabeca e cauda do grupo navegando pelo filho direto do OL
        # (o LI alvo pode estar dentro de um DIV wrapper de flash/highlight)
        bounds = page.evaluate(f"""() => {{
            const msgId = '{msg_id}';
            const el = document.querySelector(`li[id*="${{msgId}}"]`);
            if (!el) return null;

            function getOlChild(node) {{
                let c = node;
                while (c.parentElement && c.parentElement.tagName !== 'OL') c = c.parentElement;
                return c;
            }}
            function getLi(node) {{ return node.tagName === 'LI' ? node : node.querySelector('li'); }}
            function isGroupHead(node) {{
                const li = getLi(node);
                if (!li) return true;
                return !!(
                    li.querySelector('[class*="username"]') ||
                    li.querySelector('[class*="headerText"]') ||
                    li.querySelector('[class*="avatar"] img') ||
                    li.querySelector('img[class*="avatar"]')
                );
            }}

            const olChild = getOlChild(el);

            // Sobe ate achar a cabeca do grupo
            let head = olChild;
            if (!isGroupHead(olChild)) {{
                let cur = olChild.previousElementSibling;
                while (cur) {{
                    if (isGroupHead(cur)) {{ head = cur; break; }}
                    head = cur;
                    cur = cur.previousElementSibling;
                }}
            }}

            // Desce ate antes do proximo grupo
            let tail = olChild, cur = olChild.nextElementSibling;
            while (cur) {{
                if (isGroupHead(cur)) break;
                tail = cur;
                cur = cur.nextElementSibling;
            }}

            // Scrolla cabeca para o topo do viewport
            getLi(head)?.scrollIntoView({{ block: 'start', behavior: 'instant' }});

            return {{ scrolled: true }};
        }}""")

        time.sleep(0.5)

        # Segunda leitura apos scroll para coordenadas corretas
        bounds = page.evaluate(f"""() => {{
            const msgId = '{msg_id}';
            const el = document.querySelector(`li[id*="${{msgId}}"]`);
            if (!el) return null;

            function getOlChild(node) {{
                let c = node;
                while (c.parentElement && c.parentElement.tagName !== 'OL') c = c.parentElement;
                return c;
            }}
            function getLi(node) {{ return node.tagName === 'LI' ? node : node.querySelector('li'); }}
            function isGroupHead(node) {{
                const li = getLi(node);
                if (!li) return true;
                return !!(
                    li.querySelector('[class*="username"]') ||
                    li.querySelector('[class*="headerText"]') ||
                    li.querySelector('[class*="avatar"] img') ||
                    li.querySelector('img[class*="avatar"]')
                );
            }}

            const olChild = getOlChild(el);
            let head = olChild;
            if (!isGroupHead(olChild)) {{
                let cur = olChild.previousElementSibling;
                while (cur) {{
                    if (isGroupHead(cur)) {{ head = cur; break; }}
                    head = cur;
                    cur = cur.previousElementSibling;
                }}
            }}
            let tail = olChild, cur = olChild.nextElementSibling;
            while (cur) {{
                if (isGroupHead(cur)) break;
                tail = cur;
                cur = cur.nextElementSibling;
            }}

            const hb = head.getBoundingClientRect();
            const tb = tail.getBoundingClientRect();
            return {{ x: Math.floor(hb.x), y: Math.floor(hb.y), width: Math.ceil(hb.width), height: Math.ceil(tb.bottom - hb.y) }};
        }}""")

        if not bounds:
            raise ValueError("bounds null")

        clip = {
            "x":      bounds["x"],
            "y":      max(0, bounds["y"] - PADDING),
            "width":  bounds["width"],
            "height": bounds["height"] + PADDING * 2,
        }
        page.screenshot(path=str(out_path), clip=clip)
        return True

    except Exception as e:
        print(f"    Erro: {e}")
        try:
            msg_el.screenshot(path=str(out_path))
            return True
        except Exception as e2:
            print(f"    Fallback tambem falhou: {e2}")
            return False


def calibrate():
    """Abre o browser em uma mensagem real e aguarda o usuario ajustar a janela."""
    cases_index = load_json(WEB_DATA / "cases_index.json")
    if not cases_index:
        print("web/data/cases_index.json nao encontrado. Rode 08_report.py primeiro.")
        sys.exit(1)

    # Pega o primeiro caso grave como referencia
    sample = next((c for c in cases_index if c["label"] in SERIOUS_LABELS), cases_index[0])
    full   = load_json(WEB_DATA / "cases" / f"{sample['msg_id']}.json")
    ch_id  = full["channel_id"]
    msg_id = full["msg_id"]
    url    = f"https://discord.com/channels/{GUILD_ID}/{ch_id}/{msg_id}"

    print(f"Calibrando com mensagem: @{full['author']} | {full['ts']}")
    print(f"URL: {url}")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=CHROME_EXEC,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        if SESSION_FILE.exists():
            ctx = browser.new_context(storage_state=str(SESSION_FILE),
                                      viewport={"width": 960, "height": 900})
        else:
            print("session.json nao encontrado. Rode sem --calibrate primeiro para logar.")
            browser.close()
            sys.exit(1)

        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(3)

        print("=" * 60)
        print("  Ajuste o tamanho da janela do Chrome como preferir.")
        print("  Quando estiver satisfeito, pressione ENTER aqui.")
        print("=" * 60)
        input()

        # Le o tamanho atual da janela
        size = page.evaluate("() => ({ w: window.outerWidth, h: window.outerHeight, iw: window.innerWidth, ih: window.innerHeight })")
        print(f"\nTamanho detectado:")
        print(f"  Janela (outer):   {size['w']} x {size['h']}")
        print(f"  Viewport (inner): {size['iw']} x {size['ih']}")

        # Arredonda para multiplos de 10
        vw = round(size['iw'] / 10) * 10
        vh = round(size['ih'] / 10) * 10
        print(f"\n  -> Usar no script: VIEWPORT_WIDTH = {vw}, VIEWPORT_HEIGHT = {vh}")

        browser.close()
        return vw, vh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true", help="Abre browser para calibrar viewport")
    parser.add_argument("--all",       action="store_true", help="Incluir offensive/suspicious")
    parser.add_argument("--user",      help="Filtrar por username")
    parser.add_argument("--limit",     type=int, default=0)
    parser.add_argument("--dry-run",   action="store_true", help="Lista casos sem tirar prints")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
        return

    cases_index = load_json(WEB_DATA / "cases_index.json")
    if not cases_index:
        print("web/data/cases_index.json nao encontrado. Rode 08_report.py primeiro.")
        sys.exit(1)

    labels = ALL_LABELS if args.all else SERIOUS_LABELS
    targets = [c for c in cases_index if c["label"] in labels]

    if args.user:
        targets = [c for c in targets if c["author"].lower() == args.user.lower()]

    # Carrega detalhes completos de cada caso
    full_cases = []
    for c in targets:
        full = load_json(WEB_DATA / "cases" / f"{c['msg_id']}.json")
        if full:
            full_cases.append(full)

    # Remove já feitos
    pending = [c for c in full_cases if not already_done(c["msg_id"])]

    if args.limit:
        pending = pending[:args.limit]

    print(f"Casos graves: {len(full_cases)} | Ja com print: {len(full_cases)-len(pending)} | Pendentes: {len(pending)}")

    if args.dry_run:
        for c in pending:
            print(f"  [{c['label']}] @{c['author']} | {c['ts']} | {c['orig'][:60]}")
        return

    if not pending:
        print("Nada novo para processar.")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=CHROME_EXEC,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        if SESSION_FILE.exists():
            print("  Carregando sessao salva...")
            ctx = browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            )
            page = ctx.new_page()
            page.goto("https://discord.com/channels/@me", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            if "login" in page.url.lower():
                print("  Sessao expirada. Apague session.json e rode novamente.")
                browser.close()
                sys.exit(1)
        else:
            print("  Primeira execucao: faca login na janela que abriu.")
            ctx = browser.new_context(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
            page = ctx.new_page()
            page.goto("https://discord.com/login", wait_until="domcontentloaded")
            print("  Aguardando login...", end="", flush=True)
            while True:
                time.sleep(1)
                print(".", end="", flush=True)
                try:
                    page.wait_for_url("**/channels/**", timeout=1000)
                    break
                except Exception:
                    pass
            print(" OK!")
            time.sleep(2)
            ctx.storage_state(path=str(SESSION_FILE))
            print(f"  Sessao salva em session.json")

        print(f"  Browser pronto ({VIEWPORT_WIDTH}px viewport).\n")

        success = 0
        fail    = 0

        for i, c in enumerate(pending):
            msg_id  = c["msg_id"]
            ch_id   = c["channel_id"]
            author  = c["author"]
            label   = c["label"]

            out_dir = CARDS / author / msg_id
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / "screenshot.png"

            print(f"  [{i+1}/{len(pending)}] [{label}] @{author} | {c['ts']}")
            print(f"    {c['orig'][:70]}")

            ok = screenshot_message(page, ch_id, msg_id, out)

            if ok:
                info = {
                    "msg_id":          msg_id,
                    "discord_link":    c["discord_link"],
                    "timestamp_local": c["ts"],
                    "channel_id":      ch_id,
                    "channel_name":    c["channel_name"],
                    "author":          author,
                    "label":           label,
                    "confidence":      c["confidence"],
                    "orig":            c["orig"],
                    "en":              c.get("en", ""),
                    "pt":              c.get("pt", ""),
                    "reason":          c.get("reason", ""),
                }
                Path(out_dir / "info.json").write_text(
                    json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                success += 1
                print(f"    salvo: cards/{author}/{msg_id}/")
            else:
                fail += 1
                print(f"    falhou")

            time.sleep(0.8)

        browser.close()

    print(f"\n{success} prints | {fail} falhas")
    print(f"Execute 08_report.py para incluir os prints no web/")


if __name__ == "__main__":
    main()
