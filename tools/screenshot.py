#!/usr/bin/env python3
"""
screenshot.py — Tira prints do Discord para cada caso do pipeline.

Uso:
  venv/bin/python tools/screenshot.py                     # racist/xenophobic, headless
  venv/bin/python tools/screenshot.py --all               # todos os labels
  venv/bin/python tools/screenshot.py --workers 5         # 5 abas paralelas
  venv/bin/python tools/screenshot.py --show              # janela visivel (debug)
  venv/bin/python tools/screenshot.py --limit 5 --show   # debug de 5 casos
  venv/bin/python tools/screenshot.py --dry-run

Background:
  nohup venv/bin/python tools/screenshot.py --all --workers 5 > logs/screenshot.log 2>&1 &
  tail -f logs/screenshot.log
"""

import asyncio
import json
import sys
import time
import argparse
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import GUILD_ID

load_dotenv()

ROOT       = Path(__file__).parent.parent
WEB_DATA   = ROOT / "web" / "data"
CARDS      = ROOT / "cards"
LOGS       = ROOT / "logs"
CARDS.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

SESSION_FILE = ROOT / "session.json"
CHROME_EXEC  = "/bin/google-chrome"
VIEWPORT_W   = 980
VIEWPORT_H   = 900
PADDING      = 8
MAX_HEIGHT   = 360

SERIOUS_LABELS = {"racist", "xenophobic"}
ALL_LABELS     = {"racist", "xenophobic", "offensive", "suspicious"}

_JS_HIDE_OVERLAYS = """
() => {
    // Esconde por seletor de classe
    [
        '[class*="jumpToPresentBar"]', '[class*="newMessagesBar"]',
        '[class*="unreadPill"]',       '[class*="jumpToPresent"]',
        '[class*="tooltip"]',          '[class*="layerContainer"]',
    ].forEach(sel => {
        document.querySelectorAll(sel).forEach(el =>
            el.style.setProperty('display', 'none', 'important'));
    });

    // Esconde por conteudo de texto (cobre renomeacoes de classe do Discord)
    const keywords = ['mensagem nova', 'new message', 'mark as read', 'marcar como'];
    document.querySelectorAll('div, span, section').forEach(el => {
        if (el.children.length < 5) {
            const txt = el.innerText?.toLowerCase() || '';
            if (keywords.some(k => txt.includes(k)))
                el.style.setProperty('display', 'none', 'important');
        }
    });
}
"""

_JS_SCROLL_AND_BOUNDS = """
(msgId) => {
    const el = document.querySelector(`li[id*="${msgId}"]`);
    if (!el) return null;

    function getOlChild(node) {
        let c = node;
        while (c.parentElement && c.parentElement.tagName !== 'OL') c = c.parentElement;
        return c;
    }
    function getLi(node) { return node.tagName === 'LI' ? node : node.querySelector('li'); }
    function isGroupHead(node) {
        const li = getLi(node);
        if (!li) return true;
        return !!(
            li.querySelector('[class*="username"]') ||
            li.querySelector('[class*="headerText"]') ||
            li.querySelector('[class*="avatar"] img') ||
            li.querySelector('img[class*="avatar"]')
        );
    }

    const olChild = getOlChild(el);

    let head = olChild;
    if (!isGroupHead(olChild)) {
        let cur = olChild.previousElementSibling;
        while (cur) {
            if (isGroupHead(cur)) { head = cur; break; }
            head = cur;
            cur = cur.previousElementSibling;
        }
    }

    let tail = olChild;
    let cur = olChild.nextElementSibling;
    while (cur) {
        if (isGroupHead(cur)) break;
        tail = cur;
        cur = cur.nextElementSibling;
    }

    getLi(head)?.scrollIntoView({ block: 'start', behavior: 'instant' });
    return { scrolled: true };
}
"""

