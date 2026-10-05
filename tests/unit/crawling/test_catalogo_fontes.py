"""Testes do spider baseado no catálogo de fontes."""

import asyncio
from pathlib import Path

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, TextResponse

from observatorio_vagas.crawling.catalog import (
    ErroCatalogoFontes,
    carregar_alvos_csv,
    carregar_alvos_csv_tolerante,
)
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.spiders.catalogo_fontes import CatalogoFontesSpider
from observatorio_vagas.domain.enums import Fonte

# Cabeçalho que será utilizado nos catálogos temporários dos testes.
CABECALHO = "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica"

# Página HTML falsa.
#
# Os testes não acessam a internet.
CORPO_HTML = b"<html><body><h1>Carreiras</h1></body></html>"


def test_catalogo_simples_aceita_apenas_urls_e_cria_alvos_coletaveis(
    tmp_path: Path,
) -> None:
    """O arquivo de uso diário precisa exigir somente a coluna url."""

    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "url\nhttps://jobs.lever.co/flashapp\nhttps://hcor.portaldetalentos.senior.com.br/jobs\n",
        encoding="utf-8",
    )

    alvos = carregar_alvos_csv(catalogo)

    assert len(alvos) == 2
    assert all(alvo.fonte is Fonte.PAGINA_CARREIRAS for alvo in alvos)
    assert all(alvo.habilitado_para_coleta for alvo in alvos)
    assert all(not alvo.habilitado_para_publicacao for alvo in alvos)
    assert [alvo.url_inicial for alvo in alvos] == [
        "https://jobs.lever.co/flashapp",
        "https://hcor.portaldetalentos.senior.com.br/jobs",
    ]
    assert len({alvo.alvo_id for alvo in alvos}) == 2


def test_catalogo_simples_promove_apenas_url_com_autorizacao_registrada(
    tmp_path: Path,
) -> None:
    """A promoção exige a mesma URL na lista simples de autorizações."""

    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "url\nhttps://empresa-a.example/carreiras\nhttps://empresa-b.example/carreiras\n",
        encoding="utf-8",
    )
    (tmp_path / "fontes_autorizadas.csv").write_text(
        "url\nhttps://empresa-b.example/carreiras\n",
        encoding="utf-8",
    )

    alvos = carregar_alvos_csv(catalogo)

    assert alvos[0].habilitado_para_publicacao is False
    assert alvos[1].habilitado_para_publicacao is True
    assert alvos[1].politica.autorizacao_escrita is True


def test_catalogo_simples_isola_url_de_dominio_bloqueado(
    tmp_path: Path,
) -> None:
    """Uma URL bloqueada não pode impedir a leitura das demais fontes."""

    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text(
        "url\nhttps://empresa.example/carreiras\nhttps://www.catho.com.br/vagas\n",
        encoding="utf-8",
    )

    resultado = carregar_alvos_csv_tolerante(catalogo)

    assert [alvo.url_inicial for alvo in resultado.alvos] == ["https://empresa.example/carreiras"]
    assert len(resultado.falhas) == 1
    assert resultado.falhas[0].numero_linha == 3
    assert "Catho" in resultado.falhas[0].mensagem


def test_catalogo_simples_recusa_gupy(
    tmp_path: Path,
) -> None:
    """O formato simples não aceita Gupy como página própria de carreira."""

    catalogo = tmp_path / "fontes.csv"
    catalogo.write_text("url\nhttps://empresa.gupy.io/jobs\n", encoding="utf-8")

    with pytest.raises(ErroCatalogoFontes, match="Gupy"):
        carregar_alvos_csv(catalogo)


def escrever_catalogo(
    tmp_path: Path,
    *linhas: str,
) -> Path:
    """Cria um catálogo temporário para o spider."""

    caminho = tmp_path / "fontes_spider.csv"

    caminho.write_text(
        "\n".join(
            [
                CABECALHO,
                *linhas,
                "",
            ]
        ),
        encoding="utf-8",
    )

    return caminho


async def coletar_requisicoes(
    spider: CatalogoFontesSpider,
) -> list[Request]:
    """Consome todas as requisições iniciais do spider."""

    return [request async for request in spider.start()]


