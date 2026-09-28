"""Testes da segunda barreira de política do crawler."""

from unittest.mock import MagicMock

import pytest
from scrapy import Request
from scrapy.exceptions import IgnoreRequest
from scrapy.http.request import NO_CALLBACK

from observatorio_vagas.crawling.middlewares import (
    BarreiraPoliticaDownloaderMiddleware,
)


def criar_middleware() -> tuple[
    BarreiraPoliticaDownloaderMiddleware,
    MagicMock,
]:
    """Cria o middleware com estatísticas falsas para o teste."""

    stats = MagicMock()

    middleware = BarreiraPoliticaDownloaderMiddleware(
        stats=stats,
    )

    return middleware, stats


def metadados_autorizados(
    *,
    dominio: str = "empresa.example",
    status: str = "aprovada",
) -> dict[str, object]:
    """Produz os metadados exigidos pela barreira."""

    return {
        "observatorio_requisicao_autorizada": True,
        "observatorio_status_politica": status,
        "observatorio_dominio": dominio,
    }


def test_permite_requisicao_criada_pela_fabrica() -> None:
    """Uma requisição autorizada deve continuar para o downloader."""

    middleware, stats = criar_middleware()

    request = Request(
        url="https://empresa.example/vagas",
        meta=metadados_autorizados(),
    )

    assert middleware.process_request(request) is None

    stats.inc_value.assert_called_once_with("observatorio/politica/requisicoes_autorizadas")


def test_bloqueia_requisicao_sem_marcador() -> None:
    """Um spider não pode ignorar silenciosamente a fábrica."""

    middleware, stats = criar_middleware()
    request = Request(url="https://empresa.example/vagas")

    with pytest.raises(
        IgnoreRequest,
        match="sem_autorizacao",
    ):
        middleware.process_request(request)

    stats.inc_value.assert_called_once_with("observatorio/politica/bloqueios/sem_autorizacao")


def test_bloqueia_status_nao_permitido() -> None:
    """Um marcador não transforma uma política bloqueada em válida."""

    middleware, stats = criar_middleware()

    request = Request(
        url="https://empresa.example/vagas",
        meta=metadados_autorizados(
            status="bloqueada",
        ),
    )

    with pytest.raises(
        IgnoreRequest,
        match="status_nao_permitido",
    ):
        middleware.process_request(request)

    stats.inc_value.assert_called_once_with("observatorio/politica/bloqueios/status_nao_permitido")


def test_bloqueia_redirecionamento_para_outro_dominio() -> None:
    """A autorização de um domínio não pode ser usada em outro."""

    middleware, stats = criar_middleware()

    request = Request(
        url="https://outro.example/vagas",
        meta=metadados_autorizados(
            dominio="empresa.example",
        ),
    )

    with pytest.raises(
        IgnoreRequest,
        match="dominio_divergente",
    ):
        middleware.process_request(request)

    stats.inc_value.assert_called_once_with("observatorio/politica/bloqueios/dominio_divergente")


def test_permite_apenas_api_publica_companheira_da_abler() -> None:
    """A exceção Abler não permite outros endpoints nem outros alvos."""

    middleware, stats = criar_middleware()
    request = Request(
        url=(
            "https://hulk-smash.abler.com.br/api/company/v1/"
            "careers_pages/goldenrh/vacancies?per_page=100&page=1"
        ),
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta=metadados_autorizados(dominio="ats.abler.com.br"),
    )

    assert middleware.process_request(request) is None
    assert stats.inc_value.call_args_list == [
        (("observatorio/politica/api_publica_companheira",), {}),
        (("observatorio/politica/requisicoes_autorizadas",), {}),
    ]


def test_permite_api_publica_companheira_da_smartrecruiters() -> None:
    middleware, stats = criar_middleware()
    request = Request(
        url="https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100&offset=0",
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta=metadados_autorizados(dominio="jobs.smartrecruiters.com"),
    )

    assert middleware.process_request(request) is None
    assert stats.inc_value.call_args_list == [
        (("observatorio/politica/api_publica_companheira",), {}),
        (("observatorio/politica/requisicoes_autorizadas",), {}),
    ]


def test_permite_redirecionamento_oficial_da_smartrecruiters() -> None:
    middleware, stats = criar_middleware()
    request = Request(
        url="https://careers.smartrecruiters.com/BoschGroup",
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta={
            **metadados_autorizados(dominio="jobs.smartrecruiters.com"),
            "redirect_urls": ["https://jobs.smartrecruiters.com/BoschGroup"],
        },
    )

    assert middleware.process_request(request) is None
    assert stats.inc_value.call_args_list == [
        (("observatorio/politica/recurso_oficial_redirecionado",), {}),
        (("observatorio/politica/requisicoes_autorizadas",), {}),
    ]


@pytest.mark.parametrize(
    ("url", "dominio", "codigo"),
    [
        (
            "https://www.empregos.com.br/vagas",
            "www.empregos.com.br",
            "dominio_empregos",
        ),
        (
            "https://br.indeed.com/jobs",
            "br.indeed.com",
            "dominio_indeed",
        ),
        (
            "https://www.infojobs.com.br/vagas",
            "www.infojobs.com.br",
            "dominio_infojobs",
        ),
        (
            "https://www.catho.com.br/vagas",
            "www.catho.com.br",
            "dominio_catho",
        ),
    ],
)
def test_bloqueia_dominio_restrito_mesmo_com_metadados_forjados(
    url: str,
    dominio: str,
    codigo: str,
) -> None:
    """A lista central prevalece sobre metadados forjados."""

    middleware, stats = criar_middleware()

    request = Request(
        url=url,
        meta=metadados_autorizados(
            dominio=dominio,
        ),
    )

    with pytest.raises(
        IgnoreRequest,
        match=codigo,
    ):
        middleware.process_request(request)

    stats.inc_value.assert_called_once_with(f"observatorio/politica/bloqueios/{codigo}")


def test_permite_requisicao_interna_de_robots_txt() -> None:
    """A barreira não deve impedir o respeito ao robots.txt."""

    middleware, stats = criar_middleware()

    request = Request(
        url="https://empresa.example/robots.txt",
        meta={
            "dont_obey_robotstxt": True,
        },
        callback=NO_CALLBACK,
    )

    assert middleware.process_request(request) is None

    stats.inc_value.assert_called_once_with("observatorio/politica/robots_interno")


def test_bloqueia_esquema_que_nao_e_http() -> None:
    """Arquivos locais não podem entrar no crawler externo."""

    middleware, stats = criar_middleware()
    request = Request(url="file:///tmp/vagas.html")

    with pytest.raises(
        IgnoreRequest,
        match="esquema_invalido",
    ):
        middleware.process_request(request)

    stats.inc_value.assert_called_once_with("observatorio/politica/bloqueios/esquema_invalido")
