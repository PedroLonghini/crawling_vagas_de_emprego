"""Detecta agregadores pelos anúncios gravados: sites com vagas de muitas empresas.

    python scripts/detectar_agregadores.py            # mostra e grava a lista
    python scripts/detectar_agregadores.py --minimo 15

Grava ``config/agregadores_detectados.csv`` (dominio, empresas, anuncios). A leitura
usa essa lista para nunca tomar o nome do próprio site como empresa e, quando o
agregador não diz quem contrata, usar "confidential". Só lê o MongoDB.

Plataformas de recrutamento (Gupy, Quickin, Abler...) ficam de fora: nelas cada
página é de uma empresa, e a empresa vem da própria plataforma.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from urllib.parse import urlsplit

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.plataformas import plataforma_do_host
from observatorio_vagas.extraction.agregadores import ARQUIVO_DETECTADOS
from observatorio_vagas.extraction.listagem_carreiras import DOMINIOS_DE_TERCEIROS
from observatorio_vagas.storage.mongodb.connection import ConexaoMongoDB

# Com 5, sites de empresa com nomes variados (nu.com, Santa Casa: 9) entravam.
MINIMO_EMPRESAS = 10


def _dominio(url: str) -> str:
    return (urlsplit(url or "").hostname or "").casefold().removeprefix("www.")


def detectar(minimo: int) -> list[tuple[str, int, int]]:
    banco = ConexaoMongoDB(get_settings()).banco
    empresas: dict[str, set] = defaultdict(set)
    anuncios: dict[str, int] = defaultdict(int)
    for documento in banco["anuncios"].find({}, {"url": 1, "empresa_id": 1}):
        dominio = _dominio(documento.get("url"))
        if not dominio:
            continue
        anuncios[dominio] += 1
        if documento.get("empresa_id"):
            empresas[dominio].add(documento["empresa_id"])
    return sorted(
        (
            (dominio, len(ids), anuncios[dominio])
            for dominio, ids in empresas.items()
            if len(ids) >= minimo
            and plataforma_do_host(dominio) is None
            and not any(dominio == d or dominio.endswith("." + d) for d in DOMINIOS_DE_TERCEIROS)
        ),
        key=lambda item: -item[1],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--minimo", type=int, default=MINIMO_EMPRESAS, help="empresas distintas")
    opcoes = parser.parse_args()

    detectados = detectar(opcoes.minimo)
    with ARQUIVO_DETECTADOS.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(("dominio", "empresas", "anuncios"))
        escritor.writerows(detectados)
    print(f"{len(detectados)} agregador(es) detectado(s) -> {ARQUIVO_DETECTADOS}")
    for dominio, total_empresas, total_anuncios in detectados:
        print(f"  {dominio}: {total_empresas} empresas em {total_anuncios} anúncios")
    return 0


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