def test_start_cria_requisicoes_somente_para_fontes_autorizadas(
    tmp_path: Path,
) -> None:
    """Fontes pendentes, inativas e bloqueadas devem ser ignoradas."""

    catalogo = escrever_catalogo(
        tmp_path,
        ("aprovada,Empresa A,outra,https://aprovada.example/carreiras,true,1,aprovada"),
        ("interna,Empresa B,outra,https://interna.example/carreiras,true,1,somente_coleta"),
        ("pendente,Empresa C,outra,https://pendente.example/carreiras,true,1,pendente"),
        ("inativa,Empresa D,outra,https://inativa.example/carreiras,false,1,aprovada"),
        ("empregos,Empregos,empregos,https://www.empregos.com.br,true,1,bloqueada"),
    )

    spider = CatalogoFontesSpider(
        catalogo=str(catalogo),
    )

    requisicoes = asyncio.run(coletar_requisicoes(spider))

    # Somente as fontes "aprovada" e "somente_coleta"
    # podem ser acessadas.
    assert len(requisicoes) == 2

    assert {request.url for request in requisicoes} == {
        "https://aprovada.example/carreiras",
        "https://interna.example/carreiras",
    }

    # Apenas os dois domínios autorizados entram no spider.
    assert spider.allowed_domains == [
        "aprovada.example",
        "interna.example",
    ]

    # As requisições precisam carregar a marca de autorização.
    for request in requisicoes:
        assert request.meta["observatorio_requisicao_autorizada"] is True

        assert request.callback == spider.parse


def test_start_sem_fontes_autorizadas_nao_cria_requisicao(
    tmp_path: Path,
) -> None:
    """Um catálogo totalmente restrito não deve acessar a internet."""

    catalogo = escrever_catalogo(
        tmp_path,
        ("pendente,Empresa,outra,https://pendente.example,true,1,pendente"),
    )

    spider = CatalogoFontesSpider(
        catalogo=str(catalogo),
    )

    requisicoes = asyncio.run(coletar_requisicoes(spider))

    # Nenhuma requisição significa nenhum acesso à internet.
    assert requisicoes == []
    assert spider.allowed_domains == []


def test_start_isola_linha_invalida_e_continua(
    tmp_path: Path,
) -> None:
    """Uma fonte malformada não pode impedir outra fonte válida."""

    catalogo = escrever_catalogo(
        tmp_path,
        "quebrada,Empresa Ruim,outra,url-invalida,true,10,somente_coleta",
        ("valida,Empresa Válida,outra,https://valida.example/carreiras,true,10,somente_coleta"),
    )

    spider = CatalogoFontesSpider(
        catalogo=str(catalogo),
    )

    requisicoes = asyncio.run(coletar_requisicoes(spider))

    assert [requisicao.url for requisicao in requisicoes] == [
        "https://valida.example/carreiras",
    ]
    assert len(spider.falhas_catalogo) == 1
    assert spider.falhas_catalogo[0].alvo_id == "quebrada"


def test_start_preserva_contextos_que_usam_a_mesma_url(
    tmp_path: Path,
) -> None:
    """O filtro global do Scrapy não pode apagar um segundo alvo válido."""

    catalogo = escrever_catalogo(
        tmp_path,
        (
            "empresa_um,Empresa Um,outra,https://compartilhada.example/carreiras,true,5,somente_coleta"
        ),
        (
            "empresa_dois,Empresa Dois,outra,https://compartilhada.example/carreiras,true,5,somente_coleta"
        ),
    )

    spider = CatalogoFontesSpider(
        catalogo=str(catalogo),
    )

    requisicoes = asyncio.run(coletar_requisicoes(spider))

    assert len(requisicoes) == 2
    assert [requisicao.cb_kwargs["alvo_id"] for requisicao in requisicoes] == [
        "empresa_um",
        "empresa_dois",
    ]
    assert all(requisicao.dont_filter for requisicao in requisicoes)


