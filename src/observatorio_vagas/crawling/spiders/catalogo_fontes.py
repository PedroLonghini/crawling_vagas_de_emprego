"""Spider que coleta páginas autorizadas pelo catálogo de fontes."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urldefrag, urlsplit

from scrapy import Request, Spider, signals
from scrapy.exceptions import DontCloseSpider, IgnoreRequest
from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.adaptadores import selecionar_adaptador
from observatorio_vagas.crawling.adaptadores.generico import descobrir_endpoints_json_publicos
from observatorio_vagas.crawling.adapters import converter_resposta_scrapy
from observatorio_vagas.crawling.catalog import (
    carregar_alvos_csv_tolerante,
)
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.estado_incremental import EstadoIncrementalLocal
from observatorio_vagas.crawling.filtro_conteudo import eh_conteudo_nao_empregaticio
from observatorio_vagas.crawling.janela_publicacao import (
    JanelaPublicacao,
    carregar_urls_conhecidas,
    chave_de_bloqueio,
    extrair_data_publicacao,
)
from observatorio_vagas.crawling.paginacao import descobrir_paginacao, eh_link_listagem
from observatorio_vagas.crawling.plataformas import (
    SITEMAP_NENHUM,
    SITEMAP_POR_LOCATARIO,
    filtrar_urls_do_locatario,
    hosts_companheiros_de,
    locatario_do_alvo,
    politica_sitemap,
)
from observatorio_vagas.crawling.request_factory import (
    criar_requisicao_inicial,
    criar_requisicoes_detalhe,
)
from observatorio_vagas.crawling.ritmo_sites import carregar_ritmo
from observatorio_vagas.crawling.sitemap import (
    criar_url_sitemap_padrao,
    descobrir_urls_sitemap,
    eh_resposta_sitemap,
    eh_url_sitemap,
)
from observatorio_vagas.crawling.urls import normalizar_url_vaga
from observatorio_vagas.domain.enums import Fonte
from observatorio_vagas.extraction.tor_project import eh_url_tor_project_vaga

# Evidências dos adaptadores que indicam uma página de LISTAGEM ou PAGINAÇÃO:
# ela continua sendo navegada (tipo ``inicial``) em vez de virar detalhe de vaga,
# que nunca descobre outras páginas. Um adaptador novo que agende uma listagem
# precisa registrar sua evidência aqui; o teste de contrato em
# tests/unit/crawling/test_plataformas.py acusa a omissão.
EVIDENCIAS_NAVEGACAO = frozenset(
    {
        "paginacao_json",
        "paginacao_querido_diario",
        "listagem_recurso_ckan",
        "listagem_solides_api",
        "paginacao_solides_api",
        "listagem_senior_api",
        "listagem_abler_api",
        "paginacao_abler_api",
        "paginacao_randstad",
        "listagem_smartrecruiters_api",
        "paginacao_smartrecruiters_api",
        "listagem_workday_cxs",
        "paginacao_workday_cxs",
        "portal_csod_bradesco",
        "portal_empregare_sicoob",
        "paginacao_empregare_sicoob",
        "paginacao_john_deere",
        "paginacao_caterpillar",
        "paginacao_bunge",
        "paginacao_tetra_pak",
        "paginacao_accor",
        "portal_basf_successfactors",
    }
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from scrapy.http import Response


# Páginas de listagem/sitemap por fonte quando há --limite-anuncios (mediana medida: 4,
# p90: 27, p95: 72 nas fontes com 20+ vagas). Fontes grandes podem subir com
# -a limite_navegacao=N.
LIMITE_NAVEGACAO_PADRAO = 100
# Fonte sem nenhum link de vaga depois de tantas páginas de navegação (ou sitemaps) para.
PAGINAS_SEM_VAGAS_PARA_ENCERRAR = 20
SITEMAPS_SEM_VAGAS_PARA_ENCERRAR = 3
# Detalhes de uma fonte em andamento ao mesmo tempo. Pedir tudo de uma vez fazia
# o corte por idade chegar tarde: na rodada de 05/10/2026, ~28% das páginas foram
# baixadas depois de a fonte já ter sido encerrada.
LOTE_DETALHES = 8
# Respostas 403/429 seguidas de um mesmo domínio que fazem o robô desistir dele
# nesta coleta (na rodada de 05/10/2026, 100% dos detalhes da BNE vieram 403 e
# seguraram o último bloco por 4 minutos).
BLOQUEIOS_SEGUIDOS_PARA_PARAR = 10
STATUS_DE_BLOQUEIO = frozenset({403, 429})


class CatalogoFontesSpider(Spider):
    """Coleta páginas iniciais e detalhes autorizados."""

    # Nome utilizado no terminal.
    name = "catalogo_fontes"

    custom_settings = {
        # Ativa a segunda barreira de política.
        "DOWNLOADER_MIDDLEWARES": {
            (
                "observatorio_vagas.crawling.janela_publicacao."
                "EncerramentoPorIdadeDownloaderMiddleware"
            ): 70,
            "observatorio_vagas.crawling.ritmo_sites.RitmoPorSiteDownloaderMiddleware": 60,
            ("observatorio_vagas.crawling.middlewares.BarreiraPoliticaDownloaderMiddleware"): 75,
            ("observatorio_vagas.crawling.javascript.RenderizacaoJavaScriptMiddleware"): 540,
        },
        # O orçamento global por alvo substitui o limite de um único salto.
        "DEPTH_LIMIT": 0,
        # O limite é por alvo. A execução direta não deve parar o catálogo
        # inteiro após apenas 100 respostas (o script de lote aplica sua trava).
        "CLOSESPIDER_PAGECOUNT": 0,
        # Ritmo do CSV (config/ritmo_sites.csv) mais as exceções fixas abaixo,
        # que têm prioridade. O dicionário do spider SUBSTITUI o do projeto,
        # por isso o CSV é mesclado aqui.
        "DOWNLOAD_SLOTS": {
            **carregar_ritmo().slots_scrapy(),
            "dados.es.gov.br": {"delay": 10, "concurrency": 1, "randomize_delay": False},
            "dados.ufvjm.edu.br": {"delay": 10, "concurrency": 1, "randomize_delay": False},
            "api.queridodiario.org.br": {"delay": 1.1, "concurrency": 1, "randomize_delay": False},
        },
        # Permite que respostas como 404, 429 e 500 cheguem
        # ao método parse depois das tentativas automáticas.
        #
        # Assim elas também poderão ser preservadas para auditoria.
        "HTTPERROR_ALLOW_ALL": True,
    }

    def __init__(
        self,
        catalogo: str = "config/catalogo_fontes.csv",
        limite_anuncios: str | int | None = None,
        relatorio_cobertura: str | None = None,
        estado_incremental: str | None = None,
        usar_cache_incremental: str | bool = True,
        reler_detalhes_conhecidos: str | bool = False,
        usar_agendamento_inteligente: str | bool = True,
        janela_horas: str | int | None = None,
        limite_navegacao: str | int | None = None,
        urls_conhecidas: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Carrega e valida o catálogo antes de iniciar o crawler."""

        # Executa a inicialização interna do Scrapy.
        super().__init__(
            *args,
            **kwargs,
        )
        self.limite_anuncios = int(limite_anuncios) if limite_anuncios is not None else None
        self.relatorio_cobertura = Path(relatorio_cobertura) if relatorio_cobertura else None
        self.usar_cache_incremental = str(usar_cache_incremental).casefold() not in {
            "0",
            "false",
            "nao",
            "não",
        }
        self.reler_detalhes_conhecidos = str(reler_detalhes_conhecidos).casefold() in {
            "1",
            "true",
            "sim",
        }
        self.usar_agendamento_inteligente = str(usar_agendamento_inteligente).casefold() not in {
            "0",
            "false",
            "nao",
            "não",
        }
        # Com janela, a fonte para quando as vagas lidas já passaram dela. Sem
        # data na página, ela para na primeira vaga já gravada no MongoDB e lê
        # só as primeiras listagens.
        horas = int(janela_horas) if janela_horas not in (None, "") else None
        if horas is not None and horas < 1:
            raise ValueError("janela_horas deve ser pelo menos 1")
        self.janela_publicacao = JanelaPublicacao(horas=horas)
        self.urls_conhecidas = carregar_urls_conhecidas(
            Path(urls_conhecidas) if urls_conhecidas else None
        )
        self.resultados_download: dict[str, dict[str, dict[str, Any]]] = {}
        self.detalhes_descobertos: dict[str, set[str]] = {}
        if self.limite_anuncios is not None and not 1 <= self.limite_anuncios <= 10000:
            raise ValueError("limite_anuncios deve estar entre 1 e 10000")

        if not isinstance(catalogo, str) or not catalogo.strip():
            raise ValueError("informe o caminho do catálogo de fontes")

        # Converte o texto recebido no terminal em caminho.
        self.caminho_catalogo = Path(catalogo.strip())
        caminho_estado_incremental = (
            Path(estado_incremental)
            if estado_incremental
            # Manter o estado ao lado do catálogo evita misturar fontes de
            # catálogos diferentes e torna cópias do projeto independentes.
            else self.caminho_catalogo.parent / ".cache" / "fontes_incrementais.json"
        )
        self.estado_incremental = (
            EstadoIncrementalLocal(caminho_estado_incremental)
            if self.usar_cache_incremental
            else None
        )

        # Uma linha inválida não impede as demais fontes de funcionar.
        resultado_catalogo = carregar_alvos_csv_tolerante(self.caminho_catalogo)

        self.alvos = resultado_catalogo.alvos
        self.falhas_catalogo = resultado_catalogo.falhas
        self.alvos_adiados: tuple[str, ...] = ()
        if self.estado_incremental is not None:
            if self.usar_agendamento_inteligente:
                self.alvos_adiados = tuple(
                    alvo.alvo_id
                    for alvo in self.alvos
                    if not self.estado_incremental.deve_coletar_hoje(alvo.alvo_id)
                )
                self.alvos = tuple(
                    alvo
                    for alvo in self.alvos
                    if self.estado_incremental.deve_coletar_hoje(alvo.alvo_id)
                )
            ordem_fila = {"rapida": 0, "normal": 1, "lenta": 2}
            self.alvos = tuple(
                sorted(
                    self.alvos,
                    key=lambda alvo: (
                        ordem_fila[self.estado_incremental.fila(alvo.alvo_id)],
                        *(-valor for valor in self.estado_incremental.prioridade(alvo.alvo_id)),
                    ),
                )
            )
        self.urls_agendadas: dict[str, set[str]] = {}
        self.urls_visitadas: dict[str, set[str]] = {}
        self.urls_listagem_agendadas: dict[str, set[str]] = {}
        self.detalhes_pendentes: dict[str, dict[str, None]] = {}
        self.navegacao_pendente: dict[str, dict[str, None]] = {}
        # Orçamento de listagens e sitemaps por fonte. O --limite-anuncios só limita
        # detalhes; sem isto cada fonte herdava 10.000 páginas de navegação (59% das
        # páginas do teste de 10 mil foram sitemaps).
        if limite_navegacao in (None, ""):
            self.limite_navegacao = LIMITE_NAVEGACAO_PADRAO if self.limite_anuncios else None
        else:
            self.limite_navegacao = int(limite_navegacao)
            if self.limite_navegacao < 1:
                raise ValueError("limite_navegacao deve ser pelo menos 1")
        self.candidatos_vistos: Counter[str] = Counter()
        self.paginas_navegacao: Counter[str] = Counter()
        self.sitemaps_vistos: Counter[str] = Counter()
        self.paginas_recebidas: Counter[str] = Counter()
        self.falhas_download: Counter[str] = Counter()
        self.candidatos_por_evidencia: dict[str, Counter[str]] = {}
        self.respostas_listagem: dict[str, Response] = {}
        self.detalhes_reaproveitados: Counter[str] = Counter()
        self.detalhes_novos: Counter[str] = Counter()
        self.detalhes_ja_gravados: Counter[str] = Counter()
        self.detalhes_em_voo: Counter[str] = Counter()
        self.bloqueios_seguidos: Counter[str] = Counter()
        self.dominios_bloqueados: set[str] = set()

        # O OffsiteMiddleware do Scrapy também bloqueará
        # domínios que não aparecem nesta lista.
        self.allowed_domains = sorted(
            {alvo.dominio for alvo in self.alvos if alvo.habilitado_para_coleta}
        )
        if "dados.es.gov.br" in self.allowed_domains:
            # Permite ao Scrapy consultar também o robots.txt do armazenamento.
            # A barreira continua exigindo o redirect do recurso SETADES exato.
            self.allowed_domains.append("one.s3.es.gov.br")
        # APIs e portais externos que as páginas de carreira consultam vêm do
        # registro de plataformas (Sólides, Abler, SmartRecruiters, CSOD...).
        self.allowed_domains.extend(
            host
            for host in hosts_companheiros_de(frozenset(self.allowed_domains))
            if host not in self.allowed_domains
        )

    def _url_inicial_do_alvo(self, alvo_id: str) -> str:
        return next((a.url_inicial for a in self.alvos if a.alvo_id == alvo_id), "")

    def _deve_pedir_sitemap_padrao(self, alvo_id: str, response: Response) -> bool:
        """Aplica a política de sitemap da plataforma do host (plataformas.toml)."""

        host = (urlsplit(response.url).hostname or "").casefold()
        politica = politica_sitemap(host)
        if politica == SITEMAP_NENHUM:
            return False
        if politica == SITEMAP_POR_LOCATARIO:
            # Em host compartilhado sem empresa identificável não há como
            # separar as vagas: melhor não baixar o sitemap da plataforma.
            return locatario_do_alvo(self._url_inicial_do_alvo(alvo_id)) is not None
        return True

    def _sitemap_do_proprio_locatario(self, alvo_id: str, response: Response) -> tuple[str, ...]:
        """Em plataformas multi-empresa, segue só as entradas da empresa do alvo."""

        urls = descobrir_urls_sitemap(response)
        host = (urlsplit(response.url).hostname or "").casefold()
        if politica_sitemap(host) != SITEMAP_POR_LOCATARIO:
            return urls
        locatario = locatario_do_alvo(self._url_inicial_do_alvo(alvo_id))
        if locatario is None:
            return ()
        mantidas = filtrar_urls_do_locatario(urls, host=host, locatario=locatario)
        self.logger.info(
            "Sitemap de plataforma compartilhada: alvo_id=%s locatario=%s mantidas=%s de %s",
            alvo_id,
            locatario,
            len(mantidas),
            len(urls),
        )
        return tuple(mantidas)

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.retomar_pendentes, signal=signals.spider_idle)
        return spider

    def retomar_pendentes(self) -> None:
        """Esvazia a fila quando a navegação termina, inclusive após erros."""
        retomadas = 0
        for alvo_id, pendentes in self.detalhes_pendentes.items():
            resposta = self.respostas_listagem.get(alvo_id)
            navegacao = self.navegacao_pendente.get(alvo_id, {})
            if resposta is None or not (pendentes or navegacao):
                continue
            if self.janela_publicacao.encerrada(alvo_id):
                continue
            listagens = self.urls_listagem_agendadas[alvo_id]
            limite = resposta.meta["observatorio_limite_paginas"]
            maximo = limite if self.limite_anuncios is not None else _maximo_listagens(limite)
            proximas = [u for u in navegacao if u not in self.urls_agendadas[alvo_id]]
            proximas = proximas[: max(0, maximo - len(listagens))]
            # Ociosidade: nada está em andamento; um contador que ficou alto (pedido
            # descartado sem errback) não pode travar a fonte.
            self.detalhes_em_voo[alvo_id] = 0
            requisicoes = self._criar_requisicoes(
                resposta=resposta,
                urls=(*proximas, *pendentes),
                urls_agendadas=self.urls_agendadas[alvo_id],
                navegacao=proximas,
                maximo_detalhes=LOTE_DETALHES,
            )
            for requisicao in requisicoes:
                requisicao.errback = self.tratar_falha_download
                if requisicao.url in navegacao:
                    requisicao.meta["observatorio_tipo_pagina"] = "inicial"
                    requisicao.meta["observatorio_lista_origem"] = navegacao.get(
                        requisicao.url
                    ) or navegacao.get(normalizar_url_vaga(requisicao.url))
                    listagens.add(requisicao.url)
                    navegacao.pop(requisicao.url, None)
                else:
                    self._marcar_origem_detalhe(requisicao, pendentes.pop(requisicao.url, None))
                    self.detalhes_em_voo[alvo_id] += 1
                self.crawler.engine.crawl(requisicao)
                retomadas += 1
        if retomadas:
            self.logger.info("Detalhes pendentes retomados: %s", retomadas)
            raise DontCloseSpider

    def _criar_requisicoes(
        self, *, resposta, urls, urls_agendadas, navegacao=(), maximo_detalhes=None
    ):
        """Aplica orçamentos independentes sem mudar as barreiras da fábrica.

        ``maximo_detalhes`` limita quantos detalhes são CRIADOS (o lote em andamento);
        URL recusada pela fábrica não ocupa vaga.
        """
        if self.limite_anuncios is None:
            if maximo_detalhes is not None:
                # O lote também vale sem orçamento separado: navegação passa, detalhes
                # só até o teto.
                navegacao_set = set(navegacao)
                detalhes = [url for url in urls if url not in navegacao_set][:maximo_detalhes]
                urls = [*(url for url in urls if url in navegacao_set), *detalhes]
            return criar_requisicoes_detalhe(
                resposta=resposta,
                urls=urls,
                callback=self.parse,
                urls_agendadas=urls_agendadas,
            )
        alvo_id = resposta.meta["observatorio_alvo_id"]
        listagens = self.urls_listagem_agendadas.get(alvo_id, set())
        limite_listagens = resposta.meta["observatorio_limite_paginas"]
        restantes_listagens = max(0, limite_listagens - len(listagens))
        restantes_detalhes = max(0, self.limite_anuncios - len(urls_agendadas - listagens))
        if maximo_detalhes is not None:
            restantes_detalhes = min(restantes_detalhes, maximo_detalhes)
        meta = dict(resposta.meta)
        meta["observatorio_limite_paginas"] = limite_listagens + self.limite_anuncios
        contexto = resposta.replace(request=resposta.request.replace(meta=meta))
        pedidos = []
        for url in dict.fromkeys(urls):
            if url in urls_agendadas:
                continue
            if url in navegacao:
                if restantes_listagens <= 0:
                    continue
            else:
                if restantes_detalhes <= 0:
                    continue
            novos = criar_requisicoes_detalhe(
                resposta=contexto,
                urls=(url,),
                callback=self.parse,
                urls_agendadas=urls_agendadas,
            )
            if novos:
                if url in navegacao:
                    restantes_listagens -= 1
                else:
                    restantes_detalhes -= 1
                pedidos.extend(novos)
        for pedido in pedidos:
            pedido.meta["observatorio_limite_paginas"] = limite_listagens
            self._aplicar_configuracao_download(pedido, alvo_id)
        return tuple(pedidos)

    async def start(
        self,
    ) -> AsyncIterator[Request]:
        """Produz requisições somente para alvos autorizados."""

        for alvo_id in self.alvos_adiados:
            self.logger.info(
                "Fonte adiada pelo agendamento inteligente: alvo_id=%s",
                alvo_id,
            )

        for falha in self.falhas_catalogo:
            self.logger.error(
                "Linha do catálogo ignorada: linha=%s alvo_id=%s motivo=%s",
                falha.numero_linha,
                falha.alvo_id,
                falha.mensagem,
            )

        for alvo in self.alvos:
            requisicao = criar_requisicao_inicial(
                alvo=alvo,
                callback=self.parse,
            )

            # None significa que a política ou situação operacional
            # impediu a criação da requisição.
            if requisicao is None:
                self.logger.info(
                    "Alvo ignorado: alvo_id=%s ativo=%s status=%s",
                    alvo.alvo_id,
                    alvo.ativa,
                    alvo.politica.status.value,
                )
                continue

            if self.limite_navegacao is not None:
                requisicao.meta["observatorio_limite_paginas"] = min(
                    requisicao.meta.get("observatorio_limite_paginas", self.limite_navegacao),
                    self.limite_navegacao,
                )
            self.urls_agendadas.setdefault(alvo.alvo_id, set()).add(requisicao.url)
            self.urls_listagem_agendadas.setdefault(alvo.alvo_id, set()).add(requisicao.url)
            if self.estado_incremental is not None:
                requisicao.headers.update(
                    self.estado_incremental.cabecalhos_condicionais(
                        alvo_id=alvo.alvo_id,
                        url=requisicao.url,
                    )
                )
                self._aplicar_configuracao_download(requisicao, alvo.alvo_id)
            requisicao.errback = self.tratar_falha_download
            yield requisicao

    def parse(
        self,
        response: Response,
        alvo_id: str,
        empresa_nome: str,
        fonte: str,
    ) -> Iterator[RespostaBruta | Request]:
        """Preserva a página e descobre detalhes quando permitido."""

        try:
            yield from self._processar_resposta(response, alvo_id, empresa_nome, fonte)
        finally:
            # Só depois de processar (e talvez esgotar a listagem) pede o próximo detalhe.
            if response.meta.get("observatorio_tipo_pagina") == "detalhe_vaga":
                self._liberar_vaga_de_detalhe(alvo_id)

    def _processar_resposta(
        self,
        response: Response,
        alvo_id: str,
        empresa_nome: str,
        fonte: str,
    ) -> Iterator[RespostaBruta | Request]:
        self.paginas_recebidas[alvo_id] += 1
        self._contar_bloqueio(response)
        self.resultados_download.setdefault(alvo_id, {})[response.request.url] = {
            "url_final": response.url,
            "status_http": response.status,
            "tipo_pagina": response.meta.get("observatorio_tipo_pagina"),
            "tentativas": response.meta.get("retry_times", 0) + 1,
            "javascript": response.meta.get("observatorio_javascript", "desativado"),
            "javascript_diagnostico": response.meta.get("observatorio_javascript_diagnostico", {}),
            "content_type": response.headers.get(b"Content-Type", b"")
            .decode("latin-1", errors="replace")
            .split(";", maxsplit=1)[0],
            "bytes": len(response.body),
        }
        self.urls_visitadas.setdefault(alvo_id, set()).add(urldefrag(response.url)[0])
        if self.estado_incremental is not None:
            self.estado_incremental.registrar_resposta(alvo_id=alvo_id, resposta=response)
        if (
            self.estado_incremental is not None
            and response.meta.get("observatorio_tipo_pagina") == "detalhe_vaga"
            and 200 <= response.status < 300
            and self.estado_incremental.registrar_detalhe_sucesso(
                alvo_id=alvo_id,
                url=normalizar_url_vaga(response.request.url),
            )
        ):
            self.detalhes_novos[alvo_id] += 1

        if eh_conteudo_nao_empregaticio(url=response.url, conteudo=response.body):
            self.resultados_download[alvo_id][response.request.url]["conteudo_ignorado"] = True
            self.logger.info(
                "Página ignorada por política global de conteúdo: alvo_id=%s url=%s",
                alvo_id,
                response.url,
            )
            return

        if (
            response.meta.get("observatorio_tipo_pagina") == "detalhe_vaga"
            and 200 <= response.status < 300
        ):
            lista = response.meta.get("observatorio_lista")
            esgotada_antes = self.janela_publicacao.lista_esgotada(lista)
            descartar = self.janela_publicacao.registrar(
                alvo_id,
                extrair_data_publicacao(response),
                conhecida=normalizar_url_vaga(response.request.url) in self.urls_conhecidas,
                lista=lista,
                posicao=response.meta.get("observatorio_posicao"),
            )
            if not esgotada_antes and self.janela_publicacao.lista_esgotada(lista):
                self._podar_lista_esgotada(alvo_id, lista)
            if descartar:
                # Vaga fora da janela ou já gravada não é guardada nem extraída.
                return

        # Toda resposta que chega ao callback é preservada primeiro.
        #
        # Isso vale para:
        # - página inicial;
        # - página de detalhe;
        # - resposta bem-sucedida;
        # - resposta de erro.
        yield converter_resposta_scrapy(
            resposta=response,
            fonte=Fonte(fonte),
            alvo_id=alvo_id,
            empresa_nome=empresa_nome,
        )

        tipo_pagina = response.meta.get("observatorio_tipo_pagina")

        # Se a listagem inicial não mudou, seus links também não mudaram.
        # Parar aqui evita baixar novamente cada detalhe já conhecido.
        if (
            tipo_pagina == "inicial"
            and self.estado_incremental is not None
            and self.estado_incremental.resposta_inalterada(
                alvo_id=alvo_id,
                url=response.request.url,
                resposta=response,
            )
        ):
            self.estado_incremental.confirmar_listagem_inalterada(alvo_id=alvo_id)
            self.logger.info(
                "Fonte inalterada: alvo_id=%s status=%s; "
                "detalhes reaproveitados da coleta anterior",
                alvo_id,
                response.status,
            )
            return

        # Páginas de detalhes são armazenadas, mas não podem
        # descobrir outras páginas.
        #
        # Isso impede uma navegação recursiva sem controle.
        if tipo_pagina != "inicial":
            return

        # Respostas de erro são preservadas para auditoria,
        # mas não são utilizadas para descobrir links.
        if not 200 <= response.status < 300:
            return

        self.respostas_listagem[alvo_id] = response
        if isinstance(response, HtmlResponse):
            self.resultados_download[alvo_id][response.request.url]["indicios_html"] = {
                "links": len(response.css("a[href]")),
                "scripts": len(
                    response.css("script[src], script:not([type='application/ld+json'])")
                ),
                "raiz_spa": bool(response.css("#__next, #app, #root, [data-reactroot]")),
            }

        # Uma página própria de carreiras também pode expor o sitemap padrão.
        # A URL é do mesmo domínio, continua sujeita ao robots.txt e compete
        # pelo mesmo limite de páginas do alvo.
        urls_sitemap: set[str] = set()
        if eh_resposta_sitemap(response):
            candidatos = ()
            paginacao: dict[str, None] = {}
            paginas_seguintes: set[str] = set()
            urls_sitemap.update(self._sitemap_do_proprio_locatario(alvo_id, response))
        else:
            # Analisa somente o HTML que já foi baixado.
            # Esta função não faz nenhuma nova requisição.
            adaptador = selecionar_adaptador(Fonte(fonte))
            candidatos = adaptador.descobrir(response)
            # Endpoints declarados em scripts só existem em HTML. Respostas
            # JSON (como a paginação do Querido Diário) não aceitam seletores
            # CSS e já são tratadas pelo adaptador especializado.
            endpoints_json = (
                descobrir_endpoints_json_publicos(response)
                if isinstance(response, HtmlResponse)
                else ()
            )
            if alvo_id == "tor_project_jobs":
                # A navegação do Tor mistura traduções e a página do conselho
                # com vagas reais. Só a URL canônica em inglês pode ser detalhe.
                candidatos = tuple(
                    candidato for candidato in candidatos if eh_url_tor_project_vaga(candidato.url)
                )
            evidencias = self.candidatos_por_evidencia.setdefault(alvo_id, Counter())
            for candidato in candidatos:
                evidencias.update(candidato.evidencias)
            paginacao = dict.fromkeys(descobrir_paginacao(response))
            # Só a paginação da MESMA listagem herda o esgotamento dela; categorias,
            # endpoints JSON e sitemap são outras listas e nunca são podados.
            paginas_seguintes = {normalizar_url_vaga(url) for url in paginacao}
            # Endpoints de listagem declarados pelo próprio site são tratados
            # como páginas iniciais: a resposta JSON poderá revelar detalhes
            # de vagas sem abrir o navegador.
            paginacao.update((url, None) for url in endpoints_json)
            paginacao.update(
                (candidato.url, None)
                for candidato in candidatos
                if set(candidato.evidencias) & EVIDENCIAS_NAVEGACAO
            )
            if (
                Fonte(fonte) is Fonte.PAGINA_CARREIRAS
                and response.meta.get("observatorio_numero_pagina") == 1
                and alvo_id != "tor_project_jobs"
                and self._deve_pedir_sitemap_padrao(alvo_id, response)
            ):
                urls_sitemap.add(criar_url_sitemap_padrao(response))
        if fonte == Fonte.QUERIDO_DIARIO.value:
            try:
                dados = json.loads(response.text)
                self.logger.info(
                    "Querido Diário: alvo_id=%s diarios_na_pagina=%s total_na_busca=%s",
                    alvo_id,
                    len(dados.get("gazettes", [])),
                    dados.get("total_gazettes"),
                )
            except (AttributeError, TypeError, ValueError):
                self.logger.warning("JSON incompatível: alvo_id=%s", alvo_id)

        if not eh_resposta_sitemap(response):
            self.logger.info(
                "Links candidatos: alvo_id=%s adaptador=%s encontrados=%s",
                alvo_id,
                adaptador.nome,
                len(candidatos),
            )
        else:
            self.logger.info(
                "Sitemap: alvo_id=%s urls_uteis_encontradas=%s",
                alvo_id,
                len(urls_sitemap),
            )

        agendadas = self.urls_agendadas.setdefault(alvo_id, {response.request.url})
        listagens_agendadas = self.urls_listagem_agendadas.setdefault(
            alvo_id,
            {response.request.url},
        )

        # Uma URL de sitemap continua sendo navegação; uma URL de vaga dentro
        # do sitemap entra na fila de detalhes. Isso permite avançar por
        # listagens sem perder cards descobertos nas páginas anteriores.
        urls_navegacao = list(paginacao)
        urls_detalhe = [candidato.url for candidato in candidatos if candidato.url not in paginacao]
        for url in urls_sitemap:
            (urls_navegacao if eh_url_sitemap(url) else urls_detalhe).append(url)

        if fonte not in {Fonte.CKAN.value, Fonte.QUERIDO_DIARIO.value}:
            for candidato in candidatos:
                if eh_link_listagem(candidato.url) and candidato.url not in urls_navegacao:
                    urls_navegacao.append(candidato.url)
                    if candidato.url in urls_detalhe:
                        urls_detalhe.remove(candidato.url)

        # Aliases de redirecionamento não consomem duas posições do orçamento.
        urls_navegacao = list(dict.fromkeys(normalizar_url_vaga(u) for u in urls_navegacao))
        urls_detalhe = list(dict.fromkeys(normalizar_url_vaga(u) for u in urls_detalhe))
        # Conta as vagas da página antes de tirar as já conhecidas: uma fonte cujas
        # vagas já estão todas gravadas não é 'fonte sem vagas'.
        vagas_na_pagina = len(urls_detalhe)
        if self.estado_incremental is not None:
            self.estado_incremental.registrar_urls_observadas(
                alvo_id=alvo_id,
                urls=tuple(urls_detalhe),
            )
        if self.estado_incremental is not None and not self.reler_detalhes_conhecidos:
            total_antes = len(urls_detalhe)
            urls_detalhe = [
                url
                for url in urls_detalhe
                if not self.estado_incremental.detalhe_conhecido(alvo_id=alvo_id, url=url)
            ]
            self.detalhes_reaproveitados[alvo_id] += total_antes - len(urls_detalhe)
        if self.urls_conhecidas:
            # Vaga já gravada no MongoDB não é nem pedida (antes era baixada e só
            # então descartada, encerrando a fonte inteira).
            total_antes = len(urls_detalhe)
            urls_detalhe = [url for url in urls_detalhe if url not in self.urls_conhecidas]
            self.detalhes_ja_gravados[alvo_id] += total_antes - len(urls_detalhe)
        self.detalhes_descobertos.setdefault(alvo_id, set()).update(urls_detalhe)
        if (
            not urls_detalhe
            and not urls_navegacao
            and isinstance(response, HtmlResponse)
            and response.css("script[src], #__next, #app, #root")
        ):
            self.logger.info(
                "Diagnóstico: alvo_id=%s sem links reconhecidos; página possui scripts. "
                "Verificar JavaScript ou adaptador; não confirma ausência de vagas.",
                alvo_id,
            )
        urls_navegacao = [url for url in urls_navegacao if url not in self.urls_visitadas[alvo_id]]
        urls_detalhe = [url for url in urls_detalhe if url not in self.urls_visitadas[alvo_id]]
        self._verificar_fonte_sem_vagas(
            alvo_id,
            candidatos_na_pagina=vagas_na_pagina + len(candidatos),
            e_sitemap=eh_resposta_sitemap(response),
        )

        navegacao_pendente = self.navegacao_pendente.setdefault(alvo_id, {})
        for url in urls_navegacao:
            if url not in agendadas:
                # Guarda de que listagem veio: se ela esgotar, a seguinte também.
                navegacao_pendente.setdefault(
                    url, response.url if url in paginas_seguintes else None
                )
        urls_navegacao = [url for url in navegacao_pendente if url not in agendadas]

        pendentes = self.detalhes_pendentes.setdefault(alvo_id, {})
        lista_ordenada = None if eh_resposta_sitemap(response) else response.url
        for posicao, url in enumerate(urls_detalhe):
            if url not in agendadas:
                # Posição na ordem da listagem (sitemap não tem ordem de data).
                pendentes.setdefault(url, (lista_ordenada, posicao))

        if fonte in {Fonte.CKAN.value, Fonte.QUERIDO_DIARIO.value}:
            # Adaptadores especializados mantêm a navegação própria: CKAN
            # pode expor vários CSVs e Querido Diário avança por offset.
            urls = [*urls_navegacao, *pendentes]
        else:
            maximo_listagens = (
                response.meta.get("observatorio_limite_paginas", 1)
                if self.limite_anuncios is not None
                else _maximo_listagens(response.meta.get("observatorio_limite_paginas", 1))
            )
            proximas_listagens = [
                url
                for url in urls_navegacao
                if url not in agendadas and url not in listagens_agendadas
            ]
            vagas_listagem_restantes = max(0, maximo_listagens - len(listagens_agendadas))
            # Com orçamento separado, páginas irmãs já expostas pelo site
            # podem ser colocadas na fila juntas. A concorrência por domínio
            # continua limitada nas configurações globais; só removemos a
            # espera artificial de terminar uma página antes de conhecer a
            # próxima. Sem orçamento separado mantemos uma por vez, pois o
            # limite legado é compartilhado com os detalhes.
            if vagas_listagem_restantes <= 0:
                proximas_listagens = []
            elif self.limite_anuncios is not None:
                proximas_listagens = proximas_listagens[:vagas_listagem_restantes]
            else:
                proximas_listagens = proximas_listagens[:1]

            detalhes = list(pendentes)
            if proximas_listagens:
                # Com orçamentos separados, os detalhes não precisam esperar
                # a paginação acabar (ou todas as outras fontes ficarem ociosas).
                # O downloader mantém os limites de concorrência e intervalo.
                if self.limite_anuncios is None:
                    detalhes = detalhes[:1]
                urls = [*proximas_listagens, *detalhes]
            else:
                urls = detalhes

        # A fábrica aplica:
        # - domínio;
        # - política;
        # - bloqueio do Empregos;
        # - deduplicação;
        # - limite de páginas.
        navegacao_origem = dict(navegacao_pendente)
        requisicoes = self._criar_requisicoes(
            resposta=response,
            urls=urls,
            urls_agendadas=agendadas,
            navegacao=urls_navegacao,
            maximo_detalhes=self._vagas_de_detalhe(alvo_id),
        )

        for requisicao in requisicoes:
            self._aplicar_configuracao_download(requisicao, alvo_id)
            requisicao.errback = self.tratar_falha_download
            if (
                requisicao.url in urls_navegacao
                or eh_url_sitemap(requisicao.url)
                or (
                    fonte not in {Fonte.CKAN.value, Fonte.QUERIDO_DIARIO.value}
                    and eh_link_listagem(requisicao.url)
                )
            ):
                # Listagens podem descobrir detalhes, mas não viram vagas pelo fallback HTML.
                requisicao.meta["observatorio_tipo_pagina"] = "inicial"
                listagens_agendadas.add(requisicao.url)
                navegacao_pendente.pop(requisicao.url, None)
            else:
                # Só removemos um detalhe da fila depois que a fábrica criou
                # a requisição autorizada para ele.
                self._marcar_origem_detalhe(requisicao, pendentes.pop(requisicao.url, None))
                self.detalhes_em_voo[alvo_id] += 1
            if requisicao.meta.get("observatorio_tipo_pagina") == "inicial":
                requisicao.meta["observatorio_lista_origem"] = navegacao_origem.get(
                    requisicao.url
                ) or navegacao_origem.get(normalizar_url_vaga(requisicao.url))

        self.logger.info(
            "Detalhes autorizados: alvo_id=%s agendados=%s",
            alvo_id,
            len(requisicoes),
        )
        self.logger.info(
            "Navegação: alvo_id=%s paginas_agendadas=%s limite=%s paginacao_encontrada=%s",
            alvo_id,
            len(agendadas),
            response.meta.get("observatorio_limite_paginas"),
            len(paginacao),
        )
        if not requisicoes:
            limite = response.meta.get("observatorio_limite_paginas", 1)
            motivo = (
                "limite_atingido"
                if len(agendadas) >= limite
                else ("sem_links_adicionais" if not urls else "links_repetidos_ou_fora_do_dominio")
            )
            self.logger.info("Fim da navegação: alvo_id=%s motivo=%s", alvo_id, motivo)

        # Entrega as requisições aprovadas ao Scrapy.
        yield from requisicoes

    def _contar_bloqueio(self, response: Response) -> None:
        """403/429 seguidos de um domínio: para de pedir a ele nesta coleta."""

        # URL final (depois de redirect): o redirect passa de novo pelo middleware,
        # que corta pela mesma chave.
        dominio = chave_de_bloqueio(response.url)
        if not dominio:
            return
        if response.status in STATUS_DE_BLOQUEIO:
            self.bloqueios_seguidos[dominio] += 1
            if (
                self.bloqueios_seguidos[dominio] >= BLOQUEIOS_SEGUIDOS_PARA_PARAR
                and dominio not in self.dominios_bloqueados
            ):
                self.dominios_bloqueados.add(dominio)
                self.logger.warning(
                    "Domínio parou de responder ao robô (%s respostas %s seguidas): %s; "
                    "nada mais será pedido a ele nesta coleta",
                    self.bloqueios_seguidos[dominio],
                    response.status,
                    dominio,
                )
        elif 200 <= response.status < 300:
            self.bloqueios_seguidos[dominio] = 0

    def _vagas_de_detalhe(self, alvo_id: str) -> int:
        return max(0, LOTE_DETALHES - self.detalhes_em_voo[alvo_id])

    def _proximos_detalhes(self, alvo_id: str) -> list[Request]:
        """Próximo lote de detalhes pendentes da fonte, na ordem da listagem."""

        resposta = self.respostas_listagem.get(alvo_id)
        pendentes = self.detalhes_pendentes.get(alvo_id)
        if resposta is None or not pendentes or self.janela_publicacao.encerrada(alvo_id):
            return []
        vagas = self._vagas_de_detalhe(alvo_id)
        if not vagas:
            return []
        requisicoes = self._criar_requisicoes(
            resposta=resposta,
            urls=list(pendentes),
            urls_agendadas=self.urls_agendadas.setdefault(alvo_id, set()),
            navegacao=(),
            maximo_detalhes=vagas,
        )
        for requisicao in requisicoes:
            requisicao.errback = self.tratar_falha_download
            self._marcar_origem_detalhe(requisicao, pendentes.pop(requisicao.url, None))
            self.detalhes_em_voo[alvo_id] += 1
        return list(requisicoes)

    def _liberar_vaga_de_detalhe(self, alvo_id: str) -> None:
        """Um detalhe terminou (com resposta, erro ou descarte): pede o próximo do lote."""

        self.detalhes_em_voo[alvo_id] = max(0, self.detalhes_em_voo[alvo_id] - 1)
        motor = getattr(getattr(self, "crawler", None), "engine", None)
        if motor is None:
            return
        for requisicao in self._proximos_detalhes(alvo_id):
            try:
                motor.crawl(requisicao)
            except RuntimeError:
                # O spider está fechando; não há mais o que agendar.
                return

    @staticmethod
    def _marcar_origem_detalhe(requisicao: Request, origem: object) -> None:
        """Leva para a requisição a listagem e a posição em que o detalhe apareceu."""

        if isinstance(origem, tuple) and len(origem) == 2:
            requisicao.meta["observatorio_lista"], requisicao.meta["observatorio_posicao"] = origem

    def _podar_lista_esgotada(self, alvo_id: str, lista: str) -> None:
        """Tira da fila os detalhes seguintes da listagem e as páginas que vieram dela."""

        limite = self.janela_publicacao.limite_da_lista(lista) or 0
        pendentes = self.detalhes_pendentes.get(alvo_id, {})
        for url, origem in list(pendentes.items()):
            if isinstance(origem, tuple) and origem[0] == lista and origem[1] >= limite:
                pendentes.pop(url, None)
        navegacao = self.navegacao_pendente.get(alvo_id, {})
        for url, origem in list(navegacao.items()):
            if origem == lista:
                navegacao.pop(url, None)
        self.logger.info(
            "Listagem esgotada (3 vagas velhas seguidas): alvo_id=%s lista=%s", alvo_id, lista
        )

    def _verificar_fonte_sem_vagas(
        self, alvo_id: str, *, candidatos_na_pagina: int, e_sitemap: bool
    ) -> None:
        """Para a fonte cujas primeiras páginas de navegação não mostram nenhuma vaga."""

        self.candidatos_vistos[alvo_id] += candidatos_na_pagina
        self.paginas_navegacao[alvo_id] += 1
        if e_sitemap:
            self.sitemaps_vistos[alvo_id] += 1
        if self.candidatos_vistos[alvo_id] or self.janela_publicacao.encerrada(alvo_id):
            return
        if (
            self.paginas_navegacao[alvo_id] >= PAGINAS_SEM_VAGAS_PARA_ENCERRAR
            or self.sitemaps_vistos[alvo_id] >= SITEMAPS_SEM_VAGAS_PARA_ENCERRAR
        ):
            self.janela_publicacao.encerrar(alvo_id)
            self.detalhes_pendentes.get(alvo_id, {}).clear()
            self.navegacao_pendente.get(alvo_id, {}).clear()
            self.logger.info(
                "Fonte encerrada sem nenhuma vaga nas primeiras %s páginas: alvo_id=%s",
                self.paginas_navegacao[alvo_id],
                alvo_id,
            )

    def closed(self, reason: str) -> None:
        """Distingue páginas realmente recebidas de URLs apenas agendadas."""
        if self.estado_incremental is not None:
            for alvo_id in self.urls_agendadas:
                respondeu_ok = any(
                    200 <= resultado.get("status_http", 0) < 300
                    for resultado in self.resultados_download.get(alvo_id, {}).values()
                )
                self.estado_incremental.registrar_execucao(
                    alvo_id=alvo_id,
                    detalhes_novos=self.detalhes_novos[alvo_id],
                    sem_candidatos=respondeu_ok and not self.candidatos_vistos[alvo_id],
                )
            self.estado_incremental.salvar()
        for alvo_id, urls in self.urls_agendadas.items():
            self.logger.info(
                "Resumo da fonte: alvo_id=%s agendadas=%s recebidas=%s falhas_download=%s "
                "pendentes_nao_agendados=%s limite=%s encerramento=%s",
                alvo_id,
                len(urls),
                self.paginas_recebidas[alvo_id],
                self.falhas_download[alvo_id],
                len(self.detalhes_pendentes.get(alvo_id, {})),
                next(alvo.limite_paginas for alvo in self.alvos if alvo.alvo_id == alvo_id),
                reason,
            )
        if self.relatorio_cobertura is not None:
            self.relatorio_cobertura.parent.mkdir(parents=True, exist_ok=True)
            self.relatorio_cobertura.write_text(
                json.dumps(self.resumir_cobertura(reason), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.logger.info("Cobertura salva em: %s", self.relatorio_cobertura)

    def resumir_cobertura(self, motivo: str) -> dict[str, Any]:
        """Conta candidatos e downloads; não confunde páginas com anúncios extraídos."""
        fontes = []
        for alvo_id, agendadas in self.urls_agendadas.items():
            resultados = self.resultados_download.get(alvo_id, {})
            descobertos = self.detalhes_descobertos.get(alvo_id, set())
            fontes.append(
                {
                    "alvo_id": alvo_id,
                    "fila": (
                        self.estado_incremental.fila(alvo_id)
                        if self.estado_incremental is not None
                        else "normal"
                    ),
                    "paginas_agendadas": len(agendadas),
                    "paginas_recebidas": self.paginas_recebidas[alvo_id],
                    "candidatos_unicos": len(descobertos),
                    "candidatos_por_evidencia": dict(
                        sorted(self.candidatos_por_evidencia.get(alvo_id, Counter()).items())
                    ),
                    "listagens_agendadas": len(self.urls_listagem_agendadas.get(alvo_id, set())),
                    "detalhes_agendados": len(
                        {
                            url
                            for url in agendadas
                            if url not in self.urls_listagem_agendadas.get(alvo_id, set())
                        }
                    ),
                    "detalhes_reaproveitados": self.detalhes_reaproveitados[alvo_id],
                    "possiveis_encerradas": (
                        self.estado_incremental.possiveis_encerradas(alvo_id=alvo_id)
                        if self.estado_incremental is not None
                        else []
                    ),
                    "detalhes_http_ok": sum(
                        200 <= resultados[u].get("status_http", 0) < 300
                        for u in descobertos
                        if u in resultados
                    ),
                    "candidatos_nao_agendados": sorted(descobertos - agendadas),
                    "detalhes_sem_resposta": sorted((descobertos & agendadas) - resultados.keys()),
                    "agendadas_sem_resposta": sorted(agendadas - resultados.keys()),
                    "erros": {
                        u: r
                        for u, r in resultados.items()
                        if not 200 <= r.get("status_http", 0) < 300
                    },
                    "resultados": resultados,
                    **_diagnosticar_fonte(
                        resultados=resultados,
                        candidatos=len(descobertos),
                        detalhes_http_ok=sum(
                            200 <= resultados[u].get("status_http", 0) < 300
                            for u in descobertos
                            if u in resultados
                        ),
                        candidatos_nao_agendados=len(descobertos - agendadas),
                    ),
                }
            )
        return {
            "encerramento": motivo,
            "fontes": fontes,
            "fontes_adiadas": list(self.alvos_adiados),
            "filas": {
                fila: sum(
                    1
                    for alvo in self.alvos
                    if self.estado_incremental is not None
                    and self.estado_incremental.fila(alvo.alvo_id) == fila
                )
                for fila in ("rapida", "normal", "lenta")
            },
            "ranking_fontes": self.estado_incremental.ranking()
            if self.estado_incremental is not None
            else [],
        }

    def tratar_falha_download(self, falha: Any) -> None:
        """Uma falha de rede/política fica visível, sem interromper outros alvos."""
        alvo_id = falha.request.meta.get("observatorio_alvo_id", "desconhecido")
        if falha.request.meta.get("observatorio_tipo_pagina") == "detalhe_vaga":
            self._liberar_vaga_de_detalhe(alvo_id)
        if isinstance(getattr(falha, "value", None), IgnoreRequest):
            # Descartado de propósito (listagem esgotada, fonte encerrada, política):
            # não conta como falha da fonte nem aciona o disjuntor.
            return
        if self.estado_incremental is not None:
            self.estado_incremental.registrar_falha(alvo_id=alvo_id)
        self.falhas_download[alvo_id] += 1
        self.resultados_download.setdefault(alvo_id, {})[falha.request.url] = {
            "erro": type(falha.value).__name__,
            "tentativas": falha.request.meta.get("retry_times", 0) + 1,
            "tipo_pagina": falha.request.meta.get("observatorio_tipo_pagina"),
        }
        self.logger.warning(
            "Download não concluído: alvo_id=%s url=%s erro=%s "
            "detalhe=%s tentativas=%s; continuando os demais alvos",
            alvo_id,
            falha.request.url.split("?", 1)[0],
            type(falha.value).__name__,
            str(falha.value).split("?", 1)[0][:300],
            falha.request.meta.get("retry_times", 0) + 1,
        )

    def _aplicar_configuracao_download(self, requisicao: Request, alvo_id: str) -> None:
        """Evita gastar várias tentativas em fontes historicamente instáveis."""

        if self.estado_incremental is None:
            return
        timeout, tentativas = self.estado_incremental.configuracao_download(alvo_id)
        requisicao.meta["download_timeout"] = timeout
        requisicao.meta["max_retry_times"] = tentativas


def _diagnosticar_fonte(
    *,
    resultados: dict[str, dict[str, Any]],
    candidatos: int,
    detalhes_http_ok: int,
    candidatos_nao_agendados: int,
) -> dict[str, str]:
    """Classifica a etapa que impediu uma fonte de produzir detalhes acessíveis."""

    paginas = tuple(resultados.values())
    listagens = tuple(p for p in paginas if p.get("tipo_pagina") == "inicial")
    listagens_ok = tuple(p for p in listagens if 200 <= p.get("status_http", 0) < 300)
    erros_http = tuple(p for p in paginas if p.get("status_http", 0) >= 400)
    erros_rede = tuple(p for p in paginas if p.get("erro"))

    if any(p.get("conteudo_ignorado") for p in paginas):
        return {
            "diagnostico": "conteudo_filtrado",
            "proxima_acao": "Revisar o motivo do filtro global antes de ajustar a fonte.",
        }

    if any(p.get("status_http") in {401, 403, 429} for p in listagens + erros_http):
        return {
            "diagnostico": "acesso_restrito_ou_rate_limit",
            "proxima_acao": (
                "Verificar acesso permitido, limites do site ou solicitar feed oficial."
            ),
        }

    if not listagens_ok:
        if erros_rede:
            return {
                "diagnostico": "falha_de_rede",
                "proxima_acao": (
                    "Verificar DNS, timeout e disponibilidade antes de repetir a coleta."
                ),
            }
        if erros_http:
            return {
                "diagnostico": "erro_http_na_listagem",
                "proxima_acao": (
                    "Verificar redirecionamento, URL inicial e status HTTP da listagem."
                ),
            }
        return {
            "diagnostico": "listagem_sem_resposta",
            "proxima_acao": (
                "Conferir se a fonte entrou na coleta e se o limite do lote foi atingido."
            ),
        }

    if candidatos == 0:
        sinais_js = any(
            pagina.get("javascript") in {"falha", "limite", "renderizado"}
            or pagina.get("indicios_html", {}).get("raiz_spa")
            or pagina.get("indicios_html", {}).get("scripts", 0) >= 3
            for pagina in listagens_ok
        )
        if sinais_js:
            return {
                "diagnostico": "possivel_javascript_ou_adaptador",
                "proxima_acao": (
                    "Testar a renderização seletiva; persistindo o zero, adaptar a plataforma."
                ),
            }
        return {
            "diagnostico": "nenhum_link_de_vaga_reconhecido",
            "proxima_acao": (
                "Inspecionar a resposta bruta por sitemap, JSON público ou padrão de links."
            ),
        }

    if detalhes_http_ok == 0:
        return {
            "diagnostico": "detalhes_com_falha_de_acesso",
            "proxima_acao": (
                "Inspecionar status e URLs dos detalhes; validar redirecionamento e acesso."
            ),
        }

    if candidatos_nao_agendados:
        return {
            "diagnostico": "cobertura_parcial_ou_limite",
            "proxima_acao": "Revisar o orçamento e os candidatos que ficaram sem agendamento.",
        }

    return {
        "diagnostico": "detalhes_baixados_revisar_extracao",
        "proxima_acao": "Conferir extração dos detalhes; HTTP 2xx não confirma anúncio extraído.",
    }


def _maximo_listagens(limite_paginas: object) -> int:
    """Reserva até metade do orçamento para revelar páginas posteriores."""

    if isinstance(limite_paginas, bool) or not isinstance(limite_paginas, int):
        return 1

    return min(10, max(1, (limite_paginas + 1) // 2))
