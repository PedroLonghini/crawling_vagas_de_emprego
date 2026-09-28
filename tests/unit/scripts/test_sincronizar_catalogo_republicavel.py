"""Testes do gerador do catálogo republicável."""

from urllib.parse import parse_qs, urlsplit

import pytest
from scripts.sincronizar_catalogo_republicavel import (
    CONSULTA_QUERIDO_DIARIO_COMBINADA,
    ErroSincronizacaoCatalogo,
    atualizar_consultas_querido_diario,
    criar_linhas_querido_diario,
    extrair_municipios_disponiveis,
)


def test_filtra_indisponiveis_e_cria_lotes_disjuntos() -> None:
    """Somente municípios cobertos aparecem uma única vez nas consultas."""

    municipios = extrair_municipios_disponiveis(
        {
            "cities": [
                {
                    "territory_id": f"3550{numero:03d}",
                    "territory_name": f"Cidade {numero}",
                    "state_code": "SP",
                    "availability_date": "2026-01-01",
                }
                for numero in range(1, 13)
            ]
            + [
                {
                    "territory_id": "3304557",
                    "territory_name": "Rio de Janeiro",
                    "state_code": "RJ",
                    "availability_date": "",
                }
            ]
        }
    )

    linhas = criar_linhas_querido_diario(municipios, tamanho_lote=10)
    codigos = [
        codigo
        for linha in linhas
        for codigo in parse_qs(urlsplit(linha["url_inicial"]).query)["territory_ids"]
    ]

    assert len(linhas) == 4
    assert len(codigos) == 24
    assert len(set(codigos)) == 12
    assert all(linha["status_politica"] == "aprovada" for linha in linhas)
    assert all(linha["republicacao_permitida"] == "true" for linha in linhas)
    assert "published_since" not in linhas[0]["url_inicial"]
    assert {linha["alvo_id"].rsplit("_", maxsplit=1)[-1] for linha in linhas} == {
        "empregos",
        "aprendizes",
    }


def test_rejeita_resposta_sem_municipios_disponiveis() -> None:
    """Uma falha da API não pode gerar um catálogo vazio."""

    with pytest.raises(ErroSincronizacaoCatalogo, match="nenhum município"):
        extrair_municipios_disponiveis(
            {
                "cities": [
                    {
                        "territory_id": "3550308",
                        "territory_name": "São Paulo",
                        "state_code": "SP",
                        "availability_date": "",
                    }
                ]
            }
        )


def test_municipio_tem_id_estavel_e_uma_busca_sem_sobreposicao() -> None:
    from scripts.sincronizar_catalogo_republicavel import MunicipioDisponivel

    cidade = MunicipioDisponivel("SP", "3550308", "São Paulo")
    anterior = MunicipioDisponivel("SP", "3500001", "Cidade anterior")
    primeiro = criar_linhas_querido_diario([cidade], por_municipio=True)[0]
    depois = criar_linhas_querido_diario([anterior, cidade], por_municipio=True)
    assert primeiro in depois  # incluir municípios não muda os IDs de outros
    assert primeiro["alvo_id"] == "querido_diario_ibge_3550308_oportunidades"
    assert primeiro["limite_paginas"] == "10"
    parametros = parse_qs(urlsplit(primeiro["url_inicial"]).query)
    assert parametros["territory_ids"] == ["3550308"]
    assert " | " in parametros["querystring"][0]


def test_atualiza_consulta_municipal_sem_mudar_politica_ou_limites() -> None:
    linhas = atualizar_consultas_querido_diario(
        (
            {
                "alvo_id": "querido_diario_ibge_3550308_oportunidades",
                "fonte": "querido_diario",
                "ativa": "true",
                "limite_paginas": "10",
                "status_politica": "aprovada",
                "url_inicial": (
                    "https://api.queridodiario.org.br/gazettes?territory_ids=3550308"
                    "&querystring=antiga&size=50"
                ),
            },
            {
                "alvo_id": "historico",
                "fonte": "querido_diario",
                "ativa": "false",
                "url_inicial": "https://api.queridodiario.org.br/gazettes?querystring=historica",
            },
        )
    )

    parametros = parse_qs(urlsplit(linhas[0]["url_inicial"]).query)
    assert parametros["territory_ids"] == ["3550308"]
    assert parametros["size"] == ["50"]
    assert parametros["querystring"] == [CONSULTA_QUERIDO_DIARIO_COMBINADA]
    assert linhas[0]["limite_paginas"] == "10"
    assert linhas[0]["status_politica"] == "aprovada"
    assert linhas[1]["url_inicial"].endswith("querystring=historica")


@pytest.mark.parametrize("tamanho", [0, 21])
def test_rejeita_tamanho_de_lote_inseguro(tamanho: int) -> None:
    """Lotes exagerados não devem sobrecarregar a API."""

    with pytest.raises(ValueError, match="entre 1 e 20"):
        criar_linhas_querido_diario((), tamanho_lote=tamanho)
