#!/usr/bin/env python3
"""
06_report.py - Gera relatorio HTML e arquivo de exposicao a partir das classificacoes da IA.
So inclui mensagens classificadas como racist ou xenophobic.

Uso:
  python etl/06_report.py --channel 1510279576721428612
  python etl/06_report.py --channel chat-polish --min-confidence 0.8
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Template

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import (
    ALL_CHANNELS, load_json, save_json, load_jsonl,
    messages_path, authors_path, translations_path, ai_review_path,
    suspects_path, discord_link,
)

CONFIRMED_LABELS = {"racist", "xenophobic"}

HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dossie {{ channel_name }}</title>
<style>
:root{--bg:#0f1117;--sur:#1a1d27;--card:#22263a;--bor:#2e3350;--acc:#5865f2;--red:#ed4245;--grn:#57f287;--ylw:#fee75c;--txt:#dcddde;--mut:#72767d;--ora:#f57731}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'Segoe UI',sans-serif;font-size:14px}
header{background:var(--sur);border-bottom:2px solid var(--acc);padding:18px 28px;position:sticky;top:0;z-index:10}
header h1{font-size:18px;color:#fff}
header p{color:var(--mut);font-size:12px;margin-top:3px}
.stats{display:flex;gap:20px;padding:12px 28px;background:var(--sur);border-bottom:1px solid var(--bor);flex-wrap:wrap}
.stat .num{font-size:24px;font-weight:700;color:var(--acc)}
.stat .label{font-size:10px;color:var(--mut);text-transform:uppercase}
nav{display:flex;gap:4px;padding:8px 28px;background:var(--bg);border-bottom:1px solid var(--bor);flex-wrap:wrap}
nav button{background:var(--sur);border:1px solid var(--bor);color:var(--txt);padding:4px 14px;border-radius:20px;cursor:pointer;font-size:13px}
nav button.active{background:var(--acc);border-color:var(--acc);color:#fff}
.controls{padding:8px 28px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--bor)}
.controls input,.controls select{background:var(--sur);border:1px solid var(--bor);color:var(--txt);padding:4px 10px;border-radius:5px;font-size:12px}
.controls input{width:200px}
section{padding:16px 28px;display:none}
section.active{display:block}
h2{font-size:14px;font-weight:700;color:#fff;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--bor)}
.offender{background:var(--card);border:1px solid var(--bor);border-radius:8px;margin-bottom:8px;overflow:hidden}
.offender-hdr{padding:12px 14px;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.offender-hdr:hover{background:rgba(88,101,242,.1)}
.offender.open .arr{transform:rotate(180deg)}
.arr{transition:.2s;color:var(--mut)}
.badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:4px}
.badge{font-size:10px;padding:1px 7px;border-radius:10px;font-weight:600;text-transform:uppercase}
.badge.r{background:var(--red);color:#fff}
.badge.x{background:var(--ora);color:#fff}
.badge.o{background:#555;color:#fff}
.badge.cnt{background:var(--sur);border:1px solid var(--bor);color:var(--mut)}
.msgs-wrap{display:none;border-top:1px solid var(--bor);padding:8px 14px 14px}
.offender.open .msgs-wrap{display:block}
.msg{background:var(--sur);border:1px solid var(--bor);border-radius:6px;padding:10px 12px;margin-top:6px}
.msg.racist{border-left:3px solid var(--red)}
.msg.xenophobic{border-left:3px solid var(--ora)}
.msg.offensive{border-left:3px solid #555}
.msg-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;font-size:12px;color:var(--mut)}
.msg-link{color:var(--acc);text-decoration:none;margin-left:6px;opacity:.7}
.msg-link:hover{opacity:1}
.ai-label{font-size:10px;font-weight:600;text-transform:uppercase;padding:1px 6px;border-radius:8px}
.ai-label.racist{background:rgba(237,66,69,.2);color:var(--red)}
.ai-label.xenophobic{background:rgba(245,119,49,.2);color:var(--ora)}
.ai-label.offensive{background:rgba(80,80,80,.3);color:#aaa}
.original{font-family:monospace;font-size:12px;padding:6px 8px;background:var(--card);border-radius:4px;margin-bottom:5px;white-space:pre-wrap;word-break:break-word}
.trans{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px}
.tr-block{flex:1;min-width:150px;background:var(--card);border:1px solid var(--bor);border-radius:5px;padding:5px 8px}
.tr-lbl{font-size:10px;text-transform:uppercase;color:var(--mut);margin-bottom:2px;font-weight:600}
.tr-txt{font-size:12px}
.ai-reason{font-size:11px;color:var(--mut);font-style:italic;margin-top:3px}
.ctx{border-left:2px solid var(--bor);padding:3px 8px;margin-bottom:5px;color:var(--mut);font-size:11px}
.hidden{display:none!important}
.expose-box{background:#0a0c12;border:1px solid var(--bor);border-radius:6px;padding:8px 12px;margin-top:6px;font-size:11px;font-family:monospace;white-space:pre-wrap;color:var(--txt)}
.copy-btn{background:var(--sur);border:1px solid var(--bor);color:var(--txt);font-size:11px;padding:2px 8px;border-radius:4px;cursor:pointer;float:right;margin-bottom:4px}
.copy-btn:hover{background:var(--acc);border-color:var(--acc)}
</style>
</head>
<body>
<header>
  <h1>Dossie #{{ channel_name }} -- {{ guild_name }}</h1>
  <p>Gerado em {{ generated_at }} | Uso para denuncia e exposicao de racismo</p>
</header>

<div class="stats">
  <div class="stat"><div class="num">{{ stats.total_msgs }}</div><div class="label">Msgs analisadas</div></div>
  <div class="stat"><div class="num">{{ stats.racist }}</div><div class="label">Racistas</div></div>
  <div class="stat"><div class="num">{{ stats.xenophobic }}</div><div class="label">Xenofobicas</div></div>
  <div class="stat"><div class="num">{{ stats.offensive }}</div><div class="label">Ofensivas</div></div>
  <div class="stat"><div class="num">{{ stats.offenders }}</div><div class="label">Infratores</div></div>
</div>

<nav>
  <button class="active" onclick="showTab('tab-offenders',this)">Infratores ({{ offenders|length }})</button>
  <button onclick="showTab('tab-all',this)">Todas as infrações</button>
  <button onclick="showTab('tab-expose',this)">Cards de exposição</button>
</nav>

<div class="controls">
  <input type="text" id="search" placeholder="Buscar mensagem ou autor...">
  <select id="label-filter">
    <option value="">Todos os tipos</option>
    <option value="racist">Racista</option>
    <option value="xenophobic">Xenofobico</option>
    <option value="offensive">Ofensivo</option>
  </select>
</div>

<!-- INFRATORES -->
<section id="tab-offenders" class="active">
<h2>Infratores ordenados por gravidade</h2>
{% for o in offenders %}
<div class="offender" id="off-{{ o.user_id }}">
  <div class="offender-hdr" onclick="toggleOff('{{ o.user_id }}')">
    <div>
      <div style="font-weight:700;color:#fff">{{ o.display }} <span style="color:var(--mut);font-size:11px;font-weight:400">@{{ o.username }} ({{ o.user_id }})</span></div>
      <div class="badges">
        {% if o.racist_count %}<span class="badge r">{{ o.racist_count }} racista</span>{% endif %}
        {% if o.xenophobic_count %}<span class="badge x">{{ o.xenophobic_count }} xenofobia</span>{% endif %}
        {% if o.offensive_count %}<span class="badge o">{{ o.offensive_count }} ofensiva</span>{% endif %}
        <span class="badge cnt">{{ o.total_count }} msgs analisadas</span>
      </div>
    </div>
    <span class="arr">▼</span>
  </div>
  <div class="msgs-wrap">
    {% for msg in o.messages %}
    <div class="msg {{ msg.label }}"
         data-label="{{ msg.label }}"
         data-content="{{ msg.content | lower }}"
         data-author="{{ o.username }}">
      <div class="msg-hdr">
        <span>{{ msg.timestamp }} UTC
          <a class="msg-link" href="{{ msg.link }}" target="_blank">abrir no Discord</a>
        </span>
        <span>
          <span class="ai-label {{ msg.label }}">{{ msg.label }}</span>
          <span style="color:var(--mut);font-size:10px;margin-left:4px">{{ (msg.confidence * 100)|int }}%</span>
        </span>
      </div>
      {% if msg.ctx_before %}
      <div class="ctx">
        {% for c in msg.ctx_before %}<div><b>{{ c.author }}</b>: {{ c.content }}</div>{% endfor %}
      </div>
      {% endif %}
      <div class="original">{{ msg.content }}</div>
      {% if msg.translation_en or msg.translation_pt %}
      <div class="trans">
        {% if msg.translation_en %}<div class="tr-block"><div class="tr-lbl">EN</div><div class="tr-txt">{{ msg.translation_en }}</div></div>{% endif %}
        {% if msg.translation_pt %}<div class="tr-block"><div class="tr-lbl">PT-BR</div><div class="tr-txt">{{ msg.translation_pt }}</div></div>{% endif %}
      </div>
      {% endif %}
      <div class="ai-reason">IA: {{ msg.reason }}</div>
    </div>
    {% endfor %}
  </div>
</div>
{% endfor %}
</section>

<!-- TODAS AS INFRACOES -->
<section id="tab-all">
<h2>Todas as mensagens confirmadas pela IA</h2>
{% for msg in all_infractions %}
<div class="msg {{ msg.label }}" style="margin-bottom:6px"
     data-label="{{ msg.label }}"
     data-content="{{ msg.content | lower }}"
     data-author="{{ msg.username }}">
  <div class="msg-hdr">
    <span><b>{{ msg.display }}</b> @{{ msg.username }} | {{ msg.timestamp }} UTC
      <a class="msg-link" href="{{ msg.link }}" target="_blank">abrir no Discord</a>
    </span>
    <span class="ai-label {{ msg.label }}">{{ msg.label }}</span>
  </div>
  <div class="original">{{ msg.content }}</div>
  {% if msg.translation_en or msg.translation_pt %}
  <div class="trans">
    {% if msg.translation_en %}<div class="tr-block"><div class="tr-lbl">EN</div><div class="tr-txt">{{ msg.translation_en }}</div></div>{% endif %}
    {% if msg.translation_pt %}<div class="tr-block"><div class="tr-lbl">PT-BR</div><div class="tr-txt">{{ msg.translation_pt }}</div></div>{% endif %}
  </div>
  {% endif %}
  <div class="ai-reason">IA: {{ msg.reason }}</div>
</div>
{% endfor %}
</section>

<!-- CARDS DE EXPOSICAO -->
<section id="tab-expose">
<h2>Cards prontos para expor no Discord (copie e cole)</h2>
<p style="color:var(--mut);font-size:12px;margin-bottom:12px">Cada card inclui o texto bruto original para quem quiser verificar manualmente, mais as traducoes.</p>
{% for msg in expose_cards %}
<div style="margin-bottom:16px">
  <div style="color:var(--mut);font-size:11px;margin-bottom:4px">@{{ msg.username }} | {{ msg.timestamp }}</div>
  <button class="copy-btn" onclick="copyCard('card-{{ loop.index }}')">Copiar</button>
  <div class="expose-box" id="card-{{ loop.index }}">{{ msg.expose_text }}</div>
</div>
{% endfor %}
</section>

<script>
function showTab(id,btn){
  document.querySelectorAll('section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
  applyFilters();
}
function toggleOff(id){
  document.getElementById('off-'+id).classList.toggle('open');
}
function applyFilters(){
  const q=document.getElementById('search').value.toLowerCase();
  const lbl=document.getElementById('label-filter').value;
  document.querySelectorAll('.msg').forEach(m=>{
    const qOk=!q||m.dataset.content.includes(q)||(m.dataset.author||'').includes(q);
    const lOk=!lbl||m.dataset.label===lbl;
    m.classList.toggle('hidden',!(qOk&&lOk));
  });
}
document.getElementById('search').addEventListener('input',applyFilters);
document.getElementById('label-filter').addEventListener('change',applyFilters);
function copyCard(id){
  const txt=document.getElementById(id).textContent;
  navigator.clipboard.writeText(txt).then(()=>{
    const btn=document.querySelector(`[onclick="copyCard('${id}')"]`);
    btn.textContent='Copiado!';
    setTimeout(()=>btn.textContent='Copiar',1500);
  });
}
// Abre cards de infratores com muitas infrações automaticamente
document.querySelectorAll('.offender').forEach(c=>{
  const count=c.querySelectorAll('.msg').length;
  if(count>=3)c.classList.add('open');
});
</script>
</body>
</html>
"""


