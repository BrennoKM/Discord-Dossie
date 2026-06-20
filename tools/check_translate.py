#!/usr/bin/env python3
"""
check_translate.py - Testa se o Google Translate esta acessivel neste IP.

Uso:
  python tools/check_translate.py
"""

import sys
import requests

TESTS = [
    "Dzisiaj jest piekna pogoda",
    "Nie rozumiem co mowisz",
    "Gdzie jest moj kot",
]

URL     = "https://translate.googleapis.com/translate_a/t"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def check():
    print("Testando Google Translate (endpoint batch, client=gtx)...\n")

    try:
        data   = [("q", t) for t in TESTS]
        params = {"client": "gtx", "sl": "pl", "tl": "en"}
        r = requests.post(URL, params=params, data=data, headers=HEADERS, timeout=10)
        results = r.json()

        ok = 0
        for text, item in zip(TESTS, results):
            translation = item if isinstance(item, str) else (item[0] if item else "")
            if translation and "Error" not in translation:
                print(f"  OK   '{text}'")
                print(f"       -> '{translation}'\n")
                ok += 1
            else:
                print(f"  FAIL '{text}'")
                print(f"       -> '{translation}'\n")

        print("-" * 50)
        if ok == len(TESTS):
            print(f"Livre! {ok}/{len(TESTS)} OK. Pode rodar o pipeline.")
        elif ok > 0:
            print(f"Parcial: {ok}/{len(TESTS)} OK.")
        else:
            print(f"Bloqueado. 0/{len(TESTS)} OK. Aguarde ou troque de IP.")

    except Exception as e:
        print(f"Erro na requisicao: {e}")
        print("Bloqueado ou sem conexao.")


if __name__ == "__main__":
    check()
