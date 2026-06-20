"""Utilitarios compartilhados entre todas as etapas do ETL."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── logging ───────────────────────────────────────────────────────────────────

UTC_OFFSET = -3  # America/Sao_Paulo

def ts():
    return datetime.now().strftime("%H:%M:%S")

def to_local(utc_str: str) -> str:
    """Converte timestamp UTC do Discord (YYYY-MM-DD HH:MM:SS) para hora local."""
    try:
        from datetime import timedelta
        dt = datetime.strptime(utc_str[:16], "%Y-%m-%d %H:%M")
        dt = dt + timedelta(hours=UTC_OFFSET)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return utc_str[:16]

def log(msg: str):
    print(f"[{ts()}] {msg}", flush=True)

def log_progress(current: int, total: int, label: str = ""):
    pct = current / total * 100 if total else 0
    bar_len = 30
    filled = int(bar_len * current / total) if total else 0
    bar = "#" * filled + "-" * (bar_len - filled)
    suffix = f"  {label}" if label else ""
    print(f"\r[{ts()}] [{bar}] {current:,}/{total:,} ({pct:.1f}%){suffix}", end="", flush=True)

def log_section(title: str):
    line = "=" * 55
    print(f"\n{line}", flush=True)
    print(f"  {title}", flush=True)
    print(f"{line}", flush=True)

load_dotenv(Path(__file__).parent.parent / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GUILD_ID      = os.getenv("GUILD_ID", "1427722762809770126")

DATA_DIR = Path(__file__).parent.parent / "data"

# Canais hardcoded como fallback (Task Bar Hero)
_CHANNELS_FALLBACK = {
    "1510279576721428612": "chat-polish",
    "1427726870564180040": "chat-english",
    "1509047425933905970": "chat-brazil",
    "1509047989585580112": "chat-portugal",
    "1509469702890455110": "chat-spanish",
    "1509889866547069059": "chat-german",
    "1509930256046231744": "chat-russian",
    "1510258361004982322": "chat-turkish",
    "1427727873917059273": "chat-chinese",
    "1427727952942075984": "chat-japanese",
    "1428607413619003402": "chat-indonesian",
    "1463118321439215730": "chat-french",
    "1509630403202519161": "chat-thailand",
    "1509910911073128669": "chat-pilipino",
    "1510376330024452138": "chat-vietnam",
    "1427728219301216359": "chat-korean",
    "1427726892878004414": "tips-and-tricks",
    "1427727044678123630": "q-and-a",
    "1427727649156890808": "media-and-art",
    "1427726995378274324": "item-info",
}

def _load_channels() -> dict:
    cfg = DATA_DIR / "channels_config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            # Formato: {guild_id: {channel_id: name, ...}, ...}
            # Pega o guild atual se existir, senao pega o primeiro disponivel
            if GUILD_ID in data:
                return data[GUILD_ID]
            if data:
                return next(iter(data.values()))
        except Exception:
            pass
    return _CHANNELS_FALLBACK

ALL_CHANNELS: dict = _load_channels()

# ── paths ─────────────────────────────────────────────────────────────────────

def channel_dir(channel_id: str) -> Path:
    d = DATA_DIR / "channels" / channel_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def messages_path(channel_id: str) -> Path:
    return channel_dir(channel_id) / "messages.jsonl"

def authors_path() -> Path:
    return DATA_DIR / "authors.json"

def translations_path(channel_id: str) -> Path:
    return channel_dir(channel_id) / "translations.json"

def ai_review_path(channel_id: str) -> Path:
    return channel_dir(channel_id) / "ai_review.json"

def meta_path(channel_id: str) -> Path:
    return channel_dir(channel_id) / "meta.json"

def suspects_path() -> Path:
    return DATA_DIR / "suspects.json"

def pre_filtered_path(channel_id: str) -> Path:
    return channel_dir(channel_id) / "pre_filtered.json"


def save_meta(channel_id: str, meta: dict = None):
    """Salva meta.json no formato padrao, garantindo campos obrigatorios."""
    path = meta_path(channel_id)
    data = load_json(path, {})
    data.update(channel_id=channel_id,
                channel_name=ALL_CHANNELS.get(channel_id, channel_id),
                guild_id=GUILD_ID)
    if meta:
        data.update(meta)
    save_json(path, data)


# ── JSON helpers ──────────────────────────────────────────────────────────────

def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    return json.loads(p.read_text(encoding="utf-8"))

def save_json(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def load_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]

def append_jsonl(path, records: list[dict]):
    """Acrescenta registros ao arquivo JSONL, evitando duplicatas por ID."""
    existing_ids = {m["id"] for m in load_jsonl(path)}
    new = [r for r in records if r["id"] not in existing_ids]
    if not new:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new)

# ── Discord API ───────────────────────────────────────────────────────────────

_HEADERS = {
    "Authorization": DISCORD_TOKEN,
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
}

def api_get(url: str, params: dict = None) -> dict | list:
    while True:
        r = requests.get(url, headers=_HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            wait = float(r.json().get("retry_after", 5)) + 0.5
            print(f"  [rate limit] {wait:.1f}s...", end="\r")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()

def fetch_channel_info(channel_id: str) -> dict:
    return api_get(f"https://discord.com/api/v10/channels/{channel_id}")

def fetch_batch(channel_id: str, before: str = None, after: str = None) -> list[dict]:
    params = {"limit": 100}
    if before:
        params["before"] = before
    if after:
        params["after"] = after
    return api_get(
        f"https://discord.com/api/v10/channels/{channel_id}/messages", params
    )

SEARCH_URL = f"https://discord.com/api/v10/guilds/{GUILD_ID}/messages/search"


def estimate_channel_msgs(channel_id: str) -> int | None:
    """Retorna total de mensagens num canal via search API, ou None se falhar."""
    try:
        r = requests.get(
            SEARCH_URL,
            headers=_HEADERS,
            params={"channel_id": channel_id, "include_nsfw": "true"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("total_results")
    except Exception:
        pass
    return None


# ── Compact message format ────────────────────────────────────────────────────

def compact(m: dict) -> dict:
    """Converte mensagem raw da API para formato compacto."""
    author = m.get("author", {})
    ref    = m.get("referenced_message")
    atts   = [a["url"] for a in m.get("attachments", []) if a.get("url")]

    out = {
        "id": m["id"],
        "ts": m.get("timestamp", "")[:19].replace("T", " "),
        "a":  author.get("id", ""),
        "c":  m.get("content", ""),
    }
    if ref:
        out["ref"] = ref.get("id", "")
    if atts:
        out["att"] = atts

    return out

def compact_author(author: dict) -> dict:
    return {
        "u": author.get("username", "?"),
        "d": author.get("global_name") or author.get("username", "?"),
    }

def discord_link(channel_id: str, msg_id: str) -> str:
    return f"https://discord.com/channels/{GUILD_ID}/{channel_id}/{msg_id}"
