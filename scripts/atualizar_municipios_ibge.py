"""Baixa a lista oficial de municípios do IBGE para completar a UF das cidades.

Gera ``config/municipios_ibge.csv`` (nome, uf). Rodar só quando o IBGE mudar a
lista (município novo é raro).
"""

from __future__ import annotations

import csv
import gzip
import json
import urllib.request
from pathlib import Path

URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
DESTINO = Path(__file__).resolve().parents[1] / "config" / "municipios_ibge.csv"


def main() -> int:
    with urllib.request.urlopen(URL, timeout=60) as resposta:
        corpo = resposta.read()
    # O IBGE devolve gzip mesmo sem o cliente pedir.
    if corpo[:2] == b"\x1f\x8b":
        corpo = gzip.decompress(corpo)
    dados = json.loads(corpo)
    linhas = []
    for municipio in dados:
        microrregiao = municipio.get("microrregiao") or {}
        regiao_imediata = municipio.get("regiao-imediata") or {}
        uf = (
            (microrregiao.get("mesorregiao") or {}).get("UF")
            or (regiao_imediata.get("regiao-intermediaria") or {}).get("UF")
            or {}
        ).get("sigla")
        if municipio.get("nome") and uf:
            linhas.append((municipio["nome"], uf))
    linhas.sort()
    with DESTINO.open("w", encoding="utf-8", newline="") as saida:
        escritor = csv.writer(saida)
        escritor.writerow(("nome", "uf"))
        escritor.writerows(linhas)
    print(f"{len(linhas)} municípios -> {DESTINO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
