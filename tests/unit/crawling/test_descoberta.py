"""Testes da descoberta conservadora de links de vagas."""

import pytest
from scrapy.http import HtmlResponse, Response, TextResponse

from observatorio_vagas.crawling.descoberta import (
    descobrir_links_candidatos,
)

HTML_CARREIRAS = b"""
<html>
  <body>
    <a href="/vagas/123">
      <span>Desenvolvedor Python</span>
    </a>

    <a href="/vagas/123#descricao">
      Link repetido
    </a>

    <a href="/oportunidades/456">
      Analista de Dados
    </a>

    <a href="/positions/789">
      Software Engineer
    </a>

    <a href="/sobre">
      Conheca nossas carreiras
    </a>

    <a href="https://externa.example/jobs/1">
      Vaga em plataforma externa
    </a>

    <a href="/privacidade">
      Privacidade das vagas
    </a>

    <a href="/documentos/vagas.pdf">
      Vagas em PDF
    </a>

    <a href="mailto:rh@empresa.example">
      Envie seu curriculo para vagas
    </a>

    <a href="/carreiras#topo">
      Voltar para carreiras
    </a>

    <a href="/noticias">
      Noticias da empresa
    </a>
  </body>
</html>
"""


def criar_resposta_html() -> HtmlResponse:
    """Cria uma página HTML sem acessar a internet."""

    return HtmlResponse(
        url="https://empresa.example/carreiras",
        status=200,
        body=HTML_CARREIRAS,
        encoding="utf-8",
        headers={
            b"Content-Type": b"text/html; charset=utf-8",
        },
    )


def test_descobre_links_por_url_e_texto() -> None:
    """Sinais na URL ou no texto transformam o link em candidato."""

    candidatos = descobrir_links_candidatos(criar_resposta_html())

    assert [candidato.url for candidato in candidatos] == [
        "https://empresa.example/vagas/123",
        "https://empresa.example/oportunidades/456",
        "https://empresa.example/positions/789",
        "https://empresa.example/sobre",
        "https://externa.example/jobs/1",
    ]


def test_registra_evidencias_da_descoberta() -> None:
    """O resultado deve explicar por que o link foi selecionado."""

    candidatos = descobrir_links_candidatos(criar_resposta_html())

    primeiro = candidatos[0]
    institucional = candidatos[3]

    assert primeiro.evidencias == ("palavra_na_url",)

    assert institucional.evidencias == ("palavra_no_texto",)


def test_remove_fragmentos_e_links_repetidos() -> None:
    """A mesma vaga com fragmentos diferentes aparece uma vez."""

    candidatos = descobrir_links_candidatos(criar_resposta_html())

    urls = [candidato.url for candidato in candidatos]

    assert urls.count("https://empresa.example/vagas/123") == 1


def test_ignora_arquivos_esquemas_e_paginas_excluidas() -> None:
    """Arquivos e páginas institucionais restritas são descartados."""

    candidatos = descobrir_links_candidatos(criar_resposta_html())

    urls = {candidato.url for candidato in candidatos}

    assert "https://empresa.example/privacidade" not in urls

    assert "https://empresa.example/documentos/vagas.pdf" not in urls

    assert "mailto:rh@empresa.example" not in urls

    assert "https://empresa.example/carreiras" not in urls


def test_link_externo_ainda_e_apenas_candidato() -> None:
    """A descoberta não concede autorização a outro domínio."""

    candidatos = descobrir_links_candidatos(criar_resposta_html())

    assert candidatos[-1].url == ("https://externa.example/jobs/1")

    # No próximo componente, a fábrica segura verificará
    # o domínio e impedirá o download deste candidato.


def test_resposta_de_texto_nao_html_retorna_vazio() -> None:
    """JSON e outros textos não devem usar seletores de HTML."""

    resposta = TextResponse(
        url="https://empresa.example/api",
        status=200,
        body=b'{"jobs": []}',
        encoding="utf-8",
        headers={
            b"Content-Type": b"application/json",
        },
    )

    assert descobrir_links_candidatos(resposta) == ()


def test_resposta_binaria_retorna_vazio() -> None:
    """Uma resposta binária não possui links HTML."""

    resposta = Response(
        url="https://empresa.example/arquivo",
        status=200,
        body=b"conteudo binario",
    )

    assert descobrir_links_candidatos(resposta) == ()


def test_nome_do_dominio_nao_transforma_qualquer_link_em_vaga() -> None:
    resposta = TextResponse(
        url="https://jobs.example/",
        encoding="utf-8",
        body=b'<a href="/sobre">Sobre</a><a href="/vaga/123">Python</a>',
    )
    assert [x.url for x in descobrir_links_candidatos(resposta)] == [
        "https://jobs.example/vaga/123"
    ]


def test_compartilhamentos_e_formularios_nao_consumem_limite_de_vagas() -> None:
    resposta = TextResponse(
        url="https://empresa.example/carreiras",
        encoding="utf-8",
        body=(
            b'<a href="/formulario-curriculo?vagaTitulo=Python">Vaga</a>'
            b'<a href="https://wa.me/?text=/vaga/123">Vaga</a>'
            b'<a href="https://www.facebook.com/sharer.php?u=/vaga/123">Vaga</a>'
            b'<a href="/vaga/123">Python</a>'
        ),
    )
    assert [x.url for x in descobrir_links_candidatos(resposta)] == [
        "https://empresa.example/vaga/123"
    ]


def test_cargo_privacidade_nao_e_confundido_com_pagina_administrativa():
    resposta = TextResponse(
        url="https://empresa.example/carreiras",
        encoding="utf-8",
        body=(
            b'<a href="/vaga/analista-privacidade">Analista de privacidade</a>'
            b'<a href="/jobs/12">Contato com clientes</a>'
            b'<a href="/jobs/login">Vagas</a>'
            b'<a href="/politica-de-privacidade">Vagas e privacidade</a>'
        ),
    )
    assert [c.url for c in descobrir_links_candidatos(resposta)] == [
        "https://empresa.example/vaga/analista-privacidade",
        "https://empresa.example/jobs/12",
    ]


def test_rejeita_objeto_que_nao_e_resposta() -> None:
    """Erros de integração devem produzir mensagem clara."""

    with pytest.raises(
        TypeError,
        match="Response do Scrapy",
    ):
        descobrir_links_candidatos(
            "pagina",  # type: ignore[arg-type]
        )
