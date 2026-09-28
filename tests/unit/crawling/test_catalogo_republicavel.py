"""Validação da lista simples de páginas de carreira."""

from pathlib import Path

from observatorio_vagas.crawling.catalog import carregar_alvos_csv
from observatorio_vagas.domain.enums import Fonte

CATALOGO = Path("config/catalogo_fontes.csv")


def test_catalogo_simples_contem_apenas_paginas_de_carreira_autorizadas() -> None:
    """As URLs registradas possuem autorização privada para publicar."""

    alvos = carregar_alvos_csv(CATALOGO)

    assert alvos
    assert all(alvo.ativa for alvo in alvos)
    assert all(alvo.habilitado_para_coleta for alvo in alvos)
    assert all(alvo.habilitado_para_publicacao for alvo in alvos)
    assert all(alvo.politica.autorizacao_escrita for alvo in alvos)
    assert all(alvo.fonte is Fonte.PAGINA_CARREIRAS for alvo in alvos)


def test_catalogo_operacional_nao_contem_bases_publicas_ou_consultas_municipais() -> None:
    """As fontes retiradas não podem voltar ao lote de publicação."""

    alvos = carregar_alvos_csv(CATALOGO)

    assert all(alvo.fonte not in {Fonte.CKAN, Fonte.QUERIDO_DIARIO} for alvo in alvos)
    assert all("queridodiario" not in alvo.dominio for alvo in alvos)
