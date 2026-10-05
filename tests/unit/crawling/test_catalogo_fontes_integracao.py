"""Testes da integração entre descoberta, fábrica e spider."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scrapy import Request
from scrapy.exceptions import DontCloseSpider
from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.spiders.catalogo_fontes import (
    LOTE_DETALHES,
    CatalogoFontesSpider,
)

CABECALHO = "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica"


HTML_LISTAGEM = b"""
<html>
  <body>
    <a href="/vagas/1">Vaga Python</a>
    <a href="/vagas/2">Vaga Dados</a>

    <a href="https://externa.example/jobs/3">
      Vaga externa
    </a>
  </body>
</html>
"""


def test_recupera_vinte_cards_quando_proxima_listagem_falha(tmp_path: Path) -> None:
    """Um 404 na paginação não abandona os detalhes da página anterior."""
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=25)
    corpo = (
        "".join(f'<a href="/vagas/{i}">Vaga {i}</a>' for i in range(20))
        + '<a rel="next" href="?page=2">Next</a>'
    ).encode()
    resposta = criar_resposta(inicial, corpo=corpo)
    pedidos = [x for x in spider.parse(resposta, **inicial.cb_kwargs) if isinstance(x, Request)]
    proxima = next(x for x in pedidos if "page=2" in x.url)
    list(
        spider.parse(
            HtmlResponse(url=proxima.url, request=proxima, status=404), **proxima.cb_kwargs
        )
    )
    enviados = []
    spider.crawler = SimpleNamespace(engine=SimpleNamespace(crawl=enviados.append))
    # Cada ociosidade libera um lote de detalhes (LOTE_DETALHES) até esvaziar a fila.
    for _ in range(5):
        try:
            spider.retomar_pendentes()
        except DontCloseSpider:
            continue
        break
    detalhes = [
        r for r in [*pedidos, *enviados] if r.meta["observatorio_tipo_pagina"] == "detalhe_vaga"
    ]
    assert {r.url for r in detalhes} == {f"https://empresa.example/vagas/{i}" for i in range(20)}
    assert len(detalhes) == 20
    assert not spider.detalhes_pendentes["empresa_1"]
    spider.retomar_pendentes()


@pytest.mark.parametrize("limite_detalhes", [5, 20])
def test_detalhes_nao_esperam_paginacao_com_limites_separados(
    tmp_path: Path, limite_detalhes: int
) -> None:
    """Cards entram na fila imediatamente, sem ultrapassar o orçamento."""
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=2)
    spider.limite_anuncios = limite_detalhes
    corpo = (
        "".join(f'<a href="/vagas/{i}">Vaga {i}</a>' for i in range(20))
        + '<a rel="next" href="?page=2">Next</a>'
    ).encode()
    pedidos = [
        item
        for item in spider.parse(criar_resposta(inicial, corpo=corpo), **inicial.cb_kwargs)
        if isinstance(item, Request)
    ]
    detalhes = [r for r in pedidos if r.meta["observatorio_tipo_pagina"] == "detalhe_vaga"]
    primeiro_lote = min(limite_detalhes, LOTE_DETALHES)
    assert len(detalhes) == primeiro_lote
    assert len({r.url for r in detalhes}) == primeiro_lote
    assert any("page=2" in r.url for r in pedidos)
    assert len(spider.detalhes_pendentes["empresa_1"]) == 20 - primeiro_lote


def test_retomada_respeita_limite_sem_manter_spider_aberto(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=1)
    list(spider.parse(criar_resposta(inicial), **inicial.cb_kwargs))
    crawl = Mock()
    spider.crawler = SimpleNamespace(engine=SimpleNamespace(crawl=crawl))
    spider.retomar_pendentes()
    crawl.assert_not_called()


def test_paginacao_descobre_detalhes_com_orcamento_global(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=4)
    pagina1 = criar_resposta(
        inicial,
        corpo=b'<a href="/vagas/1">Vaga</a><a rel="next" href="?page=2">Next</a>',
    )
    pedidos = [x for x in spider.parse(pagina1, **inicial.cb_kwargs) if isinstance(x, Request)]
    assert len(pedidos) == 2
    segunda = next(x for x in pedidos if "page=2" in x.url)
    assert segunda.meta["observatorio_tipo_pagina"] == "inicial"
    pagina2 = criar_resposta(
        segunda,
        corpo=(
            b'<a href="/vagas/1">Vaga repetida</a>'
            b'<a href="/vagas/2">Vaga nova</a><a href="/vagas/3">Vaga extra</a>'
            b'<a rel="next" href="?page=3">Next</a>'
        ),
    )
    novos = [x for x in spider.parse(pagina2, **segunda.cb_kwargs) if isinstance(x, Request)]
    assert [x.url for x in novos] == ["https://empresa.example/vagas/2"]
    assert len(spider.urls_agendadas["empresa_1"]) == 4
    assert not [x for x in spider.parse(pagina2, **segunda.cb_kwargs) if isinstance(x, Request)]


def test_cobertura_mostra_mecanismo_que_descobriu_os_cards(tmp_path: Path) -> None:
    """O relatório deve explicar se o card veio de HTML, estado ou JSON-LD."""

    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=2)
    list(
        spider.parse(
            criar_resposta(
                inicial,
                corpo=b'<a href="/vagas/1">Vaga Python</a>',
            ),
            **inicial.cb_kwargs,
        )
    )

    cobertura = spider.resumir_cobertura("teste")

    assert cobertura["fontes"][0]["candidatos_por_evidencia"] == {
        "palavra_na_url": 1,
        "palavra_no_texto": 1,
    }


def test_preserva_paginas_irmas_apos_falha_na_segunda(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=4)
    spider.limite_anuncios = 20
    corpo = (
        b'<div class="pagination"><a href="?page=2">2</a>'
        b'<a href="?page=3">3</a><a href="?page=4">4</a></div>'
        b'<a href="/vagas/1">Vaga</a>'
    )
    pedidos = [
        r
        for r in spider.parse(criar_resposta(inicial, corpo=corpo), **inicial.cb_kwargs)
        if isinstance(r, Request)
    ]
    assert {
        requisicao.url
        for requisicao in pedidos
        if requisicao.meta["observatorio_tipo_pagina"] == "inicial"
    } == {
        "https://empresa.example/carreiras?page=2",
        "https://empresa.example/carreiras?page=3",
        "https://empresa.example/carreiras?page=4",
    }
    segunda = next(r for r in pedidos if "page=2" in r.url)
    list(
        spider.parse(
            HtmlResponse(url=segunda.url, request=segunda, status=404), **segunda.cb_kwargs
        )
    )
    enviados = []
    spider.crawler = SimpleNamespace(engine=SimpleNamespace(crawl=enviados.append))
    # As páginas irmãs já estavam agendadas na primeira resposta. Uma falha
    # na página 2 não deve exigir uma nova rodada nem impedir páginas 3 e 4.
    spider.retomar_pendentes()
    assert enviados == []
    spider.retomar_pendentes()


def test_uma_listagem_permite_vinte_detalhes_com_limites_separados(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=1)
    spider.limite_anuncios = 20
    corpo = (
        '<a href="https://externa.example/vagas/1">Vaga externa</a>'
        + "".join(f'<a href="/vagas/{i}">Vaga {i}</a>' for i in range(25))
        + '<a rel="next" href="?page=2">Next</a>'
    ).encode()
    pedidos = [
        r
        for r in spider.parse(criar_resposta(inicial, corpo=corpo), **inicial.cb_kwargs)
        if isinstance(r, Request)
    ]
    assert len(pedidos) == LOTE_DETALHES
    assert all(r.meta["observatorio_tipo_pagina"] == "detalhe_vaga" for r in pedidos)
    enviados: list[Request] = []
    spider.crawler = SimpleNamespace(engine=SimpleNamespace(crawl=enviados.append))
    for _ in range(5):
        try:
            spider.retomar_pendentes()
        except DontCloseSpider:
            continue
        break
    assert len(pedidos) + len(enviados) == 20
    assert len(spider.urls_agendadas["empresa_1"]) == 21
    crawl = Mock()
    spider.crawler = SimpleNamespace(engine=SimpleNamespace(crawl=crawl))
    spider.retomar_pendentes()
    crawl.assert_not_called()


def test_duas_listagens_nao_consumem_limite_de_detalhes(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=2)
    spider.limite_anuncios = 3
    corpo = b'<a href="/vagas/1">Vaga</a><a rel="next" href="?page=2">Next</a>'
    pedidos = [
        r
        for r in spider.parse(criar_resposta(inicial, corpo=corpo), **inicial.cb_kwargs)
        if isinstance(r, Request)
    ]
    proxima = next(r for r in pedidos if r.meta["observatorio_tipo_pagina"] == "inicial")
    corpo2 = (
        b'<a href="/vagas/2">Vaga</a><a href="/vagas/3">Vaga</a>'
        b'<a rel="next" href="?page=3">Next</a>'
    )
    novos = [
        r
        for r in spider.parse(criar_resposta(proxima, corpo=corpo2), **proxima.cb_kwargs)
        if isinstance(r, Request)
    ]
    assert len(novos) == 2
    assert len(spider.urls_agendadas["empresa_1"]) == 5
    assert len(spider.urls_listagem_agendadas["empresa_1"]) == 2


def test_primeira_paginacao_tem_prioridade_sobre_cards_excedentes(tmp_path: Path) -> None:
    """Uma fonte paginada alcança a página 2 mesmo com muitos cards iniciais."""

    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=4)
    pagina = criar_resposta(
        inicial,
        corpo=(
            b'<a href="/vagas/1">Vaga 1</a>'
            b'<a href="/vagas/2">Vaga 2</a>'
            b'<a href="/vagas/3">Vaga 3</a>'
            b'<a rel="next" href="?page=2">Next</a>'
        ),
    )

    requisicoes = [
        item for item in spider.parse(pagina, **inicial.cb_kwargs) if isinstance(item, Request)
    ]

    assert [requisicao.url for requisicao in requisicoes] == [
        "https://empresa.example/carreiras?page=2",
        "https://empresa.example/vagas/1",
    ]
    assert requisicoes[0].meta["observatorio_tipo_pagina"] == "inicial"


def test_paginacao_profunda_divide_orcamento_com_detalhes(tmp_path: Path) -> None:
    """O crawler alcança a página 3 sem abandonar os detalhes já encontrados."""

    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=6)
    pagina_1 = criar_resposta(
        inicial,
        corpo=(
            b'<a href="/vagas/1">Vaga 1</a><a href="/vagas/2">Vaga 2</a>'
            b'<a rel="next" href="?page=2">Next</a>'
        ),
    )
    requisicoes_1 = [
        item for item in spider.parse(pagina_1, **inicial.cb_kwargs) if isinstance(item, Request)
    ]
    pagina_2_request = next(item for item in requisicoes_1 if "page=2" in item.url)

    pagina_2 = criar_resposta(
        pagina_2_request,
        corpo=(
            b'<a href="/vagas/3">Vaga 3</a><a href="/vagas/4">Vaga 4</a>'
            b'<a rel="next" href="?page=3">Next</a>'
        ),
    )
    requisicoes_2 = [
        item
        for item in spider.parse(pagina_2, **pagina_2_request.cb_kwargs)
        if isinstance(item, Request)
    ]
    pagina_3_request = next(item for item in requisicoes_2 if "page=3" in item.url)

    pagina_3 = criar_resposta(
        pagina_3_request,
        corpo=b'<a href="/vagas/5">Vaga 5</a>',
    )
    requisicoes_3 = [
        item
        for item in spider.parse(pagina_3, **pagina_3_request.cb_kwargs)
        if isinstance(item, Request)
    ]

    assert [item.url for item in requisicoes_3] == ["https://empresa.example/vagas/3"]
    assert len(spider.urls_listagem_agendadas["empresa_1"]) == 3


def test_paginacao_bloqueia_externos_e_ciclos(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=10)
    resposta = criar_resposta(
        inicial,
        corpo=(
            b'<a rel="next" href="https://externa.example/page/2">Next</a>'
            b'<a rel="next" href="/login">Next</a>'
            b'<a rel="next" href="/carreiras#top">Next</a>'
        ),
    )
    assert not [x for x in spider.parse(resposta, **inicial.cb_kwargs) if isinstance(x, Request)]


def test_pagina_institucional_chega_a_listagem_e_vaga(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=3)
    resposta = criar_resposta(inicial, corpo=b'<a href="/jobs">Vagas</a>')
    listagem = next(
        x for x in spider.parse(resposta, **inicial.cb_kwargs) if isinstance(x, Request)
    )
    assert listagem.meta["observatorio_tipo_pagina"] == "inicial"
    pagina = criar_resposta(listagem, corpo=b'<a href="/jobs/123">Vaga Python</a>')
    detalhes = [x for x in spider.parse(pagina, **listagem.cb_kwargs) if isinstance(x, Request)]
    assert [x.url for x in detalhes] == ["https://empresa.example/jobs/123"]


def test_dois_alvos_mesma_url_possuem_orcamentos_independentes(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=2)
    outra = inicial.replace(
        meta={**inicial.meta, "observatorio_alvo_id": "empresa_2"},
        cb_kwargs={**inicial.cb_kwargs, "alvo_id": "empresa_2"},
    )
    for pedido in (inicial, outra):
        resposta = criar_resposta(pedido)
        assert (
            len([x for x in spider.parse(resposta, **pedido.cb_kwargs) if isinstance(x, Request)])
            == 1
        )


async def coletar_requisicoes_iniciais(
    spider: CatalogoFontesSpider,
) -> list[Request]:
    """Consome o método assíncrono start do spider."""

    return [requisicao async for requisicao in spider.start()]


def criar_spider_e_requisicao(
    tmp_path: Path,
    *,
    limite_paginas: int,
) -> tuple[CatalogoFontesSpider, Request]:
    """Cria um spider com uma fonte aprovada."""

    catalogo = tmp_path / "fontes_integracao.csv"

    catalogo.write_text(
        "\n".join(
            [
                CABECALHO,
                (
                    "empresa_1,Empresa Um,outra,"
                    "https://empresa.example/carreiras,"
                    f"true,{limite_paginas},aprovada"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    spider = CatalogoFontesSpider(
        catalogo=str(catalogo),
    )

    requisicoes = asyncio.run(coletar_requisicoes_iniciais(spider))

    return spider, requisicoes[0]


def criar_resposta(
    requisicao: Request,
    *,
    status: int = 200,
    corpo: bytes = HTML_LISTAGEM,
) -> HtmlResponse:
    """Cria uma resposta HTML ligada à requisição recebida."""

    return HtmlResponse(
        url=requisicao.url,
        request=requisicao,
        status=status,
        body=corpo,
        encoding="utf-8",
        headers={
            b"Content-Type": b"text/html; charset=utf-8",
        },
    )


def test_pagina_inicial_e_salva_e_detalhes_sao_agendados(
    tmp_path: Path,
) -> None:
    """A integração deve produzir um item e requisições seguras."""

    spider, requisicao_inicial = criar_spider_e_requisicao(
        tmp_path,
        limite_paginas=3,
    )

    resposta = criar_resposta(requisicao_inicial)

    resultados = list(
        spider.parse(
            resposta,
            **requisicao_inicial.cb_kwargs,
        )
    )

    # O primeiro resultado é a página inicial preservada.
    assert isinstance(
        resultados[0],
        RespostaBruta,
    )

    detalhes = [resultado for resultado in resultados if isinstance(resultado, Request)]

    # O link externo é descoberto, mas a fábrica o bloqueia.
    assert [detalhe.url for detalhe in detalhes] == [
        "https://empresa.example/vagas/1",
        "https://empresa.example/vagas/2",
    ]

    assert all(detalhe.callback == spider.parse for detalhe in detalhes)


def test_pagina_de_detalhe_e_salva_sem_descobrir_outras(
    tmp_path: Path,
) -> None:
    """Detalhes não devem criar uma navegação sem limite."""

    spider, requisicao_inicial = criar_spider_e_requisicao(
        tmp_path,
        limite_paginas=3,
    )

    resposta_inicial = criar_resposta(requisicao_inicial)

    resultados_iniciais = list(
        spider.parse(
            resposta_inicial,
            **requisicao_inicial.cb_kwargs,
        )
    )

    requisicao_detalhe = next(
        resultado for resultado in resultados_iniciais if isinstance(resultado, Request)
    )

    # Mesmo contendo outro link de vaga, esta página já é detalhe.
    resposta_detalhe = criar_resposta(
        requisicao_detalhe,
        corpo=(b'<html><a href="/vagas/999">Outra vaga</a></html>'),
    )

    resultados_detalhe = list(
        spider.parse(
            resposta_detalhe,
            **requisicao_detalhe.cb_kwargs,
        )
    )

    # Apenas a resposta bruta é produzida.
    assert len(resultados_detalhe) == 1

    assert isinstance(
        resultados_detalhe[0],
        RespostaBruta,
    )


def test_resposta_com_erro_e_preservada_sem_descoberta(
    tmp_path: Path,
) -> None:
    """Erros HTTP são auditados, mas não ampliam a coleta."""

    spider, requisicao_inicial = criar_spider_e_requisicao(
        tmp_path,
        limite_paginas=3,
    )

    resposta = criar_resposta(
        requisicao_inicial,
        status=500,
    )

    resultados = list(
        spider.parse(
            resposta,
            **requisicao_inicial.cb_kwargs,
        )
    )

    assert len(resultados) == 1

    assert isinstance(
        resultados[0],
        RespostaBruta,
    )

    assert resultados[0].status_http == 500


def test_limite_um_preserva_somente_pagina_inicial(
    tmp_path: Path,
) -> None:
    """O catálogo pode impedir totalmente a coleta de detalhes."""

    spider, requisicao_inicial = criar_spider_e_requisicao(
        tmp_path,
        limite_paginas=1,
    )

    resposta = criar_resposta(requisicao_inicial)

    resultados = list(
        spider.parse(
            resposta,
            **requisicao_inicial.cb_kwargs,
        )
    )

    assert len(resultados) == 1

    assert isinstance(
        resultados[0],
        RespostaBruta,
    )


def test_spider_gupy_descobre_next_data_sem_ancoras(
    tmp_path: Path,
) -> None:
    """O spider seleciona o adaptador pela fonte informada no catálogo."""

    catalogo = tmp_path / "fontes_gupy.csv"
    catalogo.write_text(
        "\n".join(
            [
                CABECALHO,
                ("gupy_1,Empresa Gupy,gupy,https://empresa.gupy.io/,true,3,somente_coleta"),
                "",
            ]
        ),
        encoding="utf-8",
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo))
    requisicao = asyncio.run(coletar_requisicoes_iniciais(spider))[0]
    dados = {
        "props": {
            "pageProps": {
                "jobs": [
                    {"id": 10, "title": "Vaga Dez"},
                    {"id": 20, "title": "Vaga Vinte"},
                ]
            }
        }
    }
    corpo = (
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(dados)}</script>'
    ).encode()
    resposta = criar_resposta(
        requisicao,
        corpo=corpo,
    )

    resultados = list(
        spider.parse(
            resposta,
            **requisicao.cb_kwargs,
        )
    )
    detalhes = [resultado for resultado in resultados if isinstance(resultado, Request)]

    assert [detalhe.url for detalhe in detalhes] == [
        "https://empresa.gupy.io/jobs/10",
        "https://empresa.gupy.io/jobs/20",
    ]


def test_cada_detalhe_concluido_libera_o_proximo_do_lote(tmp_path: Path) -> None:
    spider, inicial = criar_spider_e_requisicao(tmp_path, limite_paginas=25)
    spider.limite_anuncios = 50
    corpo = "".join(f'<a href="/vagas/{i}">Vaga {i}</a>' for i in range(12)).encode()
    pedidos = [
        r
        for r in spider.parse(criar_resposta(inicial, corpo=corpo), **inicial.cb_kwargs)
        if isinstance(r, Request)
    ]
    assert len(pedidos) == LOTE_DETALHES
    assert spider.detalhes_em_voo["empresa_1"] == LOTE_DETALHES
    assert pedidos[0].meta["observatorio_posicao"] == 0

    enviados: list[Request] = []
    spider.crawler = SimpleNamespace(engine=SimpleNamespace(crawl=enviados.append))
    list(spider.parse(criar_resposta(pedidos[0], corpo=b"<h1>Vaga</h1>"), **pedidos[0].cb_kwargs))

    assert [r.url for r in enviados] == ["https://empresa.example/vagas/8"]
    assert spider.detalhes_em_voo["empresa_1"] == LOTE_DETALHES


def test_pedido_descartado_de_proposito_nao_conta_como_falha_da_fonte(tmp_path: Path) -> None:
    from scrapy.exceptions import IgnoreRequest

    spider, _ = criar_spider_e_requisicao(tmp_path, limite_paginas=25)
    spider.tratar_falha_download(
        SimpleNamespace(
            request=Request(
                "https://empresa.example/vagas/9",
                meta={
                    "observatorio_alvo_id": "empresa_1",
                    "observatorio_tipo_pagina": "detalhe_vaga",
                },
            ),
            value=IgnoreRequest("listagem esgotada"),
        )
    )

    assert spider.falhas_download["empresa_1"] == 0
