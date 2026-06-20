#!/usr/bin/env python3
"""
list_channels.py - Lista todos os canais de texto de um servidor Discord
e salva em data/channels_config.json para o pipeline usar.

Uso:
  python tools/list_channels.py
  python tools/list_channels.py --guild 1234567890   # outro servidor
  python tools/list_channels.py --show               # so exibe, nao salva
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import api_get, GUILD_ID, DATA_DIR, log, log_section

# Tipos de canal de texto no Discord
TEXT_CHANNEL_TYPES = {
    0:  "texto",
    5:  "anuncio",
    10: "thread-anuncio",
    11: "thread-publica",
    12: "thread-privada",
    15: "forum",
}

CONFIG_FILE = DATA_DIR / "channels_config.json"


def fetch_guild_channels(guild_id: str) -> list[dict]:
    return api_get(f"https://discord.com/api/v10/guilds/{guild_id}/channels")


def fetch_guild_info(guild_id: str) -> dict:
    return api_get(f"https://discord.com/api/v10/guilds/{guild_id}")


def clean_name(name: str) -> str:
    """Remove emojis e caracteres nao-ASCII do inicio do nome do canal."""
    return name.encode("ascii", "ignore").decode().strip().lstrip("-_ ")


def list_channels(guild_id: str, save: bool = True):

    try:
        guild = fetch_guild_info(guild_id)
        guild_name = guild.get("name", guild_id)
        log(f"Servidor: {guild_name} ({guild_id})")
    except Exception as e:
        log(f"Nao foi possivel buscar info do servidor: {e}")
        guild_name = guild_id

    try:
        channels = fetch_guild_channels(guild_id)
    except Exception as e:
        log(f"Erro ao buscar canais: {e}")
        sys.exit(1)

    # Filtra apenas canais de texto e organiza por categoria
    text_channels = [
        c for c in channels
        if c.get("type") in TEXT_CHANNEL_TYPES
    ]

    # Mapeia categorias (tipo 4)
    categories = {
        c["id"]: c["name"]
        for c in channels
        if c.get("type") == 4
    }

    # Agrupa por categoria
    by_category: dict[str, list] = {}
    no_category = []
    for c in sorted(text_channels, key=lambda x: x.get("position", 0)):
        parent = c.get("parent_id")
        cat    = categories.get(parent, None) if parent else None
        if cat:
            by_category.setdefault(cat, []).append(c)
        else:
            no_category.append(c)

    # Exibe
    print(f"\n  Servidor: {guild_name}")
    print(f"  {len(text_channels)} canais de texto encontrados\n")

    channel_map = {}  # {id: name}

    def show_group(title: str, chs: list):
        if not chs:
            return
        print(f"  [{title}]")
        for c in chs:
            tipo = TEXT_CHANNEL_TYPES.get(c["type"], "?")
            name     = c["name"]
            cid      = c["id"]
            cleaned  = clean_name(name)
            channel_map[cid] = cleaned
            print(f"    {cid}  #{name:<35} -> '{cleaned}' ({tipo})")
        print()

    if no_category:
        show_group("sem categoria", no_category)
    for cat_name, chs in by_category.items():
        show_group(cat_name, chs)

    if not save:
        return channel_map

    # Salva / atualiza channels_config.json
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            config = {}

    config[guild_id] = channel_map
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"Salvo em data/channels_config.json ({len(channel_map)} canais para guild {guild_id})")
    log(f"Pipeline vai usar esses canais automaticamente.")

    return channel_map


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guild", default=GUILD_ID, help="ID do servidor (padrao: GUILD_ID do .env)")
    ap.add_argument("--show",  action="store_true", help="So exibe, nao salva")
    args = ap.parse_args()

    list_channels(args.guild, save=not args.show)


if __name__ == "__main__":
    main()