def build_expose_text(msg: dict, author: dict) -> str:
    lines = [
        f"**Infrator:** {author['d']} (@{author['u']}) | ID: {author.get('id','')}",
        f"**Canal:** #{msg['channel_name']} | {msg['timestamp']} UTC",
        f"**Classificacao:** {msg['label'].upper()} ({int(msg['confidence']*100)}% confianca)",
        f"**Link:** {msg['link']}",
        "",
        f"**Mensagem original:**",
        f"```",
        msg['content'],
        f"```",
    ]
    if msg.get("translation_en"):
        lines += [f"**EN:** {msg['translation_en']}"]
    if msg.get("translation_pt"):
        lines += [f"**PT-BR:** {msg['translation_pt']}"]
    lines += [f"", f"*Motivo (IA): {msg['reason']}*"]
    return "\n".join(lines)


def run(channel_id: str, min_confidence: float = 0.7):
    msgs     = load_jsonl(messages_path(channel_id))
    authors  = load_json(authors_path(channel_id), {})
    trans    = load_json(translations_path(channel_id), {})
    review   = load_json(ai_review_path(channel_id), {})
    ch_name  = ALL_CHANNELS.get(channel_id, channel_id)

    if not review:
        print("Nenhuma revisao de IA encontrada. Rode 05_ai_review.py primeiro.")
        return

    # Indexa mensagens por ID para acesso rapido
    msg_by_id = {m["id"]: m for m in msgs}

    # Filtra apenas confirmadas pela IA com confianca suficiente
    confirmed = []
    for mid, r in review.items():
        if r["label"] in CONFIRMED_LABELS and r["confidence"] >= min_confidence:
            m = msg_by_id.get(mid)
            if not m:
                continue
            author = authors.get(m["a"], {"u": "?", "d": "?"})
            t = trans.get(mid, {})
            confirmed.append({
                "msg_id":        mid,
                "channel_id":    channel_id,
                "channel_name":  ch_name,
                "timestamp":     m.get("ts", ""),
                "link":          discord_link(channel_id, mid),
                "user_id":       m["a"],
                "username":      author["u"],
                "display":       author["d"],
                "content":       m.get("c", ""),
                "translation_en": t.get("en", ""),
                "translation_pt": t.get("pt", ""),
                "label":         r["label"],
                "confidence":    r["confidence"],
                "reason":        r.get("reason", ""),
            })

    confirmed.sort(key=lambda x: (x["username"], x["timestamp"]))

    # Agrupa por infrator
    from collections import defaultdict
    by_user = defaultdict(list)
    for c in confirmed:
        by_user[c["user_id"]].append(c)

    offenders = []
    for uid, user_msgs in sorted(by_user.items(), key=lambda x: -len(x[1])):
        author = authors.get(uid, {"u": "?", "d": "?"})
        racist_count    = sum(1 for m in user_msgs if m["label"] == "racist")
        xenophobic_count = sum(1 for m in user_msgs if m["label"] == "xenophobic")
        offensive_count  = sum(1 for m in user_msgs if m["label"] == "offensive")

        # Contexto: pega 2 msgs anteriores para cada infração
        msgs_sorted = sorted(msgs, key=lambda m: m["id"])
        idx = {m["id"]: i for i, m in enumerate(msgs_sorted)}

        for c in user_msgs:
            i = idx.get(c["msg_id"], 0)
            ctx = []
            for prev in msgs_sorted[max(0, i-2):i]:
                prev_author = authors.get(prev["a"], {"u": "?"})
                ctx.append({"author": prev_author["u"], "content": prev.get("c", "")[:100]})
            c["ctx_before"] = ctx

        offenders.append({
            "user_id":          uid,
            "username":         author["u"],
            "display":          author["d"],
            "racist_count":     racist_count,
            "xenophobic_count": xenophobic_count,
            "offensive_count":  offensive_count,
            "total_count":      len(user_msgs),
            "messages":         user_msgs,
        })

    offenders.sort(key=lambda o: -(o["racist_count"] * 2 + o["xenophobic_count"]))

    # Cards de exposicao
    expose_cards = []
    for msg in confirmed:
        author = {"u": msg["username"], "d": msg["display"], "id": msg["user_id"]}
        expose_cards.append({
            **msg,
            "expose_text": build_expose_text(msg, author),
        })

    # Stats
    stats = {
        "total_msgs":  len(review),
        "racist":      sum(1 for r in review.values() if r["label"] == "racist"),
        "xenophobic":  sum(1 for r in review.values() if r["label"] == "xenophobic"),
        "offensive":   sum(1 for r in review.values() if r["label"] == "offensive"),
        "offenders":   len(offenders),
    }

    tmpl = Template(HTML)
    html = tmpl.render(
        channel_name=ch_name,
        guild_name="Task Bar Hero",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        stats=stats,
        offenders=offenders,
        all_infractions=confirmed,
        expose_cards=expose_cards,
    )

    out = Path(__file__).parent.parent / f"dossie_{ch_name}.html"
    out.write_text(html, encoding="utf-8")
    print(f"Relatorio salvo: {out}")
    print(f"  {stats['racist']} racistas | {stats['xenophobic']} xenofobicas | {len(offenders)} infratores")

    # Salva JSON de exposicao para uso externo
    expose_json = Path(__file__).parent.parent / "data" / f"expose_{channel_id}.json"
    save_json(expose_json, expose_cards)
    print(f"  Cards de exposicao: {expose_json}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--min-confidence", type=float, default=0.7)
    args = ap.parse_args()

    by_name = {v: k for k, v in ALL_CHANNELS.items()}
    ch_id   = by_name.get(args.channel, args.channel)
    run(ch_id, args.min_confidence)


if __name__ == "__main__":
    main()