def test_start_envia_validadores_http_da_coleta_anterior(tmp_path: Path) -> None:
    """A segunda coleta revalida a listagem sem transferir cache entre empresas."""

    catalogo = escrever_catalogo(
        tmp_path,
        "empresa,Empresa,outra,https://empresa.example/carreiras,true,1,aprovada",
    )
    estado = tmp_path / "incremental.json"
    primeira = CatalogoFontesSpider(catalogo=str(catalogo), estado_incremental=str(estado))
    requisicao = asyncio.run(coletar_requisicoes(primeira))[0]
    resposta = HtmlResponse(
        url=requisicao.url,
        request=requisicao,
        body=CORPO_HTML,
        status=200,
        headers={b"ETag": b'"v1"'},
        encoding="utf-8",
    )
    list(primeira.parse(resposta, **requisicao.cb_kwargs))

    segunda = CatalogoFontesSpider(catalogo=str(catalogo), estado_incremental=str(estado))
    revalidacao = asyncio.run(coletar_requisicoes(segunda))[0]

    assert revalidacao.headers.get("If-None-Match") == b'"v1"'


def test_parse_preserva_contexto_da_empresa(
    tmp_path: Path,
) -> None:
    """A resposta bruta deve continuar ligada ao alvo do catálogo."""

    catalogo = escrever_catalogo(
        tmp_path,
        ("empresa_123,Empresa Teste,outra,https://empresa.example/carreiras,true,1,aprovada"),
    )

    spider = CatalogoFontesSpider(
        catalogo=str(catalogo),
    )

    request = asyncio.run(coletar_requisicoes(spider))[0]

    # Cria uma resposta falsa como se ela tivesse vindo da internet.
    response = HtmlResponse(
        url=request.url,
        request=request,
        status=200,
        body=CORPO_HTML,
        encoding="utf-8",
        headers={
            b"Content-Type": b"text/html; charset=utf-8",
        },
    )

    itens = list(
        spider.parse(
            response,
            **request.cb_kwargs,
        )
    )

    assert len(itens) == 1

    resposta_bruta = itens[0]

    assert isinstance(
        resposta_bruta,
        RespostaBruta,
    )

    # Estes campos permitem identificar qual empresa
    # produziu aquele conteúdo.
    assert resposta_bruta.alvo_id == "empresa_123"
    assert resposta_bruta.empresa_nome == "Empresa Teste"

    assert resposta_bruta.fonte is Fonte.OUTRA
    assert resposta_bruta.corpo == CORPO_HTML


def test_pagina_de_carreiras_consulta_sitemap_dentro_do_mesmo_orcamento(
    tmp_path: Path,
) -> None:
    """O sitemap público é uma segunda rota para encontrar vagas ocultas."""

    catalogo = escrever_catalogo(
        tmp_path,
        (
            "empresa_123,Empresa Teste,pagina_carreiras,"
            "https://empresa.example/carreiras,true,3,aprovada"
        ),
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo), usar_cache_incremental=False)
    inicial = asyncio.run(coletar_requisicoes(spider))[0]
    resposta_inicial = HtmlResponse(
        url=inicial.url,
        request=inicial,
        status=200,
        body=CORPO_HTML,
        encoding="utf-8",
        headers={b"Content-Type": b"text/html; charset=utf-8"},
    )

    itens_iniciais = list(spider.parse(resposta_inicial, **inicial.cb_kwargs))
    requisicao_sitemap = next(item for item in itens_iniciais if isinstance(item, Request))

    assert requisicao_sitemap.url == "https://empresa.example/sitemap.xml"
    assert requisicao_sitemap.meta["observatorio_tipo_pagina"] == "inicial"

    resposta_sitemap = TextResponse(
        url=requisicao_sitemap.url,
        request=requisicao_sitemap,
        status=200,
        body=b"""
        <urlset>
          <url><loc>https://empresa.example/jobs/analista-de-dados</loc></url>
          <url><loc>https://empresa.example/sobre</loc></url>
        </urlset>
        """,
        encoding="utf-8",
        headers={b"Content-Type": b"application/xml"},
    )

    itens_sitemap = list(
        spider.parse(
            resposta_sitemap,
            **requisicao_sitemap.cb_kwargs,
        )
    )
    requisicao_vaga = next(item for item in itens_sitemap if isinstance(item, Request))

    assert requisicao_vaga.url == "https://empresa.example/jobs/analista-de-dados"
    assert requisicao_vaga.meta["observatorio_tipo_pagina"] == "detalhe_vaga"


