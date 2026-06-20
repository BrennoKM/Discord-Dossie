#!/usr/bin/env python3
"""
screenshot.py — Tira prints reais do Discord para cada infração grave.
Usa Playwright para abrir discord.com, fazer login com token e fotografar a mensagem.

Uso:
  venv/bin/python screenshot.py                  # screenshots de todas as graves
  venv/bin/python screenshot.py --user xbiedro   # só um usuário
  venv/bin/python screenshot.py --limit 20       # limita quantidade
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")

DB = Path("db")
CARDS = Path("cards")
CARDS.mkdir(exist_ok=True)

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def screenshot_message(page, channel_id, msg_id, output_path):
    """Navega para a mensagem no Discord e tira screenshot do grupo de mensagens."""
    url = f"https://discord.com/channels/1427722762809770126/{channel_id}/{msg_id}"

    try:
        page.goto(url, wait_until="networkidle", timeout=20000)
    except PlaywrightTimeout:
        pass  # networkidle pode timeout mas a página ainda carrega

    # Espera as mensagens aparecerem
    try:
        page.wait_for_selector('[class*="message"]', timeout=15000)
    except PlaywrightTimeout:
        print(f"    Timeout aguardando mensagem {msg_id}")
        # Salva screenshot da página para debug
        page.screenshot(path=str(output_path.parent / f"debug_{msg_id}.png"), full_page=False)
        return False

    time.sleep(2)

    # Tenta vários seletores que o Discord usa
    msg_el = (
        page.query_selector(f'li[id*="{msg_id}"]') or
        page.query_selector(f'div[id*="{msg_id}"]') or
        page.query_selector(f'[id$="{msg_id}"]') or
        page.query_selector(f'[data-id="{msg_id}"]')
    )

    if not msg_el:
        # Debug: mostra quais IDs existem na página
        ids = page.evaluate("""
            Array.from(document.querySelectorAll('li[id], div[id]'))
                .map(el => el.id)
                .filter(id => id.length > 10)
                .slice(0, 5)
        """)
        print(f"    Mensagem não encontrada. IDs disponíveis: {ids}")
        page.screenshot(path=str(output_path.parent / f"debug_{msg_id}.png"))
        return False

    # Discord agrupa mensagens consecutivas do mesmo autor em um <li class="groupStart...">
    # Sobe na DOM para pegar o grupo inteiro
    # O grupo começa em um li com avatar (classe groupStart ou similar)
    group_start = msg_el

    # Tenta subir até o início do grupo de mensagens
    try:
        # Procura o li pai que contém o avatar (início de grupo)
        parent = page.evaluate("""(el) => {
            let current = el;
            while (current) {
                if (current.tagName === 'LI' && current.querySelector('[class*="avatar"]')) {
                    return current.id;
                }
                // Sobe pela DOM mas não passa do container de mensagens
                let prev = current.previousElementSibling;
                if (prev && prev.tagName === 'LI') {
                    // Verifica se é continuação (sem avatar = mesmo grupo)
                    if (!prev.querySelector('[class*="avatar-"]')) {
                        current = prev;
                        continue;
                    }
                }
                break;
            }
            return current ? current.id : null;
        }""", msg_el)

        if parent:
            group_start = page.query_selector(f'#{parent}') or msg_el
    except Exception:
        pass

    # Screenshot do elemento
    try:
        group_start.screenshot(path=str(output_path))
        return True
    except Exception as e:
        # Fallback: screenshot do elemento original
        try:
            msg_el.screenshot(path=str(output_path))
            return True
        except Exception as e2:
            print(f"    Erro no screenshot: {e2}")
            return False


VESKTOP_PROFILE = Path.home() / ".config/vesktop/sessionData"
CHROME_EXECUTABLE = "/bin/google-chrome"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="Filtrar por username")
    parser.add_argument("--limit", type=int, default=0, help="Limite de screenshots")
    parser.add_argument("--all", action="store_true", help="Incluir todas as suspeitas, não só graves")
    args = parser.parse_args()

    infractions = load_json(DB / "infractions.json")

    if args.all:
        targets = infractions
    else:
        targets = [i for i in infractions if i.get("serious")]

    if args.user:
        targets = [i for i in targets if i["author_username"].lower() == args.user.lower()]

    if args.limit:
        targets = targets[:args.limit]

    print(f"Tirando {len(targets)} screenshots...")

    done_path = CARDS / "done.json"
    done = set(load_json(done_path)) if done_path.exists() else set()

    targets = [t for t in targets if t["msg_id"] not in done]
    print(f"  {len(targets)} ainda não processadas")

    if not targets:
        print("Nada novo para processar.")
        return

    with sync_playwright() as pw:
        SESSION_FILE = Path("session.json")
        browser = pw.chromium.launch(
            executable_path=CHROME_EXECUTABLE,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        if SESSION_FILE.exists():
            # Sessão salva — carrega e já está logado
            print("  Carregando sessão salva...")
            ctx = browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto("https://discord.com/channels/@me", wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            if "login" in page.url.lower():
                print("  Sessão expirada. Apague session.json e rode novamente para logar.")
                ctx.close()
                browser.close()
                sys.exit(1)
        else:
            # Primeira vez — abre login para o usuário logar manualmente
            print("  Primeira execução: faça login no Discord na janela que abriu.")
            print("  O script vai continuar automaticamente após o login.")
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.goto("https://discord.com/login", wait_until="domcontentloaded")

            # Aguarda o usuário logar (detecta quando sai da tela de login)
            print("  Aguardando login...", end="", flush=True)
            while "login" in page.url.lower() or "app" not in page.url.lower():
                time.sleep(1)
                print(".", end="", flush=True)
                try:
                    page.wait_for_url("**/channels/**", timeout=1000)
                    break
                except Exception:
                    pass
            print(" OK!")
            time.sleep(2)

            # Salva sessão para próximas execuções
            ctx.storage_state(path=str(SESSION_FILE))
            print(f"  Sessão salva em session.json — próximas execuções serão automáticas.")

        print("  Browser pronto.")

        success = 0
        fail = 0

        for i, inf in enumerate(targets):
            msg_id = inf["msg_id"]
            ch_id = inf["channel_id"]
            author = inf["author_username"]
            ts = inf["timestamp"].replace(":", "-").replace(" ", "_")
            fname = f"{author}_{ts}_{msg_id}.png"
            out = CARDS / fname

            print(f"  [{i+1}/{len(targets)}] @{author} — {inf['timestamp']}")
            print(f"    {inf['content'][:80]}")

            ok = screenshot_message(page, ch_id, msg_id, out)

            if ok:
                meta = {
                    "file": fname,
                    "msg_id": msg_id,
                    "discord_link": inf["discord_link"],
                    "timestamp": inf["timestamp"],
                    "channel": inf["channel_name"],
                    "author_id": inf["author_id"],
                    "author_username": author,
                    "author_display": inf["author_display"],
                    # Texto bruto para copiar/colar na mensagem de exposição
                    "content": inf["content"],
                    "translation_en": inf.get("translation_en", ""),
                    "translation_pt": inf.get("translation_pt", ""),
                    # Texto de exposição pronto para enviar no Discord
                    "expose_text": (
                        f"**Autor:** {inf['author_display']} (@{author})\n"
                        f"**Canal:** #{inf['channel_name']} · {inf['timestamp']} UTC\n"
                        f"**Link:** {inf['discord_link']}\n\n"
                        f"**Mensagem original:**\n```\n{inf['content']}\n```\n"
                        f"**Tradução (EN):** {inf.get('translation_en','')}\n"
                        f"**Tradução (PT-BR):** {inf.get('translation_pt','')}"
                    ),
                    "flags": inf["flags"],
                    "serious": inf.get("serious", False),
                }
                save_json(CARDS / fname.replace(".png", ".json"), meta)
                done.add(msg_id)
                success += 1
                print(f"    ✓ Salvo: cards/{fname}")
            else:
                fail += 1
                print(f"    ✗ Falhou")

            # Salva progresso a cada 10
            if (i + 1) % 10 == 0:
                save_json(done_path, list(done))

            time.sleep(0.8)

        save_json(done_path, list(done))
        browser.close()

    print(f"\n✓ {success} screenshots | ✗ {fail} falhas")
    print(f"Imagens em: cards/")


if __name__ == "__main__":
    main()
