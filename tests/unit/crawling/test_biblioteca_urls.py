"""Valida a lista simples de URLs das fontes."""

import csv
from pathlib import Path

from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.domain.enums import Fonte
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio

CATALOGO_CENTRAL = Path("config/catalogo_fontes.csv")


def test_catalogo_central_tem_uma_coluna_de_url() -> None:
    """O único arquivo operacional deve ser editável sem metadados técnicos."""

    with CATALOGO_CENTRAL.open(encoding="utf-8-sig", newline="") as arquivo:
        assert csv.DictReader(arquivo).fieldnames == ["url"]


def test_catalogo_central_contem_somente_paginas_de_carreira() -> None:
    """O catálogo canônico guarda candidatas de empresas, não bases públicas."""

    alvos = carregar_alvos_csv(CATALOGO_CENTRAL)

    assert alvos
    assert len({alvo.alvo_id.casefold() for alvo in alvos}) == len(alvos)
    assert all(alvo.ativa for alvo in alvos)
    assert all(alvo.fonte is Fonte.PAGINA_CARREIRAS for alvo in alvos)
    assert all(encontrar_restricao_dominio(alvo.dominio) is None for alvo in alvos)
    assert len({alvo.url_inicial for alvo in alvos}) == len(alvos)
