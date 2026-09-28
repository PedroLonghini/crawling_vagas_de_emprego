"""Testes do contexto da página preservado pelo crawler."""

from datetime import UTC, datetime

import pytest
from scrapy.http import HtmlResponse, Request

from observatorio_vagas.crawling.adapters import converter_resposta_scrapy
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta

DATA_COLETA = datetime(
    2026,
    8,
    24,
    10,
    0,
    tzinfo=UTC,
)

CORPO_HTML = b"<html><body>Vaga Python</body></html>"


def criar_resposta_scrapy(
    meta: dict[str, object] | None = None,
) -> HtmlResponse:
    """Cria uma resposta do Scrapy para os testes."""

    requisicao = Request(
        url="https://empresa.example/carreiras",
        meta=meta or {},
    )

    return HtmlResponse(
        url="https://empresa.example/carreiras",
        request=requisicao,
        status=200,
        body=CORPO_HTML,
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("numero_pagina", "tipo_original", "tipo_esperado"),
    [
        (
            1,
            "inicial",
            TipoPaginaColeta.INICIAL,
        ),
        (
            2,
            "detalhe_vaga",
            TipoPaginaColeta.DETALHE_VAGA,
        ),
    ],
)
def test_adaptador_preserva_contexto_da_pagina(
    numero_pagina: int,
    tipo_original: str,
    tipo_esperado: TipoPaginaColeta,
) -> None:
    """Metadados da Request devem chegar à resposta bruta."""

    resposta_scrapy = criar_resposta_scrapy(
        meta={
            "observatorio_numero_pagina": numero_pagina,
            "observatorio_tipo_pagina": tipo_original,
        },
    )

    resposta_bruta = converter_resposta_scrapy(
        resposta=resposta_scrapy,
        fonte=Fonte.PAGINA_CARREIRAS,
        coletado_em=DATA_COLETA,
    )

    assert resposta_bruta.numero_pagina == numero_pagina
    assert resposta_bruta.tipo_pagina is tipo_esperado


def test_requisicao_sem_contexto_vira_pagina_avulsa() -> None:
    """Uma Request antiga deve continuar funcionando."""

    resposta_bruta = converter_resposta_scrapy(
        resposta=criar_resposta_scrapy(),
        fonte=Fonte.OUTRA,
        coletado_em=DATA_COLETA,
    )

    assert resposta_bruta.numero_pagina == 1
    assert resposta_bruta.tipo_pagina is TipoPaginaColeta.AVULSA


@pytest.mark.parametrize(
    "numero_invalido",
    [
        0,
        -1,
        True,
        "2",
    ],
)
def test_numero_de_pagina_invalido_e_rejeitado(
    numero_invalido: object,
) -> None:
    """O adaptador não deve aceitar numeração inválida."""

    resposta_scrapy = criar_resposta_scrapy(
        meta={
            "observatorio_numero_pagina": numero_invalido,
        },
    )

    with pytest.raises(
        ValueError,
        match="observatorio_numero_pagina inválido",
    ):
        converter_resposta_scrapy(
            resposta=resposta_scrapy,
            fonte=Fonte.OUTRA,
            coletado_em=DATA_COLETA,
        )


def test_tipo_de_pagina_desconhecido_e_rejeitado() -> None:
    """Um nome desconhecido não pode entrar no armazenamento."""

    resposta_scrapy = criar_resposta_scrapy(
        meta={
            "observatorio_tipo_pagina": "pagina_qualquer",
        },
    )

    with pytest.raises(
        ValueError,
        match="observatorio_tipo_pagina inválido",
    ):
        converter_resposta_scrapy(
            resposta=resposta_scrapy,
            fonte=Fonte.OUTRA,
            coletado_em=DATA_COLETA,
        )


def test_contrato_rejeita_numero_de_pagina_zero() -> None:
    """O próprio contrato também protege a numeração."""

    with pytest.raises(
        ValueError,
        match="pelo menos 1",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada="https://empresa.example/vaga",
            url_final="https://empresa.example/vaga",
            status_http=200,
            corpo=b"",
            numero_pagina=0,
        )


def test_contrato_rejeita_tipo_de_pagina_sem_enum() -> None:
    """O contrato exige uma opção oficial de TipoPaginaColeta."""

    with pytest.raises(
        TypeError,
        match="TipoPaginaColeta",
    ):
        RespostaBruta(
            fonte=Fonte.OUTRA,
            url_solicitada="https://empresa.example/vaga",
            url_final="https://empresa.example/vaga",
            status_http=200,
            corpo=b"",
            tipo_pagina="inicial",
        )