_JS_BOUNDS = """
(msgId) => {
    const el = document.querySelector(`li[id*="${msgId}"]`);
    if (!el) return null;

    function getOlChild(node) {
        let c = node;
        while (c.parentElement && c.parentElement.tagName !== 'OL') c = c.parentElement;
        return c;
    }
    function getLi(node) { return node.tagName === 'LI' ? node : node.querySelector('li'); }
    function isGroupHead(node) {
        const li = getLi(node);
        if (!li) return true;
        return !!(
            li.querySelector('[class*="username"]') ||
            li.querySelector('[class*="headerText"]') ||
            li.querySelector('[class*="avatar"] img') ||
            li.querySelector('img[class*="avatar"]')
        );
    }

    const olChild = getOlChild(el);
    let head = olChild;
    if (!isGroupHead(olChild)) {
        let cur = olChild.previousElementSibling;
        while (cur) {
            if (isGroupHead(cur)) { head = cur; break; }
            head = cur;
            cur = cur.previousElementSibling;
        }
    }
    let tail = olChild;
    let cur = olChild.nextElementSibling;
    while (cur) {
        if (isGroupHead(cur)) break;
        tail = cur;
        cur = cur.nextElementSibling;
    }

    const hb  = head.getBoundingClientRect();
    const tb  = tail.getBoundingClientRect();
    const elb = el.getBoundingClientRect();
    return {
        x:        Math.floor(hb.x),
        y:        Math.floor(hb.y),
        width:    Math.ceil(hb.width),
        height:   Math.ceil(tb.bottom - hb.y),
        target_y: Math.floor(elb.y),
        target_h: Math.ceil(elb.height),
    };
}
"""


