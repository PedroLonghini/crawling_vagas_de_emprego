"""O armazenamento oficial não pode virar uma liberação genérica de CDN."""

from unittest.mock import MagicMock

import pytest
from scrapy import Request
from scrapy.exceptions import IgnoreRequest
from scrapy.http import TextResponse

from observatorio_vagas.crawling.adapters import converter_resposta_scrapy
from observatorio_vagas.crawling.middlewares import BarreiraPoliticaDownloaderMiddleware
from observatorio_vagas.domain.enums import Fonte

RECURSO = "638f5cdd-b8ab-495d-990b-dbacce701ce2"
ORIGEM = (
    "https://dados.es.gov.br/dataset/c43d6653-634a-4e6e-bd66-c9ca3f6ee106"
    f"/resource/{RECURSO}/download/vagas_anchieta.csv"
)
DESTINO = f"https://one.s3.es.gov.br/pr-ckan-store/resources/{RECURSO}/vagas_anchieta.csv"


def criar_request(destino=DESTINO, origem=ORIGEM, **meta):
    return Request(
        destino,
        cb_kwargs={"fonte": "ckan"},
        meta={
            "observatorio_requisicao_autorizada": True,
            "observatorio_status_politica": "aprovada",
            "observatorio_dominio": "dados.es.gov.br",
            "redirect_urls": [origem],
            **meta,
        },
    )


def test_permite_apenas_csv_que_o_proprio_portal_redirecionou() -> None:
    middleware = BarreiraPoliticaDownloaderMiddleware(MagicMock())
    assert middleware.process_request(criar_request()) is None
    assert middleware.process_request(criar_request(DESTINO + "?X-Amz-Expires=3600")) is None


@pytest.mark.parametrize(
    "destino,origem",
    [
        (DESTINO.replace("one.s3.es.gov.br", "externo.example"), ORIGEM),
        (DESTINO.replace("one.s3.es.gov.br", "one.s3.es.gov.br.evil.example"), ORIGEM),
        (DESTINO.replace("vagas_anchieta", "outro_arquivo"), ORIGEM),
        (DESTINO.replace(RECURSO, "outro-recurso"), ORIGEM),
        (DESTINO.replace("https:", "http:"), ORIGEM),
        (DESTINO, ORIGEM.replace("dados.es.gov.br", "externo.example")),
        (DESTINO, ORIGEM.replace("c43d6653-634a-4e6e-bd66-c9ca3f6ee106", "outro-dataset")),
    ],
)
def test_rejeita_outro_host_recurso_e_dataset(destino, origem) -> None:
    middleware = BarreiraPoliticaDownloaderMiddleware(MagicMock())
    with pytest.raises(IgnoreRequest, match="dominio_divergente"):
        middleware.process_request(criar_request(destino, origem))


@pytest.mark.parametrize(
    "meta",
    [
        {"redirect_urls": []},
        {"redirect_urls": ORIGEM},
        {"observatorio_requisicao_autorizada": False},
        {"observatorio_status_politica": "bloqueada"},
    ],
)
def test_nao_dispensa_autorizacao_ou_prova_do_redirect(meta) -> None:
    middleware = BarreiraPoliticaDownloaderMiddleware(MagicMock())
    with pytest.raises(IgnoreRequest):
        middleware.process_request(criar_request(**meta))


def test_bruto_preserva_url_publica_original() -> None:
    request = criar_request()
    response = TextResponse(url=DESTINO, request=request, body=b"vaga,codigo\n", encoding="utf-8")
    bruto = converter_resposta_scrapy(resposta=response, fonte=Fonte.CKAN)
    assert bruto.url_solicitada == ORIGEM
    assert bruto.url_final == DESTINO
