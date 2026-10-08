"""Testes das requisições autorizadas para páginas de detalhe."""

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, Response

from observatorio_vagas.crawling.catalog import AlvoColeta
from observatorio_vagas.crawling.request_factory import (
    criar_requisicao_inicial,
    criar_requisicoes_detalhe,
)
from observatorio_vagas.domain.enums import (
    Fonte,
    StatusPoliticaFonte,
)
from observatorio_vagas.domain.politica_fonte import PoliticaFonte


def callback_teste(
    response: Response,
    **dados: object,
) -> None:
    """Representa o callback usado pelas requisições."""

    del response, dados


def criar_alvo(
    *,
    limite_paginas: int = 3,
) -> AlvoColeta:
    """Cria um alvo aprovado e previsível."""

    return AlvoColeta(
        alvo_id="empresa_teste",
        empresa_nome="Empresa Teste",
        fonte=Fonte.OUTRA,
        url_inicial="https://empresa.example/carreiras",
        ativa=True,
        limite_paginas=limite_paginas,
        politica=PoliticaFonte(
            dominio="empresa.example",
            status=StatusPoliticaFonte.APROVADA,
            licenca_nome="Licença aberta de teste",
            licenca_url="https://licencas.example/licenca-aberta",
            republicacao_permitida=True,
        ),
    )


def criar_resposta_inicial(
    *,
    limite_paginas: int = 3,
) -> HtmlResponse:
    """Cria uma resposta que nasceu na fábrica autorizada."""

    requisicao = criar_requisicao_inicial(
        alvo=criar_alvo(
            limite_paginas=limite_paginas,
        ),
        callback=callback_teste,
    )

    assert isinstance(
        requisicao,
        Request,
    )

    return HtmlResponse(
        url=requisicao.url,
        request=requisicao,
        status=200,
        body=b"<html></html>",
        encoding="utf-8",
    )


def test_requisicao_inicial_recebe_numero_e_tipo() -> None:
    """A primeira página precisa ser identificada explicitamente."""

    resposta = criar_resposta_inicial()

    meta = resposta.request.meta

    assert meta["observatorio_numero_pagina"] == 1

    assert meta["observatorio_tipo_pagina"] == "inicial"


def test_requisicao_workday_publica_usa_post_com_paginacao() -> None:
    from observatorio_vagas.crawling.request_factory import _configuracao_requisicao_especial

    metodo, corpo, cabecalhos = _configuracao_requisicao_especial(
        "https://alliancewd.wd3.myworkdayjobs.com/wday/cxs/alliancewd/"
        "renault-group-careers/jobs?limit=20&offset=40"
    )

    assert metodo == "POST"
    assert corpo == b'{"appliedFacets": {}, "limit": 20, "offset": 40, "searchText": ""}'
    assert cabecalhos["Content-Type"] == "application/json"


def test_detalhes_respeitam_dominio_deduplicacao_e_limite() -> None:
    """Somente detalhes seguros devem virar requisições."""

    resposta = criar_resposta_inicial(
        limite_paginas=3,
    )

    requisicoes = criar_requisicoes_detalhe(
        resposta=resposta,
        urls=[
            "/vagas/1",
            "https://empresa.example/vagas/1#descricao",
            "https://externa.example/vagas/2",
            "https://www.empregos.com.br/vagas/3",
            "mailto:rh@empresa.example",
            "/vagas/2",
            "/vagas/3",
        ],
        callback=callback_teste,
    )

    assert [requisicao.url for requisicao in requisicoes] == [
        "https://empresa.example/vagas/1",
        "https://empresa.example/vagas/2",
    ]

    assert [requisicao.meta["observatorio_numero_pagina"] for requisicao in requisicoes] == [
        2,
        3,
    ]


