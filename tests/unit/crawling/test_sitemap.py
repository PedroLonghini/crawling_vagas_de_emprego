"""Testes da descoberta limitada de vagas por sitemap público."""

from scrapy.http import TextResponse

from observatorio_vagas.crawling.sitemap import (
    criar_url_sitemap_padrao,
    descobrir_urls_sitemap,
    eh_resposta_sitemap,
)


def test_sitemap_retem_somente_detalhes_de_vaga_e_submapas_do_mesmo_dominio() -> None:
    """Sitemaps amplos não devem transformar páginas institucionais em vagas."""

    resposta = TextResponse(
        url="https://empresa.example/sitemap.xml",
        body=b"""
        <urlset>
          <url><loc>https://empresa.example/jobs/analista-de-dados</loc></url>
          <url><loc>https://empresa.example/sitemap-vagas.xml</loc></url>
          <url><loc>https://empresa.example/sobre</loc></url>
          <url><loc>https://externa.example/vagas/nao-permitida</loc></url>
        </urlset>
        """,
        encoding="utf-8",
        headers={b"Content-Type": b"application/xml"},
    )

    assert eh_resposta_sitemap(resposta)
    assert descobrir_urls_sitemap(resposta) == (
        "https://empresa.example/jobs/analista-de-dados",
        "https://empresa.example/sitemap-vagas.xml",
    )


def test_cria_sitemap_padrao_no_mesmo_dominio() -> None:
    """A página de carreiras não pode indicar sitemap em outro domínio."""

    resposta = TextResponse(
        url="https://empresa.example/carreiras/vagas?origem=menu",
        body=b"<html></html>",
        encoding="utf-8",
    )

    assert criar_url_sitemap_padrao(resposta) == "https://empresa.example/sitemap.xml"
