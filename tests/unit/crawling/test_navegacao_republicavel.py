"""Regressões: índice, paginação, recursos, orçamento e extração integrada."""

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scrapy import Request
from scrapy.http import HtmlResponse, TextResponse

from observatorio_vagas.crawling.adaptadores.ckan import AdaptadorCkan
from observatorio_vagas.crawling.raw_storage import ArmazenamentoBrutoLocal
from observatorio_vagas.crawling.spiders.catalogo_fontes import CatalogoFontesSpider
from observatorio_vagas.extraction.processador import processar_respostas_brutas


def criar_spider(tmp_path: Path, fonte: str, url: str, limite: int = 10):
    catalogo = tmp_path / "catalogo.csv"
    catalogo.write_text(
        "alvo_id,empresa_nome,fonte,url_inicial,ativa,limite_paginas,status_politica\n"
        f"teste,Empresa Exemplo,{fonte},{url},true,{limite},somente_coleta\n",
        encoding="utf-8",
    )
    spider = CatalogoFontesSpider(catalogo=str(catalogo))

    async def inicio():
        return [request async for request in spider.start()]

    return spider, asyncio.run(inicio())[0]


def analisar(spider, request, corpo, tipo="text/html", url=None):
    classe = HtmlResponse if tipo == "text/html" else TextResponse
    response = classe(
        url=url or request.url,
        request=request,
        body=corpo.encode(),
        encoding="utf-8",
        headers={"Content-Type": tipo},
    )
    return list(spider.parse(response, **request.cb_kwargs))


def test_querido_diario_percorre_dez_paginas_e_para(tmp_path: Path) -> None:
    spider, request = criar_spider(
        tmp_path,
        "querido_diario",
        "https://api.queridodiario.org.br/gazettes?size=50&territory_ids=3550308",
    )
    offsets = []
    for pagina in range(10):
        offsets.append(int(parse_qs(urlsplit(request.url).query).get("offset", [0])[0]))
        saida = analisar(
            spider,
            request,
            json.dumps(
                {
                    "total_gazettes": 800,
                    "gazettes": [{"date": "2026-09-08"}],
                }
            ),
            "application/json",
        )
        seguintes = [item for item in saida if isinstance(item, Request)]
        if pagina < 9:
            assert len(seguintes) == 1
            request = seguintes[0]
            assert request.meta["observatorio_tipo_pagina"] == "inicial"
            assert request.meta["observatorio_publicacao_autorizada_na_coleta"] is False
        else:
            assert seguintes == []
    assert offsets == list(range(0, 500, 50))
    assert spider.paginas_recebidas["teste"] == 10
    assert len(spider.urls_agendadas["teste"]) == 10


def test_pagina_vazia_nao_inventa_navegacao(tmp_path: Path) -> None:
    spider, request = criar_spider(tmp_path, "querido_diario", "https://qd.example/gazettes")
    saida = analisar(spider, request, '{"total_gazettes":999,"gazettes":[]}', "application/json")
    assert len(saida) == 1  # somente o bruto, mesmo com total inconsistente


def test_ckan_html_coleta_dois_csvs_e_extrai_varias_vagas(tmp_path: Path) -> None:
    spider, request = criar_spider(tmp_path, "ckan", "https://dados.example/dataset/vagas")
    html = """
    <li class="resource-item"><span data-format="csv"></span>
      <a href="/dataset/vagas/resource/a">Visualizar</a>
      <a href="/dataset/vagas/resource/a/download/a.csv">Baixar</a></li>
    <li class="resource-item"><span data-format="csv"></span>
      <a href="/dataset/vagas/resource/b/download/b.csv">Baixar</a></li>
    <li class="resource-item"><span data-format="pdf"></span>
      <a href="/dataset/vagas/resource/manual">Dicionário</a></li>
    """
    bruto = ArmazenamentoBrutoLocal(tmp_path / "raw")
    saida = analisar(spider, request, html)
    bruto.salvar(saida[0])
    requests = [x for x in saida if isinstance(x, Request)]
    assert len(requests) == 2
    for numero, req in enumerate(requests):
        csv = "VAGA,CÓDIGO DA VAGA,posto,DESCRIÇÃO DA VAGA\n"
        csv += f"Analista,{numero}1,Anchieta,Atendimento ao público\n"
        csv += f"Técnico,{numero}2,Anchieta,Suporte técnico\n"
        bruto.salvar(analisar(spider, req, csv, "text/csv")[0])
    resultado = processar_respostas_brutas(tmp_path / "raw", alvo_id="teste")
    assert resultado.paginas_analisadas == 3
    assert len(resultado.anuncios) == 4
    assert resultado.falhas == ()


def test_ckan_indice_recurso_download_e_barreira_de_dominio(tmp_path: Path) -> None:
    spider, request = criar_spider(tmp_path, "ckan", "https://dados.example/dataset/vagas")
    saida = analisar(
        spider,
        request,
        """
        <li class="resource-item"><span data-format="csv"></span>
        <a href="/dataset/vagas/resource/a">Visualizar</a></li>
    """,
    )
    req = saida[1]
    assert req.meta["observatorio_tipo_pagina"] == "inicial"
    saida = analisar(
        spider,
        req,
        """
        <a class="resource-url-analytics" href="/dataset/vagas/resource/a/download/a.csv">CSV</a>
        <a class="resource-url-analytics" href="https://externo.example/file.csv">Externo</a>
    """,
    )
    assert len(saida) == 2
    assert saida[1].url.endswith("/a.csv")


def test_ckan_nao_descarta_recurso_atual_sem_ano() -> None:
    resposta = TextResponse(
        url="https://dados.example/api/3/action/package_show",
        body=json.dumps(
            {
                "success": True,
                "result": {
                    "resources": [
                        {"name": nome, "format": "CSV", "url": f"https://dados.example/{nome}.csv"}
                        for nome in ("2025", "2026", "atual")
                    ]
                },
            }
        ).encode(),
        encoding="utf-8",
    )
    assert [c.texto for c in AdaptadorCkan().descobrir(resposta)] == ["2026", "atual"]


def test_html_paginacao_opaca_e_redirecionamento_nao_gastam_duas_posicoes(tmp_path: Path) -> None:
    spider, request = criar_spider(tmp_path, "outra", "https://empresa.example/vagas", 3)
    saida = analisar(
        spider,
        request,
        '<div class="pager"><a href="/lista?cursor=abc">»</a></div>',
        url="https://empresa.example/vagas/",
    )
    assert len(saida) == 2
    req = saida[1]
    assert req.meta["observatorio_tipo_pagina"] == "inicial"
    saida = analisar(
        spider,
        req,
        '<a href="/vagas/123">Analista</a><a rel="next" href="/lista?cursor=def">Next</a>',
    )
    assert len(saida) == 2  # só resta espaço para o detalhe
    assert saida[1].url == "https://empresa.example/vagas/123"