def load_json(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def already_done(msg_id: str) -> bool:
    return next(CARDS.rglob(f"{msg_id}/screenshot.png"), None) is not None


async def screenshot_message(page, channel_id: str, msg_id: str, out_path: Path) -> bool:
    url = f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{msg_id}"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except PlaywrightTimeout:
        pass

    try:
        # Tenta o seletor especifico primeiro, cai em generico se demorar
        try:
            await page.wait_for_selector('ol[class*="scrollerInner"]', timeout=12000)
        except PlaywrightTimeout:
            await page.wait_for_selector('[class*="message"]', timeout=8000)
        await page.wait_for_selector(f'li[id*="{msg_id}"]', timeout=12000)
    except PlaywrightTimeout:
        print(f"    [{msg_id}] Timeout carregando mensagem")
        return False

    # Abre sidebar direita
    try:
        btn = await page.query_selector('[aria-label="Mostrar lista de membros"]')
        if btn:
            await btn.click()
            await page.wait_for_timeout(400)
        await page.mouse.move(480, 450)
        await page.wait_for_timeout(150)
    except Exception:
        pass

    # Esconde overlays
    try:
        await page.evaluate(_JS_HIDE_OVERLAYS)
    except Exception:
        pass

    msg_el = await page.query_selector(f'li[id*="{msg_id}"]')
    if not msg_el:
        return False

    try:
        # Aguarda a barra "N mensagens novas" sumir (aparece brevemente ao navegar por URL)
        try:
            await page.wait_for_selector(
                '[class*="newMessagesBar"], [class*="jumpToPresentBar"], [class*="unreadPill"]',
                state="hidden", timeout=4000
            )
        except PlaywrightTimeout:
            pass

        # Scroll da cabeca do grupo
        await page.evaluate(_JS_SCROLL_AND_BOUNDS, msg_id)
        await page.wait_for_timeout(350)
        await page.evaluate(_JS_HIDE_OVERLAYS)
        await page.wait_for_timeout(100)

        # Mede coordenadas
        bounds = await page.evaluate(_JS_BOUNDS, msg_id)
        if not bounds:
            raise ValueError("bounds null")

        full_h = bounds["height"] + PADDING * 2
        clip_y = max(0, bounds["y"] - PADDING)
        clip_h = full_h

        if full_h > MAX_HEIGHT:
            center = bounds["target_y"] + bounds["target_h"] / 2
            clip_y = max(0, int(center - MAX_HEIGHT / 2))
            clip_h = MAX_HEIGHT

        await page.screenshot(
            path=str(out_path),
            clip={"x": bounds["x"], "y": clip_y, "width": bounds["width"], "height": clip_h},
        )
        return True

    except Exception as e:
        print(f"    [{msg_id}] Erro: {e}")
        try:
            await msg_el.screenshot(path=str(out_path))
            return True
        except Exception:
            return False


async def worker(worker_id: int, ctx, job_queue: asyncio.Queue,
                 counters: dict, lock: asyncio.Lock, total: int, print_lock: asyncio.Lock):
    # Escalona o inicio para nao sobrecarregar o Discord com N logins simultaneos
    await asyncio.sleep(worker_id * 1.5)

    page = await ctx.new_page()
    try:
        await page.goto("https://discord.com/channels/@me", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1000)
    except Exception:
        pass

    while True:
        try:
            c = job_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        msg_id  = c["msg_id"]
        author  = c["author"]
        label   = c["label"]
        out_dir = CARDS / author / msg_id
        out_dir.mkdir(parents=True, exist_ok=True)

        async with print_lock:
            done = counters["ok"] + counters["fail"]
            print(f"  [w{worker_id}] [{done+1}/{total}] [{label}] @{author} | {c['orig'][:55]}")

        ok = await screenshot_message(page, c["channel_id"], msg_id, out_dir / "screenshot.png")

        async with lock:
            if ok:
                (out_dir / "info.json").write_text(
                    json.dumps({
                        "msg_id":       msg_id,
                        "discord_link": c["discord_link"],
                        "ts":           c["ts"],
                        "channel_id":   c["channel_id"],
                        "channel_name": c["channel_name"],
                        "author":       author,
                        "label":        label,
                        "confidence":   c["confidence"],
                        "orig":         c["orig"],
                        "en":           c.get("en", ""),
                        "pt":           c.get("pt", ""),
                        "reason":       c.get("reason", ""),
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                counters["ok"] += 1
            else:
                counters["fail"] += 1

        async with print_lock:
            status = "OK" if ok else "FALHOU"
            print(f"  [w{worker_id}] {status} -> {author}/{msg_id}")

        job_queue.task_done()

    await page.close()


async def run(pending: list, n_workers: int, headless: bool):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME_EXEC,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
        )

        # Verifica sessao
        check = await ctx.new_page()
        await check.goto("https://discord.com/channels/@me", wait_until="domcontentloaded", timeout=20000)
        await check.wait_for_timeout(1500)
        if "login" in check.url.lower():
            print("Sessao expirada. Apague session.json e rode com --show para logar de novo.")
            await browser.close()
            return
        await check.close()
        print(f"  Sessao OK. {len(pending)} jobs | {n_workers} workers\n")

        job_queue  = asyncio.Queue()
        for c in pending:
            await job_queue.put(c)

        counters   = {"ok": 0, "fail": 0}
        lock       = asyncio.Lock()
        print_lock = asyncio.Lock()

        t0 = time.time()
        await asyncio.gather(*[
            worker(i + 1, ctx, job_queue, counters, lock, len(pending), print_lock)
            for i in range(n_workers)
        ])
        elapsed = time.time() - t0

        await browser.close()
        print(f"\n{counters['ok']} OK | {counters['fail']} falhas | {elapsed:.0f}s")
        print("Rode 08_report.py para incluir os prints no web/")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",     action="store_true")
    parser.add_argument("--show",    action="store_true", help="Janela visivel (debug)")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--user",    help="Filtrar por username")
    parser.add_argument("--limit",   type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cases_index = load_json(WEB_DATA / "cases_index.json")
    if not cases_index:
        print("web/data/cases_index.json nao encontrado. Rode 08_report.py primeiro.")
        sys.exit(1)

    labels  = ALL_LABELS if args.all else SERIOUS_LABELS
    targets = [c for c in cases_index if c["label"] in labels]
    if args.user:
        targets = [c for c in targets if c["author"].lower() == args.user.lower()]

    full_cases = [
        fc for c in targets
        if (fc := load_json(WEB_DATA / "cases" / f"{c['msg_id']}.json"))
    ]
    pending = [c for c in full_cases if not already_done(c["msg_id"])]
    if args.limit:
        pending = pending[:args.limit]

    print(f"Total: {len(full_cases)} | Prontos: {len(full_cases)-len(pending)} | Pendentes: {len(pending)}")

    if args.dry_run:
        for c in pending:
            print(f"  [{c['label']}] @{c['author']} | {c['ts']} | {c['orig'][:60]}")
        return

    if not pending:
        print("Nada novo.")
        return

    if not SESSION_FILE.exists():
        print("session.json nao encontrado. Rode com --show uma vez para logar.")
        sys.exit(1)

    n_workers = min(args.workers, len(pending))
    headless  = not args.show
    print(f"  Modo: {'headless' if headless else 'visivel'} | {n_workers} worker(s)\n")

    asyncio.run(run(pending, n_workers, headless))


if __name__ == "__main__":
    main()
