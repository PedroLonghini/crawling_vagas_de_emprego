"""Renderização opcional e limitada, em uma thread compatível com Windows."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections import Counter
from html import escape
from importlib.util import find_spec
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from scrapy import Request
from scrapy.exceptions import NotConfigured
from scrapy.http import HtmlResponse
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.defer import maybe_deferred_to_future

from observatorio_vagas.crawling.adaptadores import selecionar_adaptador
from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.crawling.filtro_conteudo import eh_conteudo_nao_empregaticio
from observatorio_vagas.domain.enums import Fonte
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio
from observatorio_vagas.extraction.json_ld import extrair_job_postings_json_ld

logger = logging.getLogger(__name__)

BOTAO_MAIS = re.compile(
    r"^\s*(carregar mais(?: vagas| resultados)?|mostrar mais(?: vagas| resultados)?|"
    r"ver mais vagas|load more(?: jobs| results)?|show more(?: jobs| results)?)\s*$",
    re.IGNORECASE,
)

# Algumas plataformas de recrutamento não oferecem a paginação como links.
# O Greenhouse, por exemplo, usa um botão com o rótulo acessível "Next page".
# Aceitamos apenas rótulos de próxima página, nunca botões de navegação geral.
BOTAO_PROXIMA_PAGINA = re.compile(
    r"^\s*(next page|pr[oó]xima p[aá]gina|next|pr[oó]xima)\s*$",
    re.IGNORECASE,
)

# O quadro público da Greenhouse carrega o HTML em job-boards.greenhouse.io,
# mas hospeda seus scripts e a consulta pública de vagas nestes subdomínios.
# Esta é uma lista fechada, usada somente para renderizar uma página Greenhouse
# já autorizada pelo catálogo; não libera recursos de terceiros em geral.
_RECURSOS_GREENHOUSE = frozenset(
    {
        "boards-api.greenhouse.io",
        "job-boards.cdn.greenhouse.io",
        "s2-recruiting.cdn.greenhouse.io",
    }
)

# O Portal de Talentos da Senior hospeda a página de cada empresa em um
# subdomínio próprio, mas consulta a API pública de vagas neste domínio fixo.
# A exceção é propositalmente estreita: só vale para páginas do Portal Senior
# já autorizadas pelo catálogo e não libera outras dependências externas.
_RECURSOS_PORTAL_SENIOR = frozenset(
    {
        "platform.senior.com.br",
    }
)

CAMINHO_CONSULTAS_PUBLICAS_PORTAL_SENIOR = (
    "/t/senior.com.br/bridge/1.0/anonymous/rest/"
    "hcm/careersmanagercandidate/queries/"
)

CAMINHO_PERFIL_PUBLICO_PORTAL_SENIOR = (
    "/t/senior.com.br/bridge/1.0/anonymous/rest/"
    "hcm/careersmanagercandidate/actions/getProfileIdBySubdomain"
)


def preservar_descobertas(url, snapshots, max_bytes):
    """Preserva links e JSON-LD que listas virtualizadas retiraram do DOM."""
    ultimo = snapshots[-1]
    adaptador = AdaptadorGenericoHTML()
    atuais = {
        c.url
        for c in adaptador.descobrir(HtmlResponse(url, body=ultimo.encode(), encoding="utf-8"))
    }
    documentos = {
        json.dumps(v, sort_keys=True, ensure_ascii=False)
        for v in extrair_job_postings_json_ld(ultimo).vagas
    }
    extras = []
    for html in snapshots[:-1]:
        for candidato in adaptador.descobrir(
            HtmlResponse(url, body=html.encode(), encoding="utf-8")
        ):
            if candidato.url not in atuais:
                atuais.add(candidato.url)
                extras.append(
                    f'<a href="{escape(candidato.url, quote=True)}">{escape(candidato.texto)}</a>'
                )
        for vaga in extrair_job_postings_json_ld(html).vagas:
            texto = json.dumps(vaga, sort_keys=True, ensure_ascii=False)
            if texto not in documentos:
                documentos.add(texto)
                extras.append(
                    '<script type="application/ld+json">'
                    + texto.replace("<", "\\u003c")
                    + "</script>"
                )
    suplemento = (
        '<section hidden data-observatorio="descobertas-dinamicas">'
        + "".join(extras)
        + "</section>"
    )
    if extras:
        posicao = ultimo.lower().rfind("</body>")
        ultimo = (
            ultimo[:posicao] + suplemento + ultimo[posicao:]
            if posicao >= 0
            else ultimo + suplemento
        )
    corpo = ultimo.encode("utf-8")
    if len(corpo) > max_bytes:
        raise ValueError("HTML renderizado excedeu DOWNLOAD_MAXSIZE")
    return corpo


async def aguardar_conteudo(page, espera, carregando=lambda: False):
    """Espera mínima seguida de confirmação de estabilidade do texto e links."""
    from playwright.async_api import Error as PlaywrightError

    await page.wait_for_timeout(max(0, espera) * 1000)
    anterior = None
    iguais = 0
    for _ in range(40):
        try:
            atual = await page.evaluate(
                "() => JSON.stringify([document.body?.innerText, "
                "Array.from(document.querySelectorAll('a[href]'), a => a.href)])"
            )
            ocupado = await page.locator('[aria-busy="true"]:visible').count()
        except PlaywrightError:
            # Alguns portais SPA trocam de rota logo depois de resolverem o
            # perfil público da empresa. A execução continua na nova página.
            anterior = None
            iguais = 0
            await page.wait_for_timeout(250)
            continue
        iguais = iguais + 1 if atual == anterior else 0
        if iguais >= 2 and not carregando() and not ocupado:
            return
        anterior = atual
        await page.wait_for_timeout(250)


async def expandir_listagem(
    page, snapshots, espera, rodadas, max_bytes, diagnostico=None, carregando=lambda: False
):
    """Expande cards e avança na paginação sem abandonar descobertas anteriores."""
    sem_mudanca = 0
    diagnostico = diagnostico if diagnostico is not None else {}
    for _ in range(rodadas):
        html_anterior = snapshots[-1]
        diagnostico["rodadas"] = diagnostico.get("rodadas", 0) + 1
        clicou_expansao = False
        botoes = page.get_by_role("button", name=BOTAO_MAIS)
        for indice in range(min(await botoes.count(), 10)):
            botao = botoes.nth(indice)
            if not (await botao.is_visible() and await botao.is_enabled()):
                continue
            if await botao.evaluate("e => !!e.closest('form') || !!e.getAttribute('href')"):
                continue
            await botao.click(timeout=1500)
            diagnostico["cliques"] = diagnostico.get("cliques", 0) + 1
            clicou_expansao = True
            # Captura o resultado do clique antes que a rolagem virtualize
            # e remova os cards recém-carregados.
            await aguardar_conteudo(page, espera, carregando)
            apos_clique = await page.content()
            if len(apos_clique.encode()) > max_bytes:
                diagnostico["encerramento"] = "limite_conteudo"
                return
            snapshots.append(apos_clique)
            break

        # Botões de paginação precisam ser tratados separadamente: diferem de
        # "carregar mais", mas cada página ainda deve contribuir seus links.
        # Só avançamos quando não houve expansão de cards nesta rodada.
        if not clicou_expansao:
            proximos = page.get_by_role("button", name=BOTAO_PROXIMA_PAGINA)
            for indice in range(min(await proximos.count(), 3)):
                proximo = proximos.nth(indice)
                desabilitado = await proximo.get_attribute("aria-disabled")
                if (
                    not await proximo.is_visible()
                    or not await proximo.is_enabled()
                    or desabilitado == "true"
                ):
                    continue
                await proximo.click(timeout=1500)
                diagnostico["cliques_paginacao"] = diagnostico.get("cliques_paginacao", 0) + 1
                await aguardar_conteudo(page, espera, carregando)
                apos_paginacao = await page.content()
                if len(apos_paginacao.encode()) > max_bytes:
                    diagnostico["encerramento"] = "limite_conteudo"
                    return
                snapshots.append(apos_paginacao)
                break
        movimentos = await page.evaluate("""() => {
            // Avança por telas para não saltar cards de listas virtualizadas.
            const candidatos = Array.from(document.querySelectorAll('main, section, div, ul'))
              .filter(e => {
                const s = getComputedStyle(e);
                return /(auto|scroll)/.test(s.overflowY) && e.clientHeight > 80
                  && e.scrollHeight > e.clientHeight && e.getClientRects().length
                  && !e.closest('form, nav, [role="dialog"]');
              }).slice(0, 5);
            let movimentos = 0;
            for (const e of [...candidatos, document.scrollingElement]) {
                if (!e) continue;
                const antes = e.scrollTop;
                e.scrollTop = Math.min(e.scrollHeight - e.clientHeight,
                                       antes + Math.max(100, e.clientHeight * .8));
                if (e.scrollTop > antes) movimentos++;
            }
            return movimentos;
        }""")
        diagnostico["movimentos_rolagem"] = diagnostico.get("movimentos_rolagem", 0) + movimentos
        await aguardar_conteudo(page, espera, carregando)
        html = await page.content()
        if (
            len(html.encode()) > max_bytes
            or sum(len(s.encode()) for s in snapshots) > max_bytes * 3
        ):
            logger.info("Expansão interrompida pelo limite de conteúdo")
            diagnostico["encerramento"] = "limite_conteudo"
            return
        # A comparação usa conteúdo reconhecido, evitando timers e animações.
        seletor = HtmlResponse(page.url, body=html.encode(), encoding="utf-8")
        anterior = HtmlResponse(page.url, body=html_anterior.encode(), encoding="utf-8")

        def assinatura(r):
            return (
                tuple(c.url for c in AdaptadorGenericoHTML().descobrir(r)),
                extrair_job_postings_json_ld(r.text).vagas,
            )

        sem_mudanca = (
            sem_mudanca + 1 if assinatura(seletor) == assinatura(anterior) and not movimentos else 0
        )
        snapshots.append(html)
        if sem_mudanca >= 2:
            diagnostico["encerramento"] = "sem_novas_descobertas"
            return
    diagnostico["encerramento"] = "limite_rodadas" if rodadas else "somente_renderizacao"


def permite_recurso(url, origem, metodo, tipo, robots, agente):
    """O navegador recebe somente recursos de leitura do mesmo domínio autorizado."""
    destino, base = urlsplit(url), urlsplit(origem)
    destino_hostname = (destino.hostname or "").casefold()
    base_hostname = (base.hostname or "").casefold()
    mesma_origem = destino.netloc == base.netloc
    recurso_greenhouse = (
        base_hostname == "job-boards.greenhouse.io" and destino_hostname in _RECURSOS_GREENHOUSE
    )
    recurso_portal_senior = (
        base_hostname.endswith(".portaldetalentos.senior.com.br")
        and destino_hostname in _RECURSOS_PORTAL_SENIOR
    )
    consulta_publica_portal_senior = (
        recurso_portal_senior
        and metodo in {"OPTIONS", "POST"}
        and (
            destino.path.startswith(CAMINHO_CONSULTAS_PUBLICAS_PORTAL_SENIOR)
            or destino.path == CAMINHO_PERFIL_PUBLICO_PORTAL_SENIOR
        )
    )
    navegacao_interna_portal_senior = (
        base_hostname.endswith(".portaldetalentos.senior.com.br")
        and mesma_origem
        and metodo == "GET"
        and tipo == "document"
        and destino.path.rstrip("/") in {"", "/jobs"}
    )
    return (
        destino.scheme in {"http", "https"}
        and (mesma_origem or recurso_greenhouse or recurso_portal_senior)
        and not destino.username
        and (metodo == "GET" or consulta_publica_portal_senior)
        and (tipo in {"script", "stylesheet", "xhr", "fetch"} or navegacao_interna_portal_senior)
        and encontrar_restricao_dominio(destino.hostname or "") is None
        and not eh_conteudo_nao_empregaticio(url=url)
        and robots.can_fetch(agente, url)
    )


async def _renderizar(
    url,
    html,
    robots_txt,
    agente,
    timeout,
    espera,
    max_bytes,
    intervalo,
    rodadas=0,
    diagnostico=None,
):
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    robots = RobotFileParser()
    diagnostico = diagnostico if diagnostico is not None else {}
    diagnostico["dominios_externos_bloqueados"] = {}
    robots.parse(robots_txt.splitlines())
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=agente, service_workers="block")
            page = await context.new_page()
            consultas_pendentes = set()

            def iniciada(req):
                if req.resource_type in {"xhr", "fetch"}:
                    consultas_pendentes.add(req)

            page.on("request", iniciada)
            page.on("requestfinished", lambda req: consultas_pendentes.discard(req))
            page.on("requestfailed", lambda req: consultas_pendentes.discard(req))
            atendidos = 0
            inicial = False
            fila = asyncio.Lock()

            async def interceptar(route):
                nonlocal atendidos, inicial
                req = route.request
                if (
                    not inicial
                    and req.url == url
                    and req.is_navigation_request()
                    and req.frame == page.main_frame
                ):
                    inicial = True
                    await route.fulfill(
                        status=200, content_type="text/html; charset=utf-8", body=html
                    )
                    return
                if atendidos >= 60 or not permite_recurso(
                    req.url, url, req.method, req.resource_type, robots, agente
                ):
                    diagnostico["recursos_bloqueados"] = (
                        diagnostico.get("recursos_bloqueados", 0) + 1
                    )
                    destino = urlsplit(req.url).hostname
                    caminho_bloqueado = urlsplit(req.url).path
                    chave_recurso = f"{destino or 'sem_dominio'}{caminho_bloqueado}"
                    bloqueados = diagnostico.setdefault("recursos_bloqueados_urls", {})
                    bloqueados[chave_recurso] = bloqueados.get(chave_recurso, 0) + 1
                    if destino and destino != urlsplit(url).hostname:
                        dominios = diagnostico["dominios_externos_bloqueados"]
                        dominios[destino] = dominios.get(destino, 0) + 1
                    await route.abort()
                    return
                atendidos += 1
                diagnostico["recursos_permitidos"] = atendidos
                async with fila:
                    await asyncio.sleep(intervalo)
                    # Redirecionamentos não podem escapar da validação do domínio.
                    recurso = await route.fetch(max_redirects=0, timeout=timeout * 1000)
                    if 300 <= recurso.status < 400:
                        await route.abort()
                    else:
                        corpo = await recurso.body()
                        if len(corpo) > max_bytes:
                            await route.abort()
                        else:
                            await route.fulfill(response=recurso, body=corpo)
                    await recurso.dispose()

            await context.route("**/*", interceptar)
            # WebSockets não são necessários à leitura de vagas.
            await context.route_web_socket("**/*", lambda socket: socket.close())
            snapshots = []
            try:
                async with asyncio.timeout(timeout):
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    await aguardar_conteudo(page, espera, lambda: bool(consultas_pendentes))
                    snapshots.append(await page.content())
                    await expandir_listagem(
                        page,
                        snapshots,
                        espera,
                        min(max(rodadas, 0), 20),
                        max_bytes,
                        diagnostico,
                        lambda: bool(consultas_pendentes),
                    )
            except (TimeoutError, PlaywrightTimeoutError):
                if not snapshots:
                    raise
                logger.warning("Tempo de expansão esgotado; preservando conteúdo já renderizado")
                diagnostico["encerramento"] = "tempo_esgotado_parcial"
            return preservar_descobertas(url, snapshots, max_bytes)
        finally:
            await browser.close()


def renderizar_em_thread(*args):
    # O Scrapy usa Selector no Windows; subprocessos do Playwright exigem Proactor.
    fabrica = asyncio.ProactorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=fabrica) as runner:
        return runner.run(_renderizar(*args))


class RenderizacaoJavaScriptMiddleware:
    def __init__(self, crawler):
        self.crawler = crawler
        self.contagem = Counter()
        self.robots = {}
        self.semaforo = asyncio.Semaphore(2)

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool("JAVASCRIPT_ENABLED", False):
            raise NotConfigured
        if find_spec("playwright") is None:
            raise RuntimeError(
                "Instale .[javascript] e execute python -m playwright install chromium"
            )
        return cls(crawler)

    async def process_response(self, request, response):
        if (
            not isinstance(response, HtmlResponse)
            or not 200 <= response.status < 300
            or request.meta.get("observatorio_requisicao_autorizada") is not True
            # A renderização é reservada para a listagem. As páginas de detalhe
            # recebidas pelo Scrapy continuam sendo preservadas no bruto, mas
            # normalmente já trazem os dados no HTML. Abrir Chromium para cada
            # detalhe multiplica o tempo de uma coleta grande sem aumentar a
            # descoberta de vagas.
            or request.meta.get("observatorio_tipo_pagina") != "inicial"
        ):
            return response
        # A maioria das páginas já publica links, JSON-LD ou estado inicial
        # suficiente no HTML. Abrir Chromium nesses casos só consome tempo.
        # O navegador fica reservado a listagens que não revelaram nenhuma
        # vaga por leitura estática.
        fonte = request.cb_kwargs.get("fonte")
        try:
            adaptador = selecionar_adaptador(Fonte(fonte)) if fonte else AdaptadorGenericoHTML()
        except ValueError:
            adaptador = AdaptadorGenericoHTML()
        if adaptador.descobrir(response) or extrair_job_postings_json_ld(response.text).vagas:
            request.meta["observatorio_javascript"] = "dispensado_html_estatico"
            self.crawler.stats.inc_value("observatorio/javascript/dispensado_html_estatico")
            return response
        dominio = urlsplit(response.url).hostname
        if (
            dominio != request.meta.get("observatorio_dominio")
            or encontrar_restricao_dominio(dominio or "") is not None
            or request.meta.get("observatorio_status_politica")
            not in {"aprovada", "somente_coleta"}
        ):
            return response
        alvo = request.meta["observatorio_alvo_id"]
        settings = self.crawler.settings
        if self.contagem[alvo] >= settings.getint("JAVASCRIPT_MAX_PAGES_PER_TARGET", 20):
            self.crawler.stats.inc_value("observatorio/javascript/limite")
            request.meta["observatorio_javascript"] = "limite"
            return response
        self.contagem[alvo] += 1
        diagnostico = {}
        request.meta["observatorio_javascript_diagnostico"] = diagnostico
        try:
            async with self.semaforo:
                origem = urlsplit(response.url)
                robots_url = f"{origem.scheme}://{origem.netloc}/robots.txt"
                if robots_url not in self.robots:
                    pedido = Request(
                        robots_url, callback=NO_CALLBACK, meta={"dont_obey_robotstxt": True}
                    )
                    retorno = await maybe_deferred_to_future(self.crawler.engine.download(pedido))
                    if retorno.status in {404, 410}:
                        self.robots[robots_url] = "User-agent: *\nAllow: /"
                    elif retorno.status == 200:
                        self.robots[robots_url] = retorno.body.decode("utf-8", errors="replace")
                    else:
                        raise ValueError("robots.txt indisponível para recursos JavaScript")
                corpo = await asyncio.to_thread(
                    renderizar_em_thread,
                    response.url,
                    response.text,
                    self.robots[robots_url],
                    settings.get("USER_AGENT"),
                    settings.getint("JAVASCRIPT_TIMEOUT", 30),
                    settings.getfloat("JAVASCRIPT_WAIT_SECONDS", 2),
                    settings.getint("DOWNLOAD_MAXSIZE", 10 * 1024 * 1024),
                    max(0.1, settings.getfloat("DOWNLOAD_DELAY", 1)),
                    settings.getint("JAVASCRIPT_MAX_ROUNDS", 5)
                    if request.meta.get("observatorio_tipo_pagina") == "inicial"
                    else 0,
                    diagnostico,
                )
            headers = response.headers.copy()
            for nome in ("Content-Encoding", "Content-Length"):
                headers.pop(nome, None)
            headers["Content-Type"] = "text/html; charset=utf-8"
            request.meta["observatorio_javascript"] = "renderizado"
            self.crawler.stats.inc_value("observatorio/javascript/renderizadas")
            logger.info("JavaScript: alvo=%s diagnostico=%s", alvo, diagnostico)
            return response.replace(body=corpo, encoding="utf-8", headers=headers)
        except Exception as erro:
            request.meta["observatorio_javascript"] = "falha"
            self.crawler.stats.inc_value("observatorio/javascript/falhas")
            logger.warning("Renderização falhou: alvo=%s erro=%s; usando HTML original", alvo, erro)
            return response