def test_quickin_segue_so_o_sitemap_da_propria_empresa(tmp_path: Path) -> None:
    """O índice do Quickin lista centenas de empresas; só a do alvo é seguida."""

    catalogo = escrever_catalogo(
        tmp_path,
        (
            "quickin_1,Empresa Quickin,pagina_carreiras,"
            "https://jobs.quickin.io/peoplecapitalhumano/jobs,true,30,aprovada"
        ),
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo), usar_cache_incremental=False)
    inicial = asyncio.run(coletar_requisicoes(spider))[0]
    resposta_inicial = HtmlResponse(
        url=inicial.url,
        request=inicial,
        status=200,
        body=CORPO_HTML,
        encoding="utf-8",
        headers={b"Content-Type": b"text/html; charset=utf-8"},
    )

    requisicao_sitemap = next(
        item
        for item in spider.parse(resposta_inicial, **inicial.cb_kwargs)
        if isinstance(item, Request)
    )
    assert requisicao_sitemap.url == "https://jobs.quickin.io/sitemap.xml"

    indice = TextResponse(
        url=requisicao_sitemap.url,
        request=requisicao_sitemap,
        status=200,
        body=b"""
        <sitemapindex>
          <sitemap><loc>https://jobs.quickin.io/sitemaps/reply-jobs.xml</loc></sitemap>
          <sitemap><loc>https://jobs.quickin.io/sitemaps/gcareers-jobs.xml</loc></sitemap>
          <sitemap><loc>https://jobs.quickin.io/sitemaps/peoplecapitalhumano-jobs.xml</loc></sitemap>
        </sitemapindex>
        """,
        encoding="utf-8",
        headers={b"Content-Type": b"application/xml"},
    )
    pedidos = [
        item
        for item in spider.parse(indice, **requisicao_sitemap.cb_kwargs)
        if isinstance(item, Request)
    ]

    assert [pedido.url for pedido in pedidos] == [
        "https://jobs.quickin.io/sitemaps/peoplecapitalhumano-jobs.xml"
    ]


def test_smartrecruiters_nao_pede_sitemap_da_plataforma(tmp_path: Path) -> None:
    """Regressão: api.smartrecruiters.com/sitemap.xml era bloqueado a cada coleta."""

    catalogo = escrever_catalogo(
        tmp_path,
        (
            "sr_1,Bosch,pagina_carreiras,"
            "https://jobs.smartrecruiters.com/BoschGroup,true,10,aprovada"
        ),
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo), usar_cache_incremental=False)
    inicial = asyncio.run(coletar_requisicoes(spider))[0]
    assert inicial.url.startswith("https://api.smartrecruiters.com/v1/companies/BoschGroup/")
    resposta = TextResponse(
        url=inicial.url,
        request=inicial,
        status=200,
        body=b'{"content": [], "totalFound": 0}',
        encoding="utf-8",
        headers={b"Content-Type": b"application/json"},
    )

    pedidos = [
        item for item in spider.parse(resposta, **inicial.cb_kwargs) if isinstance(item, Request)
    ]

    assert not any(pedido.url.endswith("/sitemap.xml") for pedido in pedidos)


def test_spider_ativa_segunda_barreira() -> None:
    """O spider novo deve ativar o middleware de política."""

    caminho_middleware = (
        "observatorio_vagas.crawling.middlewares.BarreiraPoliticaDownloaderMiddleware"
    )

    middlewares = CatalogoFontesSpider.custom_settings["DOWNLOADER_MIDDLEWARES"]

    assert middlewares[caminho_middleware] == 75


def test_spider_rejeita_caminho_vazio() -> None:
    """O catálogo precisa ser informado por um caminho válido."""

    with pytest.raises(
        ValueError,
        match="caminho do catálogo",
    ):
        CatalogoFontesSpider(
            catalogo="  ",
        )


