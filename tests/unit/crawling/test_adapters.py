"""Testes da conversão de respostas do Scrapy."""

from datetime import UTC, datetime

from scrapy.http import HtmlResponse, Request, Response

from observatorio_vagas.crawling.adapters import converter_resposta_scrapy
from observatorio_vagas.domain.enums import Fonte

# Usamos uma data fixa para o teste produzir sempre o mesmo resultado.
DATA_COLETA = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)

# O b antes das aspas informa que o conteúdo está em bytes.
CORPO_HTML = b"<html><body><h1>Pessoa Desenvolvedora Python</h1></body></html>"


def test_conversao_preserva_dados_da_resposta_html() -> None:
    """A conversão deve preservar URLs, corpo e metadados HTTP."""

    # Esta é a URL originalmente solicitada pelo crawler.
    requisicao = Request(
        url="https://empresa.example/vagas/123",
    )

    # Esta é a resposta devolvida depois de um possível redirecionamento.
    resposta_scrapy = HtmlResponse(
        url="https://empresa.example/carreiras/vaga-123",
        request=requisicao,
        status=200,
        body=CORPO_HTML,
        encoding="utf-8",
        headers={
            b"Content-Type": b"text/html; charset=utf-8",
            b"Cache-Control": [
                b"no-cache",
                b"private",
            ],
        },
    )

    # Convertemos o objeto interno do Scrapy para o formato
    # padronizado utilizado pelo nosso projeto.
    resposta_bruta = converter_resposta_scrapy(
        resposta=resposta_scrapy,
        fonte=Fonte.PAGINA_CARREIRAS,
        coletado_em=DATA_COLETA,
    )

    # Cada assert confirma que uma informação importante foi preservada.
    assert resposta_bruta.fonte is Fonte.PAGINA_CARREIRAS
    assert resposta_bruta.url_solicitada == "https://empresa.example/vagas/123"
    assert resposta_bruta.url_final == "https://empresa.example/carreiras/vaga-123"
    assert resposta_bruta.status_http == 200
    assert resposta_bruta.corpo == CORPO_HTML
    assert resposta_bruta.tipo_conteudo == "text/html; charset=utf-8"
    assert resposta_bruta.codificacao == "utf-8"
    assert resposta_bruta.coletado_em == DATA_COLETA

    # Também verificamos se cabeçalhos repetidos foram preservados.
    assert ("Cache-Control", "no-cache") in resposta_bruta.cabecalhos
    assert ("Cache-Control", "private") in resposta_bruta.cabecalhos


def test_conversao_sem_requisicao_usa_url_da_resposta() -> None:
    """Uma resposta sem Request deve continuar podendo ser armazenada."""

    # Response representa aqui um conteúdo binário e não possui encoding.
    resposta_scrapy = Response(
        url="https://empresa.example/arquivo.pdf",
        status=404,
        body=b"arquivo nao encontrado",
        headers={
            b"Content-Type": b"application/pdf",
        },
    )

    resposta_bruta = converter_resposta_scrapy(
        resposta=resposta_scrapy,
        fonte=Fonte.OUTRA,
        coletado_em=DATA_COLETA,
    )

    # Sem uma requisição associada, usamos a própria URL da resposta.
    assert resposta_bruta.url_solicitada == "https://empresa.example/arquivo.pdf"
    assert resposta_bruta.url_final == "https://empresa.example/arquivo.pdf"

    # Confirmamos que respostas de erro também são armazenadas.
    assert resposta_bruta.status_http == 404
    assert resposta_bruta.tipo_conteudo == "application/pdf"
    assert resposta_bruta.codificacao is None
    assert resposta_bruta.sucesso is False
