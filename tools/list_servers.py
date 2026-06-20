#!/usr/bin/env python3
"""
list_servers.py - Lista todos os servidores (guilds) que o token tem acesso.
Use para descobrir o GUILD_ID antes de configurar o .env.

Uso:
  python tools/list_servers.py
  python tools/list_servers.py --save   # salva channels_config.json de todos
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from etl.common import api_get, log, log_section
from tools.list_channels import list_channels


def fetch_guilds() -> list[dict]:
    return api_get("https://discord.com/api/v10/users/@me/guilds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="Salva canais de todos os servidores em channels_config.json")
    args = ap.parse_args()

    log_section("Servidores acessiveis com este token")

    try:
        guilds = fetch_guilds()
    except Exception as e:
        log(f"Erro ao buscar servidores: {e}")
        sys.exit(1)

    if not guilds:
        log("Nenhum servidor encontrado.")
        return

    print(f"\n  {'ID':<22} {'Nome'}")
    print(f"  {'-'*55}")
    for g in sorted(guilds, key=lambda x: x.get("name", "").lower()):
        owner = " (dono)" if g.get("owner") else ""
        print(f"  {g['id']:<22} {g['name']}{owner}")

    print(f"\n  Total: {len(guilds)} servidores")
    print(f"\n  Para usar um servidor, adicione ao .env:")
    print(f"  GUILD_ID=<id acima>")
    print(f"  Depois rode: python tools/list_channels.py\n")

    if args.save:
        log("Salvando canais de todos os servidores...")
        for g in guilds:
            log(f"  Buscando #{g['name']} ({g['id']})...")
            try:
                list_channels(g["id"], save=True)
            except Exception as e:
                log(f"  Erro em {g['name']}: {e}")
        log("Concluido. data/channels_config.json atualizado.")


if __name__ == "__main__":
    main()