def test_spider_informa_catalogo_inexistente(
    tmp_path: Path,
) -> None:
    """Um arquivo ausente deve produzir o erro conhecido do catálogo."""

    caminho = tmp_path / "nao_existe.csv"

    with pytest.raises(
        ErroCatalogoFontes,
        match="não foi possível ler o catálogo",
    ):
        CatalogoFontesSpider(
            catalogo=str(caminho),
        )


def test_limite_navegacao_vale_100_com_limite_de_anuncios_e_limita_a_primeira_requisicao(
    tmp_path: Path,
) -> None:
    catalogo = escrever_catalogo(
        tmp_path,
        "aprovada,Empresa A,outra,https://aprovada.example/carreiras,true,500,aprovada",
    )

    padrao = CatalogoFontesSpider(catalogo=str(catalogo), limite_anuncios=200)
    sem_limite = CatalogoFontesSpider(catalogo=str(catalogo))
    manual = CatalogoFontesSpider(catalogo=str(catalogo), limite_anuncios=200, limite_navegacao=30)
    requisicoes = asyncio.run(coletar_requisicoes(manual))

    assert padrao.limite_navegacao == 100
    assert sem_limite.limite_navegacao is None
    assert requisicoes[0].meta["observatorio_limite_paginas"] == 30


def test_fonte_sem_nenhuma_vaga_nas_primeiras_paginas_e_encerrada(tmp_path: Path) -> None:
    catalogo = escrever_catalogo(
        tmp_path,
        "aprovada,Empresa A,outra,https://aprovada.example/carreiras,true,1,aprovada",
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo), limite_anuncios=200)

    for _ in range(19):
        spider._verificar_fonte_sem_vagas("a", candidatos_na_pagina=0, e_sitemap=False)
    assert not spider.janela_publicacao.encerrada("a")
    spider._verificar_fonte_sem_vagas("a", candidatos_na_pagina=0, e_sitemap=False)
    assert spider.janela_publicacao.encerrada("a")

    for _ in range(3):
        spider._verificar_fonte_sem_vagas("b", candidatos_na_pagina=0, e_sitemap=True)
    assert spider.janela_publicacao.encerrada("b")

    spider._verificar_fonte_sem_vagas("c", candidatos_na_pagina=5, e_sitemap=False)
    for _ in range(40):
        spider._verificar_fonte_sem_vagas("c", candidatos_na_pagina=0, e_sitemap=False)
    assert not spider.janela_publicacao.encerrada("c")


def test_lista_esgotada_tira_da_fila_so_os_detalhes_seguintes_e_as_paginas_dela(
    tmp_path: Path,
) -> None:
    from scrapy import Request

    from observatorio_vagas.crawling.janela_publicacao import DataPublicacao

    catalogo = escrever_catalogo(
        tmp_path,
        "aprovada,Empresa A,outra,https://aprovada.example/carreiras,true,500,aprovada",
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo), limite_anuncios=200, janela_horas=24)
    spider.detalhes_pendentes["a"] = {
        "https://aprovada.example/vaga/3": ("L", 3),
        "https://aprovada.example/vaga/4": ("L", 4),
        "https://aprovada.example/vaga/9": ("M", 0),
    }
    spider.navegacao_pendente["a"] = {
        "https://aprovada.example/carreiras?p=2": "L",
        "https://aprovada.example/outra?p=2": "M",
    }
    from datetime import UTC, datetime

    velha = DataPublicacao(datetime(2026, 9, 1, tzinfo=UTC), True)
    for posicao in (0, 1, 2):
        spider.janela_publicacao.registrar("a", velha, lista="L", posicao=posicao)

    spider._podar_lista_esgotada("a", "L")

    assert list(spider.detalhes_pendentes["a"]) == ["https://aprovada.example/vaga/9"]
    assert list(spider.navegacao_pendente["a"]) == ["https://aprovada.example/outra?p=2"]

    pedido = Request("https://aprovada.example/vaga/9")
    spider._marcar_origem_detalhe(pedido, ("M", 0))
    assert (pedido.meta["observatorio_lista"], pedido.meta["observatorio_posicao"]) == ("M", 0)
