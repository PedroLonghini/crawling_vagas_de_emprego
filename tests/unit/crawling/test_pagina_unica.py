"""Testes do spider que coleta uma única página."""

import asyncio

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.spiders.pagina_unica import PaginaUnicaSpider
from observatorio_vagas.domain.enums import Fonte

# Corpo fictício usado sem acessar a internet.
CORPO_HTML = b"<html><body><h1>Vaga Python</h1></body></html>"


async def coletar_requisicoes_iniciais(
    spider: PaginaUnicaSpider,
) -> list[Request]:
    """Transforma o gerador assíncrono do spider em uma lista."""

    return [requisicao async for requisicao in spider.start()]


def test_spider_exige_url_inicial() -> None:
    """A execução deve falhar claramente quando a URL não for informada."""

    with pytest.raises(
        ValueError,
        match="informe url_inicial",
    ):
        PaginaUnicaSpider()


@pytest.mark.parametrize(
    "url_invalida",
    [
        "",
        "empresa.example/vaga",
        "ftp://empresa.example/vaga",
    ],
)
def test_spider_rejeita_url_invalida(
    url_invalida: str,
) -> None:
    """Somente URLs HTTP e HTTPS completas devem ser aceitas."""

    with pytest.raises(
        ValueError,
        match="URL HTTP ou HTTPS completa",
    ):
        PaginaUnicaSpider(
            url_inicial=url_invalida,
        )


def test_spider_rejeita_fonte_desconhecida() -> None:
    """Uma fonte inexistente não deve entrar silenciosamente na coleta."""

    with pytest.raises(
        ValueError,
        match="fonte inválida",
    ):
        PaginaUnicaSpider(
            url_inicial="https://empresa.example/vaga",
            fonte="fonte_inexistente",
        )


def test_spider_normaliza_argumentos() -> None:
    """Espaços da URL devem ser removidos e a fonte deve virar enum."""

    spider = PaginaUnicaSpider(
        url_inicial="  https://empresa.example/vaga/123  ",
        fonte="gupy",
    )

    assert spider.url_inicial == "https://empresa.example/vaga/123"
    assert spider.fonte is Fonte.GUPY
    assert spider.allowed_domains == ["empresa.example"]


def test_start_produz_uma_unica_requisicao() -> None:
    """O spider piloto não deve criar várias requisições iniciais."""

    spider = PaginaUnicaSpider(
        url_inicial="https://empresa.example/vaga/123",
    )

    # asyncio.run executa o método assíncrono start do Scrapy.
    requisicoes = asyncio.run(coletar_requisicoes_iniciais(spider))

    assert len(requisicoes) == 1
    assert requisicoes[0].url == "https://empresa.example/vaga/123"
    assert requisicoes[0].dont_filter is True
    assert requisicoes[0].callback == spider.parse


def test_parse_produz_resposta_bruta() -> None:
    """A resposta do Scrapy deve ser convertida para o contrato interno."""

    spider = PaginaUnicaSpider(
        url_inicial="https://empresa.example/vaga/123",
        fonte="pagina_carreiras",
    )

    # Criamos uma requisição falsa.
    requisicao = Request(
        url=spider.url_inicial,
    )

    # Criamos uma resposta falsa.
    #
    # Portanto, este teste não acessa a internet.
    resposta_scrapy = HtmlResponse(
        url=spider.url_inicial,
        request=requisicao,
        status=200,
        body=CORPO_HTML,
        encoding="utf-8",
        headers={
            b"Content-Type": b"text/html; charset=utf-8",
        },
    )

    # Executamos manualmente o método parse.
    itens = list(spider.parse(resposta_scrapy))

    assert len(itens) == 1

    resposta_bruta = itens[0]

    # Confirma que o spider produziu o contrato correto.
    assert isinstance(resposta_bruta, RespostaBruta)

    # Confirma que nenhuma informação importante foi perdida.
    assert resposta_bruta.fonte is Fonte.PAGINA_CARREIRAS
    assert resposta_bruta.url_final == spider.url_inicial
    assert resposta_bruta.status_http == 200
    assert resposta_bruta.corpo == CORPO_HTML
    assert resposta_bruta.tipo_conteudo == "text/html; charset=utf-8"
    assert resposta_bruta.codificacao == "utf-8"
