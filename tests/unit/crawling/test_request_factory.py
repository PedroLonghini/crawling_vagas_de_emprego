"""Testes da criação segura de requisições do Scrapy."""

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from scrapy import Request
from scrapy.http import Response

from observatorio_vagas.crawling.catalog import AlvoColeta
from observatorio_vagas.crawling.request_factory import (
    criar_requisicao_inicial,
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
    """Representa o callback que futuramente processará a resposta."""

    # A função não precisa executar nada nestes testes.
    # O del evita avisos sobre parâmetros não utilizados.
    del response, dados


def criar_alvo(
    *,
    status: StatusPoliticaFonte,
    ativa: bool = True,
    dominio: str = "empresa.example",
    fonte: Fonte = Fonte.OUTRA,
    url_inicial: str | None = None,
) -> AlvoColeta:
    """Cria um alvo previsível para os testes."""

    return AlvoColeta(
        alvo_id="empresa_teste",
        empresa_nome="Empresa Teste",
        fonte=fonte,
        url_inicial=url_inicial or f"https://{dominio}/carreiras",
        ativa=ativa,
        limite_paginas=5,
        politica=PoliticaFonte(
            dominio=dominio,
            status=status,
            licenca_nome=(
                "Licença aberta de teste" if status is StatusPoliticaFonte.APROVADA else ""
            ),
            licenca_url=(
                "https://licencas.example/licenca-aberta"
                if status is StatusPoliticaFonte.APROVADA
                else ""
            ),
            republicacao_permitida=(status is StatusPoliticaFonte.APROVADA),
        ),
    )


def test_fonte_aprovada_cria_requisicao_com_contexto() -> None:
    """A requisição deve carregar identificação e auditoria do alvo."""

    alvo = criar_alvo(
        status=StatusPoliticaFonte.APROVADA,
    )

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
    )

    assert isinstance(requisicao, Request)
    assert requisicao.url == "https://empresa.example/carreiras"
    assert requisicao.callback is callback_teste

    # Estes argumentos serão entregues ao callback do spider.
    assert requisicao.cb_kwargs == {
        "alvo_id": "empresa_teste",
        "empresa_nome": "Empresa Teste",
        "fonte": "outra",
    }

    # Estes metadados poderão ser usados em logs e auditorias.
    # Confirma que a fábrica colocou o marcador de autorização.
    assert requisicao.meta["observatorio_requisicao_autorizada"] is True
    assert requisicao.meta["observatorio_alvo_id"] == "empresa_teste"
    assert requisicao.meta["observatorio_dominio"] == "empresa.example"
    assert requisicao.meta["observatorio_limite_paginas"] == 5
    assert requisicao.meta["observatorio_status_politica"] == "aprovada"

    assert requisicao.meta["observatorio_publicacao_autorizada_na_coleta"] is True


def test_smartrecruiters_inicia_pela_api_publica_da_mesma_empresa() -> None:
    alvo = criar_alvo(
        status=StatusPoliticaFonte.APROVADA,
        dominio="jobs.smartrecruiters.com",
        fonte=Fonte.PAGINA_CARREIRAS,
        url_inicial="https://jobs.smartrecruiters.com/BoschGroup",
    )

    requisicao = criar_requisicao_inicial(alvo=alvo, callback=callback_teste)

    assert isinstance(requisicao, Request)
    assert requisicao.url == (
        "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100&offset=0"
    )
    assert requisicao.meta["observatorio_dominio"] == "jobs.smartrecruiters.com"


def test_somente_coleta_cria_requisicao_sem_publicacao() -> None:
    """A coleta interna pode ocorrer sem autorizar republicação."""

    alvo = criar_alvo(
        status=StatusPoliticaFonte.SOMENTE_COLETA,
    )

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
    )

    assert isinstance(requisicao, Request)

    assert requisicao.meta["observatorio_publicacao_autorizada_na_coleta"] is False


def test_querido_diario_recebe_janela_movel_sem_perder_municipios() -> None:
    """A consulta diária busca sete dias e preserva códigos IBGE repetidos."""

    alvo = criar_alvo(
        status=StatusPoliticaFonte.APROVADA,
        dominio="api.queridodiario.org.br",
        fonte=Fonte.QUERIDO_DIARIO,
        url_inicial=(
            "https://api.queridodiario.org.br/gazettes?"
            "territory_ids=3550308&territory_ids=3304557&size=50"
        ),
    )

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
        data_referencia=date(2026, 9, 3),
    )

    assert isinstance(requisicao, Request)
    parametros = parse_qs(urlsplit(requisicao.url).query)
    assert parametros["territory_ids"] == ["3550308", "3304557"]
    assert parametros["published_since"] == ["2026-08-27"]


def test_querido_diario_respeita_janela_explicita_do_catalogo() -> None:
    """Uma consulta histórica explícita não deve ser alterada."""

    url = (
        "https://api.queridodiario.org.br/gazettes?territory_ids=3550308&published_since=2026-01-01"
    )
    alvo = criar_alvo(
        status=StatusPoliticaFonte.APROVADA,
        dominio="api.queridodiario.org.br",
        fonte=Fonte.QUERIDO_DIARIO,
        url_inicial=url,
    )

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
        data_referencia=date(2026, 9, 3),
    )

    assert isinstance(requisicao, Request)
    assert requisicao.url == url


@pytest.mark.parametrize(
    "status",
    [
        StatusPoliticaFonte.PENDENTE,
        StatusPoliticaFonte.BLOQUEADA,
        StatusPoliticaFonte.DESATIVADA,
    ],
)
def test_status_restrito_nao_cria_requisicao(
    status: StatusPoliticaFonte,
) -> None:
    """Uma fonte sem autorização não deve produzir Request."""

    alvo = criar_alvo(status=status)

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
    )

    assert requisicao is None


def test_alvo_operacionalmente_inativo_nao_cria_requisicao() -> None:
    """O botão operacional também deve impedir o acesso HTTP."""

    alvo = criar_alvo(
        status=StatusPoliticaFonte.APROVADA,
        ativa=False,
    )

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
    )

    assert requisicao is None


def test_empregos_bloqueado_nao_cria_requisicao() -> None:
    """O Empregos não deve gerar requisição no crawler de fontes."""

    alvo = criar_alvo(
        status=StatusPoliticaFonte.BLOQUEADA,
        dominio="www.empregos.com.br",
        fonte=Fonte.EMPREGOS,
    )

    requisicao = criar_requisicao_inicial(
        alvo=alvo,
        callback=callback_teste,
    )

    assert requisicao is None


def test_rejeita_alvo_de_tipo_incorreto() -> None:
    """A fábrica deve falhar claramente ao receber outro objeto."""

    with pytest.raises(
        TypeError,
        match="AlvoColeta",
    ):
        criar_requisicao_inicial(
            alvo="empresa",  # type: ignore[arg-type]
            callback=callback_teste,
        )


def test_rejeita_callback_que_nao_pode_ser_chamado() -> None:
    """O Scrapy precisa receber uma função para tratar a resposta."""

    alvo = criar_alvo(
        status=StatusPoliticaFonte.APROVADA,
    )

    with pytest.raises(
        TypeError,
        match="callback",
    ):
        criar_requisicao_inicial(
            alvo=alvo,
            callback=None,  # type: ignore[arg-type]
        )
