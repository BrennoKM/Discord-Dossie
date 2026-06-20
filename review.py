#!/usr/bin/env python3
"""
review.py — Abre interface de revisão no browser para confirmar/rejeitar infrações.
Salva resultado em db/reviewed.json

Uso:
  venv/bin/python review.py               # revisa todas pendentes
  venv/bin/python review.py --user xbiedro
  venv/bin/python review.py --only-serious
"""

import json
import argparse
import http.server
import threading
import webbrowser
from pathlib import Path

DB = Path("db")

def load_json(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

REVIEW_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Revisão de Infrações</title>
<style>
:root{--bg:#0f1117;--sur:#1a1d27;--card:#22263a;--bor:#2e3350;--acc:#5865f2;--red:#ed4245;--grn:#57f287;--ylw:#fee75c;--txt:#dcddde;--mut:#72767d}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'Segoe UI',sans-serif;font-size:14px}
header{background:var(--sur);border-bottom:2px solid var(--acc);padding:16px 24px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100}
header h1{font-size:16px;color:#fff}
.progress{color:var(--mut);font-size:13px}
.progress b{color:#fff}
.main{max-width:900px;margin:0 auto;padding:24px}
.infraction{background:var(--card);border:1px solid var(--bor);border-radius:10px;margin-bottom:32px;overflow:hidden}
.infraction.confirmed{border-color:var(--grn)}
.infraction.rejected{border-color:var(--red);opacity:.55}
.inf-header{padding:12px 16px;background:var(--sur);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.inf-header .author{font-weight:700;color:#fff}
.inf-header .meta{color:var(--mut);font-size:12px}
.badge{display:inline-block;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:600;text-transform:uppercase}
.badge.grave{background:var(--red);color:#fff}
.badge.sec{background:#7c5a0a;color:#fff}
.kws{font-size:11px;color:var(--red);margin-top:4px}
.context{padding:10px 16px;border-bottom:1px solid var(--bor)}
.ctx-msg{padding:4px 8px;margin:2px 0;border-radius:4px;font-size:12px}
.ctx-msg.target{background:rgba(88,101,242,.15);border-left:3px solid var(--acc)}
.ctx-author{font-weight:600;color:var(--acc);margin-right:6px;font-size:11px}
.ctx-ts{color:var(--mut);font-size:10px;margin-right:6px}
.original{background:#0a0c12;border-left:3px solid var(--acc);padding:8px 12px;font-family:monospace;font-size:13px;white-space:pre-wrap;word-break:break-word;margin:0 16px 0}
.original.serious{border-left-color:var(--red)}
.translations{display:flex;gap:8px;padding:8px 16px;flex-wrap:wrap}
.trans{flex:1;min-width:200px;background:var(--sur);border:1px solid var(--bor);border-radius:6px;padding:8px 10px}
.trans-lbl{font-size:10px;text-transform:uppercase;color:var(--mut);margin-bottom:3px;font-weight:600}
.trans-txt{font-size:12px}
.actions{display:flex;gap:10px;padding:12px 16px;border-top:1px solid var(--bor);align-items:center}
.btn{padding:7px 20px;border-radius:6px;border:none;cursor:pointer;font-size:13px;font-weight:600;transition:.15s}
.btn-confirm{background:var(--grn);color:#000}
.btn-confirm:hover{background:#3dc26f}
.btn-reject{background:var(--sur);border:1px solid var(--bor);color:var(--txt)}
.btn-reject:hover{background:var(--red);color:#fff;border-color:var(--red)}
.btn-link{background:none;border:none;color:var(--acc);cursor:pointer;font-size:12px;text-decoration:underline;padding:0}
.note{flex:1}
.note input{background:var(--sur);border:1px solid var(--bor);color:var(--txt);padding:5px 10px;border-radius:5px;font-size:12px;width:100%;max-width:300px}
.status-tag{font-size:11px;font-weight:600;padding:3px 9px;border-radius:10px}
.status-tag.confirmed{background:rgba(87,242,135,.15);color:var(--grn)}
.status-tag.rejected{background:rgba(237,66,69,.15);color:var(--red)}
.filter-bar{display:flex;gap:10px;padding:10px 0;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.filter-bar select,.filter-bar input{background:var(--sur);border:1px solid var(--bor);color:var(--txt);padding:5px 10px;border-radius:6px;font-size:12px}
</style>
</head>
<body>
<header>
  <h1>Revisão de Infrações — ETL Stage 2</h1>
  <div class="progress">
    <span id="prog-confirmed">0</span> confirmadas &nbsp;·&nbsp;
    <span id="prog-rejected">0</span> rejeitadas &nbsp;·&nbsp;
    <b id="prog-pending">0</b> pendentes
  </div>
</header>

<div class="main">
  <div class="filter-bar">
    <select id="filter-status" onchange="applyFilter()">
      <option value="pending">Só pendentes</option>
      <option value="all">Todas</option>
      <option value="confirmed">Só confirmadas</option>
      <option value="rejected">Só rejeitadas</option>
    </select>
    <select id="filter-author" onchange="applyFilter()">
      <option value="">Todos os autores</option>
      AUTHOR_OPTIONS
    </select>
    <input type="text" id="filter-search" placeholder="Buscar na mensagem..." oninput="applyFilter()">
  </div>

  <div id="list">CARDS_HTML</div>
</div>

<script>
const state = JSON.parse(localStorage.getItem('review_state') || '{}');
const notes = JSON.parse(localStorage.getItem('review_notes') || '{}');

function saveState() {
  localStorage.setItem('review_state', JSON.stringify(state));
  localStorage.setItem('review_notes', JSON.stringify(notes));
  updateProgress();
  // Envia para o servidor Python salvar
  fetch('/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({state, notes})
  });
}

function decide(id, decision) {
  state[id] = decision;
  const card = document.getElementById('card-' + id);
  card.className = 'infraction ' + decision;
  const tag = card.querySelector('.status-tag');
  tag.className = 'status-tag ' + decision;
  tag.textContent = decision === 'confirmed' ? '✓ Confirmada' : '✗ Rejeitada';
  saveState();
  applyFilter();
}

function setNote(id, val) {
  notes[id] = val;
  saveState();
}

function updateProgress() {
  const all = document.querySelectorAll('.infraction');
  let confirmed = 0, rejected = 0, pending = 0;
  all.forEach(c => {
    const s = state[c.dataset.id];
    if (s === 'confirmed') confirmed++;
    else if (s === 'rejected') rejected++;
    else pending++;
  });
  document.getElementById('prog-confirmed').textContent = confirmed;
  document.getElementById('prog-rejected').textContent = rejected;
  document.getElementById('prog-pending').textContent = pending;
}

function applyFilter() {
  const status = document.getElementById('filter-status').value;
  const author = document.getElementById('filter-author').value.toLowerCase();
  const q = document.getElementById('filter-search').value.toLowerCase();
  document.querySelectorAll('.infraction').forEach(c => {
    const s = state[c.dataset.id] || 'pending';
    const matchStatus = status === 'all' || s === status;
    const matchAuthor = !author || c.dataset.author === author;
    const matchQ = !q || c.dataset.content.includes(q);
    c.style.display = (matchStatus && matchAuthor && matchQ) ? '' : 'none';
  });
}

// Restaura estado salvo
document.querySelectorAll('.infraction').forEach(c => {
  const id = c.dataset.id;
  const s = state[id];
  if (s) {
    c.className = 'infraction ' + s;
    const tag = c.querySelector('.status-tag');
    tag.className = 'status-tag ' + s;
    tag.textContent = s === 'confirmed' ? '✓ Confirmada' : '✗ Rejeitada';
  }
  const noteInput = c.querySelector('input[type=text]');
  if (noteInput && notes[id]) noteInput.value = notes[id];
});
updateProgress();
applyFilter();
</script>
</body>
</html>"""


def build_html(infractions, reviewed):
    author_set = sorted({i["author_username"] for i in infractions})
    author_options = "\n".join(
        f'<option value="{a}">{a}</option>' for a in author_set
    )

    cards = []
    for inf in infractions:
        mid = inf["msg_id"]
        prev_state = reviewed.get(mid, {}).get("state", "pending")
        prev_note = reviewed.get(mid, {}).get("note", "")
        serious = inf.get("serious", False)

        # Contexto
        ctx_html = ""
        for c in inf.get("context_before", []):
            ctx_html += f'<div class="ctx-msg"><span class="ctx-ts">{c["ts"]}</span><span class="ctx-author">{c["author"]}</span>{c["content"]}</div>'
        ctx_html += f'<div class="ctx-msg target"><span class="ctx-ts">{inf["timestamp"]}</span><span class="ctx-author">➤ {inf["author_display"]}</span>{inf["content"]}</div>'
        for c in inf.get("context_after", []):
            ctx_html += f'<div class="ctx-msg"><span class="ctx-ts">{c["ts"]}</span><span class="ctx-author">{c["author"]}</span>{c["content"]}</div>'

        # Keywords
        kws_html = f'<div class="kws">⚠ {" · ".join(inf["flags"])}</div>' if inf["flags"] else ""

        # Traduções
        trans_html = ""
        if inf.get("translation_en") or inf.get("translation_pt"):
            trans_html = '<div class="translations">'
            if inf.get("translation_en"):
                trans_html += f'<div class="trans"><div class="trans-lbl">🇬🇧 English</div><div class="trans-txt">{inf["translation_en"]}</div></div>'
            if inf.get("translation_pt"):
                trans_html += f'<div class="trans"><div class="trans-lbl">🇧🇷 Português BR</div><div class="trans-txt">{inf["translation_pt"]}</div></div>'
            trans_html += "</div>"

        status_label = {"confirmed": "✓ Confirmada", "rejected": "✗ Rejeitada"}.get(prev_state, "Pendente")
        status_cls = prev_state if prev_state != "pending" else ""

        cards.append(f"""
<div class="infraction {status_cls}" id="card-{mid}" data-id="{mid}"
     data-author="{inf['author_username']}"
     data-content="{inf['content'].lower().replace('"', '').replace('<','').replace('>','')}">
  <div class="inf-header">
    <div>
      <span class="author">{inf['author_display']}</span>
      <span class="meta"> @{inf['author_username']} · #{inf['channel_name']} · {inf['timestamp']} UTC</span>
      {kws_html}
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      {'<span class="badge grave">GRAVE</span>' if serious else '<span class="badge sec">SUSPEITA</span>'}
      <span class="status-tag {status_cls}">{status_label}</span>
      <a href="{inf['discord_link']}" target="_blank" style="color:var(--acc);font-size:12px">↗ Discord</a>
    </div>
  </div>
  <div class="context">{ctx_html}</div>
  <div class="original {'serious' if serious else ''}">{inf['content']}</div>
  {trans_html}
  <div class="actions">
    <button class="btn btn-confirm" onclick="decide('{mid}','confirmed')">✓ Confirmar infração</button>
    <button class="btn btn-reject" onclick="decide('{mid}','rejected')">✗ Rejeitar (falso positivo)</button>
    <div class="note">
      <input type="text" placeholder="Nota opcional..." value="{prev_note}"
             onchange="setNote('{mid}', this.value)">
    </div>
  </div>
</div>""")

    html = REVIEW_HTML.replace("AUTHOR_OPTIONS", author_options).replace("CARDS_HTML", "\n".join(cards))
    return html


class Handler(http.server.BaseHTTPRequestHandler):
    html = ""
    reviewed_path = DB / "reviewed.json"

    def log_message(self, *a): pass  # silencia logs

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(Handler.html.encode("utf-8"))

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            reviewed = {}
            for mid, decision in body.get("state", {}).items():
                reviewed[mid] = {
                    "state": decision,
                    "note": body.get("notes", {}).get(mid, ""),
                }
            save_json(self.reviewed_path, reviewed)
            self.send_response(200)
            self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", help="Filtrar por username")
    parser.add_argument("--only-serious", action="store_true")
    parser.add_argument("--port", type=int, default=7654)
    args = parser.parse_args()

    infractions = load_json(DB / "infractions.json")
    if not infractions:
        print("db/infractions.json não encontrado. Rode build_db.py primeiro.")
        return

    if isinstance(infractions, list):
        pass
    else:
        infractions = list(infractions.values())

    if args.only_serious:
        infractions = [i for i in infractions if i.get("serious")]
    if args.user:
        infractions = [i for i in infractions if i["author_username"].lower() == args.user.lower()]

    # Ordena: graves primeiro, depois por autor
    infractions.sort(key=lambda i: (-i.get("serious", False), i["author_username"]))

    reviewed = load_json(DB / "reviewed.json") or {}
    print(f"{len(infractions)} infrações para revisar ({sum(1 for i in infractions if i.get('serious'))} graves)")
    print(f"{len(reviewed)} já revisadas anteriormente")

    Handler.html = build_html(infractions, reviewed)

    server = http.server.HTTPServer(("localhost", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"\nAbrindo revisão em {url}")
    print("Suas decisões são salvas automaticamente em db/reviewed.json")
    print("Feche o browser e pressione Ctrl+C para encerrar.\n")

    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nRevisão encerrada.")
        reviewed = load_json(DB / "reviewed.json") or {}
        confirmed = sum(1 for v in reviewed.values() if v["state"] == "confirmed")
        rejected = sum(1 for v in reviewed.values() if v["state"] == "rejected")
        print(f"  ✓ {confirmed} confirmadas | ✗ {rejected} rejeitadas")


if __name__ == "__main__":
    main()
