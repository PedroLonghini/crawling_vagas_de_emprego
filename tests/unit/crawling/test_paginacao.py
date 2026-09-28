"""Paginação HTML explícita e conservadora."""

from scrapy.http import HtmlResponse, TextResponse

from observatorio_vagas.crawling.paginacao import (
    descobrir_paginacao,
    eh_link_listagem,
)


def test_links_numericos_somente_em_controle_de_paginacao() -> None:
    resposta = HtmlResponse(
        url="https://empresa.example/vagas",
        body=(
            '<a href="/noticia/2026">2026</a>'
            '<div class="pagination"><a href="?page=2">2</a></div>'
            '<a aria-label="Próxima página" href="?page=3">&gt;</a>'
            '<link rel="next" href="?page=3">'
        ).encode(),
        encoding="utf-8",
    )
    assert descobrir_paginacao(resposta) == (
        "https://empresa.example/vagas?page=2",
        "https://empresa.example/vagas?page=3",
    )


def test_json_nao_usa_paginacao_html() -> None:
    resposta = TextResponse(
        url="https://empresa.example/api",
        body=b'{"html": "<a href="?page=2" rel=next>Next</a>"}',
        headers={"Content-Type": "application/json"},
        encoding="utf-8",
    )
    assert descobrir_paginacao(resposta) == ()


def test_paginacao_em_dominio_privacy_e_carregar_mais() -> None:
    resposta = HtmlResponse(
        url="https://dataprivacybr.org/vagas/",
        encoding="utf-8",
        body=(
            b'<a href="page/2">Carregar mais vagas</a>'
            b'<button data-next-url="?page=3" aria-disabled="true">Next</button>'
            b'<a href="/login" rel="next">Next</a>'
        ),
    )
    assert descobrir_paginacao(resposta) == ("https://dataprivacybr.org/vagas/page/2",)
    assert eh_link_listagem("https://dataprivacybr.org/vagas/page/2")


def test_reconhece_url_explicita_em_botao_de_carregar_mais() -> None:
    """O controle moderno precisa fornecer uma URL, nunca apenas um número."""

    resposta = HtmlResponse(
        url="https://empresa.example/vagas",
        body=(
            '<button data-next-url="?page=2">Carregar mais</button>'
            '<button data-page="3">Ignorar número solto</button>'
            '<button data-next="/login">Ignorar página administrativa</button>'
        ).encode(),
        encoding="utf-8",
        headers={"Content-Type": "text/html"},
    )

    assert descobrir_paginacao(resposta) == ("https://empresa.example/vagas?page=2",)


def test_descobre_url_literal_em_onclick() -> None:
    resposta = HtmlResponse(
        url="https://empresa.example/vagas",
        body=(
            b'<button onclick="loadMore(\'/vagas?page=2\')">'
            b"Carregar mais vagas</button>"
        ),
        encoding="utf-8",
    )

    assert descobrir_paginacao(resposta) == ("https://empresa.example/vagas?page=2",)


def test_onclick_nao_inventa_url_com_numero_de_pagina() -> None:
    resposta = HtmlResponse(
        url="https://empresa.example/vagas",
        body=b'<button onclick="loadMore(2)">Carregar mais vagas</button>',
        encoding="utf-8",
    )

    assert descobrir_paginacao(resposta) == ()


def test_reconhece_variacoes_explicitas_de_paginacao_modernas() -> None:
    resposta = HtmlResponse(
        url="https://empresa.example/vagas",
        encoding="utf-8",
        body=(
            '<nav class="pagination"><a href="?page=2" aria-label="Página 2">2</a></nav>'
            '<button data-next-page-url="?page=3">Próxima</button>'
            '<button data-load-more-url="?page=4">Carregar mais vagas</button>'
        ).encode(),
    )

    assert descobrir_paginacao(resposta) == (
        "https://empresa.example/vagas?page=2",
        "https://empresa.example/vagas?page=3",
        "https://empresa.example/vagas?page=4",
    )


def test_listagem_reconhece_busca_e_paginacao_por_parametro() -> None:
    """Listagens com cursor seguem descobrindo páginas posteriores."""

    assert eh_link_listagem("https://empresa.example/vagas?page=2")
    assert eh_link_listagem("https://empresa.example/jobs/search?cursor=proximo")
    assert not eh_link_listagem("https://empresa.example/jobs/123?tab=descricao")
    assert not eh_link_listagem("https://empresa.example/concursos")
