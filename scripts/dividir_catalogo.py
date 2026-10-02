"""Divide um catálogo (e sua lista de autorização) em partes independentes.

Cada parte vira uma pasta com ``catalogo_fontes.csv`` e ``fontes_autorizadas.csv``
próprios, e o estado incremental de cada uma fica na própria pasta (``.cache``).
Fontes do mesmo domínio ficam sempre na mesma parte, para o limite de ritmo por
site continuar valendo, e as partes saem com tamanhos parecidos.

Uso:
    python scripts/dividir_catalogo.py --catalogo config/lote_1000/catalogo_fontes.csv --partes 4
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit


def _ler_urls(caminho: Path) -> list[str]:
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo)
        if not leitor.fieldnames or "url" not in leitor.fieldnames:
            raise SystemExit(f"{caminho}: o CSV precisa ter a coluna 'url'")
        return [linha["url"].strip() for linha in leitor if linha.get("url", "").strip()]


def dividir(urls: list[str], partes: int) -> list[list[str]]:
    """Domínios inteiros, do maior para o menor, sempre na parte mais vazia."""

    por_dominio: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        por_dominio[(urlsplit(url).hostname or "").casefold()].append(url)

    resultado: list[list[str]] = [[] for _ in range(partes)]
    for _, grupo in sorted(por_dominio.items(), key=lambda item: len(item[1]), reverse=True):
        min(resultado, key=len).extend(grupo)
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogo", type=Path, required=True)
    parser.add_argument("--partes", type=int, required=True)
    parser.add_argument("--saida", type=Path, help="padrão: a pasta do catálogo")
    opcoes = parser.parse_args()

    if not 2 <= opcoes.partes <= 50:
        parser.error("--partes deve estar entre 2 e 50")

    urls = _ler_urls(opcoes.catalogo)
    # Só repassa a autorização das URLs que a lista de origem já autoriza.
    autorizacoes = opcoes.catalogo.with_name("fontes_autorizadas.csv")
    autorizadas = set(_ler_urls(autorizacoes)) if autorizacoes.is_file() else set()
    base = opcoes.saida or opcoes.catalogo.parent

    for numero, parte in enumerate(dividir(urls, opcoes.partes), start=1):
        pasta = base / f"parte_{numero}"
        pasta.mkdir(parents=True, exist_ok=True)
        for nome, selecao in (
            ("catalogo_fontes.csv", parte),
            ("fontes_autorizadas.csv", [u for u in parte if u in autorizadas]),
        ):
            with (pasta / nome).open("w", encoding="utf-8", newline="") as arquivo:
                escritor = csv.writer(arquivo)
                escritor.writerow(["url"])
                escritor.writerows([[u] for u in selecao])
        print(f"{pasta}: {len(parte)} fontes")
    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
