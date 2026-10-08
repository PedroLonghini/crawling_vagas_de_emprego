"""Renderização isolada e limites de navegação do navegador."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.robotparser import RobotFileParser

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.settings import Settings

from observatorio_vagas.crawling.javascript import (
    BOTAO_MAIS,
    BOTAO_PROXIMA_PAGINA,
    RenderizacaoJavaScriptMiddleware,
    aguardar_conteudo,
    permite_recurso,
    preservar_descobertas,
)


def test_espera_nao_termina_enquanto_consulta_pendente():
    page = SimpleNamespace(
        wait_for_timeout=AsyncMock(),
        evaluate=AsyncMock(return_value="conteudo estavel"),
        locator=Mock(return_value=SimpleNamespace(count=AsyncMock(return_value=0))),
    )
    pendente = Mock(side_effect=[True, True, False])
    asyncio.run(aguardar_conteudo(page, 0, pendente))
    assert pendente.call_count == 3
    assert page.evaluate.await_count == 5


def test_espera_tem_limite_mesmo_com_componente_ocupado():
    page = SimpleNamespace(
        wait_for_timeout=AsyncMock(),
        evaluate=AsyncMock(return_value="conteudo estavel"),
        locator=Mock(return_value=SimpleNamespace(count=AsyncMock(return_value=1))),
    )
    asyncio.run(aguardar_conteudo(page, 0))
    assert page.evaluate.await_count == 40


def test_preserva_links_e_documentos_removidos_sem_duplicar():
    from observatorio_vagas.extraction.json_ld import extrair_job_postings_json_ld

    antigo = """<html><body><a href="/vaga/1">Analista</a>
    <script type="application/ld+json">{"@type":"JobPosting","title":"Analista"}</script>
    </body></html>"""
    atual = '<html><body><a href="/vaga/2">Técnico</a></body></html>'
    corpo = preservar_descobertas("https://empresa.example/jobs", [antigo, antigo, atual], 10000)
    resposta = HtmlResponse("https://empresa.example/jobs", body=corpo, encoding="utf-8")
    assert resposta.css("a::attr(href)").getall() == ["/vaga/2", "https://empresa.example/vaga/1"]
    assert len(extrair_job_postings_json_ld(corpo).vagas) == 1


def test_botoes_nao_confundem_candidatura_com_expansao():
    assert BOTAO_MAIS.fullmatch("Carregar mais vagas")
    assert BOTAO_MAIS.fullmatch("Load more jobs")
    assert not BOTAO_MAIS.fullmatch("Candidatar-se")
    assert not BOTAO_MAIS.fullmatch("Enviar currículo")


def test_botao_proxima_pagina_reconhece_apenas_paginacao():
    assert BOTAO_PROXIMA_PAGINA.fullmatch("Next page")
    assert BOTAO_PROXIMA_PAGINA.fullmatch("Próxima página")
    assert not BOTAO_PROXIMA_PAGINA.fullmatch("Próximas vagas")
    assert not BOTAO_PROXIMA_PAGINA.fullmatch("Candidatar-se")


def test_recursos_respeitam_origem_robots_e_metodo():
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Disallow: /privado"])
    origem = "https://empresa.example/vagas"
    assert permite_recurso(
        "https://empresa.example/main.js", origem, "GET", "script", robots, "bot"
    )
    for url, metodo, tipo in [
        ("https://externo.example/main.js", "GET", "script"),
        ("https://empresa.example/privado/api", "GET", "fetch"),
        (origem, "POST", "fetch"),
        (origem, "GET", "document"),
    ]:
        assert not permite_recurso(url, origem, metodo, tipo, robots, "bot")


def test_greenhouse_permite_apenas_dependencias_tecnicas_conhecidas():
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Allow: /"])
    origem = "https://job-boards.greenhouse.io/xpinc"

    assert permite_recurso(
        "https://job-boards.cdn.greenhouse.io/assets/app.js",
        origem,
        "GET",
        "script",
        robots,
        "bot",
    )
    assert permite_recurso(
        "https://boards-api.greenhouse.io/v1/boards/xpinc/jobs",
        origem,
        "GET",
        "fetch",
        robots,
        "bot",
    )
    assert not permite_recurso(
        "https://qualquer-coisa.greenhouse.io/script.js",
        origem,
        "GET",
        "script",
        robots,
        "bot",
    )


def test_portal_senior_permite_somente_consultas_publicas_da_empresa():
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Allow: /"])
    origem = "https://hcor.portaldetalentos.senior.com.br/jobs"
    consulta = (
        "https://platform.senior.com.br/t/senior.com.br/bridge/1.0/anonymous/rest/"
        "hcm/careersmanagercandidate/queries/searchVacancies"
    )
    perfil = (
        "https://platform.senior.com.br/t/senior.com.br/bridge/1.0/anonymous/rest/"
        "hcm/careersmanagercandidate/actions/getProfileIdBySubdomain"
    )

    assert permite_recurso(consulta, origem, "POST", "fetch", robots, "bot")
    assert permite_recurso(consulta, origem, "OPTIONS", "xhr", robots, "bot")
    assert permite_recurso(perfil, origem, "POST", "fetch", robots, "bot")
    assert permite_recurso(origem, origem, "GET", "document", robots, "bot")

    for url, metodo, tipo in [
        (consulta.replace("queries/searchVacancies", "actions/finishCandidature"), "POST", "fetch"),
        (consulta, "PUT", "fetch"),
        (consulta, "POST", "document"),
    ]:
        assert not permite_recurso(url, origem, metodo, tipo, robots, "bot")


def test_renderizacao_substitui_html_e_respeita_limite(monkeypatch):
    crawler = SimpleNamespace(
        settings=Settings({"JAVASCRIPT_MAX_PAGES_PER_TARGET": 1}), stats=Mock()
    )
    middleware = RenderizacaoJavaScriptMiddleware(crawler)
    middleware.robots["https://empresa.example/robots.txt"] = "User-agent: *\nAllow: /"
    monkeypatch.setattr(
        "observatorio_vagas.crawling.javascript.renderizar_em_thread",
        lambda *args: b'<html><a href="/vaga/1">Python</a></html>',
    )
    pedido = Request(
        "https://empresa.example/vagas",
        meta={
            "observatorio_alvo_id": "teste",
            "observatorio_dominio": "empresa.example",
            "observatorio_status_politica": "somente_coleta",
            "observatorio_requisicao_autorizada": True,
            "observatorio_tipo_pagina": "inicial",
        },
    )
    resposta = HtmlResponse(pedido.url, request=pedido, body=b"<html></html>", encoding="utf-8")

    async def executar():
        renderizada = await middleware.process_response(pedido, resposta)
        assert b"Python" in renderizada.body
        assert pedido.meta["observatorio_javascript"] == "renderizado"
        assert await middleware.process_response(pedido, resposta) is resposta
        assert pedido.meta["observatorio_javascript"] == "limite"

    asyncio.run(executar())


def test_renderizacao_nao_abre_navegador_para_detalhe(monkeypatch):
    crawler = SimpleNamespace(settings=Settings(), stats=Mock())
    middleware = RenderizacaoJavaScriptMiddleware(crawler)
    renderizar = Mock()
    monkeypatch.setattr("observatorio_vagas.crawling.javascript.renderizar_em_thread", renderizar)
    pedido = Request(
        "https://empresa.example/vaga/1",
        meta={
            "observatorio_alvo_id": "teste",
            "observatorio_dominio": "empresa.example",
            "observatorio_status_politica": "somente_coleta",
            "observatorio_requisicao_autorizada": True,
            "observatorio_tipo_pagina": "detalhe_vaga",
        },
    )
    resposta = HtmlResponse(pedido.url, request=pedido, body=b"<html></html>", encoding="utf-8")

    assert asyncio.run(middleware.process_response(pedido, resposta)) is resposta
    renderizar.assert_not_called()


def test_renderizacao_e_dispensada_quando_html_ja_tem_link_de_vaga(monkeypatch):
    crawler = SimpleNamespace(settings=Settings(), stats=Mock())
    middleware = RenderizacaoJavaScriptMiddleware(crawler)
    renderizar = Mock()
    monkeypatch.setattr("observatorio_vagas.crawling.javascript.renderizar_em_thread", renderizar)
    pedido = Request(
        "https://empresa.example/vagas",
        meta={
            "observatorio_alvo_id": "teste",
            "observatorio_dominio": "empresa.example",
            "observatorio_status_politica": "somente_coleta",
            "observatorio_requisicao_autorizada": True,
            "observatorio_tipo_pagina": "inicial",
        },
    )
    resposta = HtmlResponse(
        pedido.url,
        request=pedido,
        body=b'<a href="/vagas/analista">Analista</a>',
        encoding="utf-8",
    )

    assert asyncio.run(middleware.process_response(pedido, resposta)) is resposta
    assert pedido.meta["observatorio_javascript"] == "dispensado_html_estatico"
    renderizar.assert_not_called()


def test_renderizacao_considera_adaptador_especifico_antes_de_abrir_chromium(monkeypatch):
    crawler = SimpleNamespace(settings=Settings(), stats=Mock())
    middleware = RenderizacaoJavaScriptMiddleware(crawler)
    renderizar = Mock()
    monkeypatch.setattr("observatorio_vagas.crawling.javascript.renderizar_em_thread", renderizar)
    url = "https://careers.dhl.com/amer/pt/jobs"
    pedido = Request(
        url,
        cb_kwargs={"fonte": "pagina_carreiras"},
        meta={
            "observatorio_alvo_id": "dhl",
            "observatorio_dominio": "careers.dhl.com",
            "observatorio_status_politica": "somente_coleta",
            "observatorio_requisicao_autorizada": True,
            "observatorio_tipo_pagina": "inicial",
        },
    )
    resposta = HtmlResponse(
        url,
        request=pedido,
        body=b'<a href="/amer/pt/job/analista/123">Analista</a>',
        encoding="utf-8",
    )

    assert asyncio.run(middleware.process_response(pedido, resposta)) is resposta
    assert pedido.meta["observatorio_javascript"] == "dispensado_html_estatico"
    renderizar.assert_not_called()


def test_erro_preserva_original(monkeypatch):
    crawler = SimpleNamespace(settings=Settings(), stats=Mock())
    middleware = RenderizacaoJavaScriptMiddleware(crawler)
    middleware.robots["https://empresa.example/robots.txt"] = "User-agent: *\nAllow: /"

    def falhar(*args):
        raise TimeoutError("tempo esgotado")

    monkeypatch.setattr("observatorio_vagas.crawling.javascript.renderizar_em_thread", falhar)
    pedido = Request(
        "https://empresa.example/vagas",
        meta={
            "observatorio_alvo_id": "teste",
            "observatorio_dominio": "empresa.example",
            "observatorio_status_politica": "somente_coleta",
            "observatorio_requisicao_autorizada": True,
            "observatorio_tipo_pagina": "inicial",
        },
    )
    resposta = HtmlResponse(pedido.url, request=pedido, body=b"<html></html>", encoding="utf-8")
    assert asyncio.run(middleware.process_response(pedido, resposta)) is resposta
    assert pedido.meta["observatorio_javascript"] == "falha"