def test_concurso_publico_nao_consume_orcamento_de_coleta() -> None:
    """O link de concurso é descartado antes de qualquer download."""

    resposta = criar_resposta_inicial(limite_paginas=3)

    requisicoes = criar_requisicoes_detalhe(
        resposta=resposta,
        urls=["/concursos/edital-2026", "/vagas/analista"],
        callback=callback_teste,
    )

    assert [requisicao.url for requisicao in requisicoes] == [
        "https://empresa.example/vagas/analista"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.empregos.com.br/vagas/1",
        "https://br.indeed.com/viewjob?jk=1",
        "https://www.infojobs.com.br/vaga-de-teste.aspx",
        "https://www.catho.com.br/vagas/teste",
    ],
)
def test_dominio_restrito_nao_gera_detalhe_mesmo_com_contexto_forjado(
    url: str,
) -> None:
    """A fábrica aplica a lista central além da verificação do middleware."""

    dominio = url.split("/", maxsplit=3)[2]
    requisicao = Request(
        url=f"https://{dominio}/vagas",
        callback=callback_teste,
        cb_kwargs={
            "alvo_id": "forjado",
            "empresa_nome": "Fonte proibida",
            "fonte": "outra",
        },
        meta={
            "observatorio_requisicao_autorizada": True,
            "observatorio_alvo_id": "forjado",
            "observatorio_dominio": dominio,
            "observatorio_limite_paginas": 2,
            "observatorio_status_politica": "aprovada",
            "observatorio_publicacao_autorizada_na_coleta": True,
            "observatorio_numero_pagina": 1,
            "observatorio_tipo_pagina": "inicial",
        },
    )
    resposta = HtmlResponse(
        url=requisicao.url,
        request=requisicao,
        status=200,
        body=b"<html></html>",
        encoding="utf-8",
    )

    assert (
        criar_requisicoes_detalhe(
            resposta=resposta,
            urls=[url],
            callback=callback_teste,
        )
        == ()
    )


def test_detalhe_preserva_contexto_e_autorizacao() -> None:
    """Cada detalhe deve continuar ligado ao alvo original."""

    resposta = criar_resposta_inicial()

    requisicao = criar_requisicoes_detalhe(
        resposta=resposta,
        urls=[
            "/vagas/123",
        ],
        callback=callback_teste,
    )[0]

    assert requisicao.cb_kwargs == {
        "alvo_id": "empresa_teste",
        "empresa_nome": "Empresa Teste",
        "fonte": "outra",
    }

    assert requisicao.meta["observatorio_requisicao_autorizada"] is True

    assert requisicao.meta["observatorio_dominio"] == "empresa.example"

    assert requisicao.meta["observatorio_tipo_pagina"] == "detalhe_vaga"


def test_limite_um_nao_permite_pagina_de_detalhe() -> None:
    """Limite um reserva toda a coleta para a página inicial."""

    resposta = criar_resposta_inicial(
        limite_paginas=1,
    )

    requisicoes = criar_requisicoes_detalhe(
        resposta=resposta,
        urls=[
            "/vagas/1",
        ],
        callback=callback_teste,
    )

    assert requisicoes == ()


def test_requisicao_sem_marcador_nao_gera_detalhes() -> None:
    """Uma resposta externa à fábrica não pode ampliar a coleta."""

    requisicao = Request(
        url="https://empresa.example/carreiras",
    )

    resposta = HtmlResponse(
        url=requisicao.url,
        request=requisicao,
        status=200,
        body=b"<html></html>",
        encoding="utf-8",
    )

    requisicoes = criar_requisicoes_detalhe(
        resposta=resposta,
        urls=[
            "/vagas/1",
        ],
        callback=callback_teste,
    )

    assert requisicoes == ()


def test_pagina_de_detalhe_nao_descobre_novas_paginas() -> None:
    """Esta primeira versão permite somente um nível de descoberta."""

    resposta = criar_resposta_inicial()

    meta = {
        **resposta.request.meta,
        "observatorio_numero_pagina": 2,
    }

    requisicao = resposta.request.replace(
        meta=meta,
    )

    resposta_detalhe = resposta.replace(
        request=requisicao,
    )

    requisicoes = criar_requisicoes_detalhe(
        resposta=resposta_detalhe,
        urls=[
            "/vagas/seguinte",
        ],
        callback=callback_teste,
    )

    assert requisicoes == ()


def test_urls_nao_pode_ser_um_texto_isolado() -> None:
    """Uma URL isolada não pode ser confundida com coleção."""

    resposta = criar_resposta_inicial()

    with pytest.raises(
        TypeError,
        match="coleção",
    ):
        criar_requisicoes_detalhe(
            resposta=resposta,
            urls="/vagas/1",
            callback=callback_teste,
        )
