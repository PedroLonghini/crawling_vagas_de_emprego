"""Teste de contrato do registro de plataformas (plataformas.toml).

Toda regra cadastrada precisa funcionar de ponta a ponta: a URL de exemplo
passa pela barreira do downloader e pela fábrica de requisições. Foi a
ausência deste teste que deixou a API da Sólides fora da barreira e zerou
fontes em coleta real.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest
from scrapy import Request
from scrapy.exceptions import IgnoreRequest

from observatorio_vagas.crawling.adaptadores import empresa_direta
from observatorio_vagas.crawling.middlewares import BarreiraPoliticaDownloaderMiddleware
from observatorio_vagas.crawling.plataformas import (
    ErroRegistroPlataformas,
    Plataforma,
    _montar_plataforma,
    aceita_json,
    carregar_plataformas,
    filtrar_urls_do_locatario,
    hosts_companheiros_de,
    locatario_do_alvo,
    permite_companheiro,
    permite_redirecionamento,
    politica_sitemap,
)
from observatorio_vagas.crawling.request_factory import (
    _cabecalhos_especificos,
    _eh_api_publica_companheira,
)
from observatorio_vagas.crawling.spiders.catalogo_fontes import EVIDENCIAS_NAVEGACAO

PLATAFORMAS = carregar_plataformas()
COMPANHEIROS = [
    pytest.param(plataforma, regra, id=f"{plataforma.nome}:{regra.host}")
    for plataforma in PLATAFORMAS
    for regra in plataforma.companheiros
]
REDIRECIONAMENTOS = [
    pytest.param(plataforma, regra, id=f"{plataforma.nome}:{regra.host}")
    for plataforma in PLATAFORMAS
    for regra in plataforma.redirecionamentos
]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold()


def _barreira() -> BarreiraPoliticaDownloaderMiddleware:
    return BarreiraPoliticaDownloaderMiddleware(stats=MagicMock())


def _meta(dominio: str, **extra: object) -> dict[str, object]:
    return {
        "observatorio_requisicao_autorizada": True,
        "observatorio_status_politica": "aprovada",
        "observatorio_dominio": dominio,
        **extra,
    }


@pytest.mark.parametrize("plataforma", PLATAFORMAS, ids=lambda p: p.nome)
def test_exemplo_de_portal_pertence_a_plataforma(plataforma: Plataforma) -> None:
    assert plataforma.casa_portal(_host(plataforma.exemplo_portal))


@pytest.mark.parametrize(("plataforma", "regra"), COMPANHEIROS)
def test_companheiro_do_registro_passa_pela_barreira_e_pela_fabrica(
    plataforma: Plataforma, regra: object
) -> None:
    dominio = _host(plataforma.exemplo_portal)
    requisicao = Request(
        url=regra.exemplo,
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta=_meta(dominio),
    )

    assert _barreira().process_request(requisicao) is None
    assert _eh_api_publica_companheira(dominio_origem=dominio, dominio_destino=regra.host)


@pytest.mark.parametrize(("plataforma", "regra"), REDIRECIONAMENTOS)
def test_redirecionamento_do_registro_passa_pela_barreira(
    plataforma: Plataforma, regra: object
) -> None:
    dominio = _host(regra.exemplo_origem)
    requisicao = Request(
        url=regra.exemplo,
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta=_meta(dominio, redirect_urls=[regra.exemplo_origem]),
    )

    assert _barreira().process_request(requisicao) is None


@pytest.mark.parametrize("plataforma", PLATAFORMAS, ids=lambda p: p.nome)
def test_hosts_json_recebem_cabecalho_accept(plataforma: Plataforma) -> None:
    for host in plataforma.hosts_json:
        assert aceita_json(host)
        assert "json" in _cabecalhos_especificos(f"https://{host}/x")["Accept"]


def test_api_da_solides_passa_pela_barreira() -> None:
    """Regressão: 4 fontes Sólides voltaram com 0 vagas por IgnoreRequest."""

    requisicao = Request(
        url="https://apigw.solides.com.br/jobs/v3/home/vacancy?take=12&slug=ccda&page=1",
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta=_meta("ccda.vagas.solides.com.br"),
    )

    assert _barreira().process_request(requisicao) is None


def test_abler_aceita_locatario_com_mais_codificado() -> None:
    """Regressão: ``liga+`` chega como ``liga%2b`` e era barrado pelo regex."""

    url = "https://hulk-smash.abler.com.br/api/company/v1/careers_pages/liga%2b/vacancies"

    assert permite_companheiro(dominio_origem="ats.abler.com.br", url=url)


@pytest.mark.parametrize(
    ("dominio", "url"),
    [
        # Outro caminho no mesmo host de API.
        ("ats.abler.com.br", "https://hulk-smash.abler.com.br/api/admin/users"),
        # HTTP puro.
        ("ats.abler.com.br", "http://hulk-smash.abler.com.br/api/company/v1/careers_pages/x/vacancies"),
        # Origem que não é da plataforma.
        ("empresa.example", "https://apigw.solides.com.br/jobs/v3/home/vacancy"),
        # Bradesco exige o parâmetro c=bradesco.
        ("banco.bradesco", "https://bradesco.csod.com/ux/ats/careersite/1/home?c=outra"),
        # Sicoob não pode usar a API do Abler.
        ("www.sicoob.com.br", "https://hulk-smash.abler.com.br/api/company/v1/careers_pages/x/vacancies"),
    ],
)
def test_companheiro_fora_da_regra_e_recusado(dominio: str, url: str) -> None:
    assert not permite_companheiro(dominio_origem=dominio, url=url)


def test_redirecionamento_para_outro_caminho_e_recusado() -> None:
    assert not permite_redirecionamento(
        dominio_origem="jobs.smartrecruiters.com",
        url_origem="https://jobs.smartrecruiters.com/BoschGroup",
        url_destino="https://careers.smartrecruiters.com/OutraEmpresa",
    )


def test_smartrecruiters_e_abler_nao_baixam_sitemap_da_plataforma() -> None:
    """Regressão: api.smartrecruiters.com/sitemap.xml era bloqueado a cada coleta."""

    for host in ("api.smartrecruiters.com", "jobs.smartrecruiters.com", "ats.abler.com.br"):
        assert politica_sitemap(host) == "nenhum"
    assert politica_sitemap("empresa.example") == "padrao"


def test_quickin_segue_apenas_o_sitemap_da_propria_empresa() -> None:
    """Regressão: 46% das requisições iam para sitemaps de ~970 outras empresas."""

    indice = [
        "https://jobs.quickin.io/sitemaps/peoplecapitalhumano-jobs.xml",
        "https://jobs.quickin.io/sitemaps/peopleconsulting-jobs.xml",
        "https://jobs.quickin.io/sitemaps/reply-jobs.xml",
        "https://jobs.quickin.io/peoplecapitalhumano/jobs/123",
        "https://jobs.quickin.io/outraempresa/jobs/9",
    ]

    assert politica_sitemap("jobs.quickin.io") == "por_locatario"
    assert locatario_do_alvo("https://jobs.quickin.io/PeopleCapitalHumano/jobs") == (
        "peoplecapitalhumano"
    )
    assert filtrar_urls_do_locatario(
        indice, host="jobs.quickin.io", locatario="peoplecapitalhumano"
    ) == [
        "https://jobs.quickin.io/sitemaps/peoplecapitalhumano-jobs.xml",
        "https://jobs.quickin.io/peoplecapitalhumano/jobs/123",
    ]


def test_hosts_companheiros_dos_alvos_incluem_portais_externos() -> None:
    hosts = hosts_companheiros_de({"banco.bradesco", "www.sicoob.com.br", "x.vagas.solides.com.br"})

    assert {"bradesco.csod.com", "sicoob.empregare.com", "apigw.solides.com.br"} <= set(hosts)


def test_evidencias_de_listagem_dos_adaptadores_sao_navegadas_pelo_spider() -> None:
    """Uma listagem sem evidência na lista viraria 'detalhe' e nunca descobriria vagas."""

    fonte = Path(empresa_direta.__file__).read_text(encoding="utf-8")
    listagens = set(re.findall(r'"((?:listagem|paginacao|portal)_[a-z0-9_]+)"', fonte))

    assert listagens
    assert listagens <= EVIDENCIAS_NAVEGACAO


@pytest.mark.parametrize(
    "bruto",
    [
        {"nome": "x", "portais": ["Host.Com"]},
        {"nome": "x", "portais": ["a.com"], "sitemap": "talvez"},
        {"nome": "x", "portais": ["a.com"], "sitemap": "por_locatario"},
        {"nome": "x", "portais_sufixo": ["semponto.com"]},
        {
            "nome": "x",
            "exemplo_portal": "https://a.com/",
            "portais": ["a.com"],
            "companheiro": [
                {"host": "api.a.com", "caminho": "/v1/x", "exemplo": "https://api.a.com/outro"}
            ],
        },
    ],
)
def test_registro_recusa_configuracao_inconsistente(bruto: dict[str, object]) -> None:
    with pytest.raises(ErroRegistroPlataformas):
        _montar_plataforma(bruto)


def test_barreira_ainda_bloqueia_host_desconhecido() -> None:
    requisicao = Request(
        url="https://intruso.example/api",
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta=_meta("ats.abler.com.br"),
    )

    with pytest.raises(IgnoreRequest, match="dominio_divergente"):
        _barreira().process_request(requisicao)
