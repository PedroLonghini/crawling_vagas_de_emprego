"""Barreira defensiva aplicada antes de downloads do Scrapy."""

from typing import Never, Self
from urllib.parse import urlsplit

from scrapy import Request, Spider
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest
from scrapy.http.request import NO_CALLBACK
from scrapy.statscollectors import StatsCollector

from observatorio_vagas.crawling.recursos_oficiais import (
    permite_api_publica_companheira,
    permite_redirecionamento_recurso,
)
from observatorio_vagas.domain.enums import StatusPoliticaFonte
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio

# Somente estes dois estados permitem que o download continue.
STATUS_COLETA_PERMITIDOS = frozenset(
    {
        StatusPoliticaFonte.APROVADA.value,
        StatusPoliticaFonte.SOMENTE_COLETA.value,
    }
)


class BarreiraPoliticaDownloaderMiddleware:
    """Bloqueia requisições que não carregam autorização válida."""

    def __init__(
        self,
        stats: StatsCollector,
    ) -> None:
        """Recebe o coletor de estatísticas do Scrapy."""

        self._stats = stats

    @classmethod
    def from_crawler(
        cls,
        crawler: Crawler,
    ) -> Self:
        """Cria o middleware usando os recursos da execução atual."""

        return cls(stats=crawler.stats)

    def _bloquear(
        self,
        codigo: str,
        mensagem: str,
    ) -> Never:
        """Registra o motivo e interrompe a requisição."""

        # Cada tipo de bloqueio terá um contador separado.
        # Esses contadores poderão aparecer no dashboard.
        self._stats.inc_value(f"observatorio/politica/bloqueios/{codigo}")

        # IgnoreRequest é a forma oficial de informar ao Scrapy
        # que esta requisição não deve chegar ao downloader.
        raise IgnoreRequest(f"{codigo}: {mensagem}")

    def process_request(
        self,
        request: Request,
        spider: Spider | None = None,
    ) -> None:
        """Autoriza ou bloqueia a requisição antes do download."""

        # O spider não é necessário para tomar esta decisão.
        del spider

        endereco = urlsplit(request.url)
        dominio_url = (endereco.hostname or "").casefold()

        if endereco.scheme not in {"http", "https"}:
            self._bloquear(
                "esquema_invalido",
                "somente URLs HTTP ou HTTPS podem ser coletadas",
            )

        if not dominio_url:
            self._bloquear(
                "dominio_ausente",
                "a requisição não possui um domínio válido",
            )

        # A lista central prevalece mesmo quando os metadados de uma
        # requisição foram forjados ou configurados incorretamente.
        restricao = encontrar_restricao_dominio(dominio_url)

        if restricao is not None:
            self._bloquear(
                restricao.codigo,
                restricao.motivo,
            )

        # O próprio Scrapy cria esta requisição para respeitar robots.txt.
        #
        # Ela não nasce no spider e, por isso, não possui nosso marcador.
        if (
            request.meta.get("dont_obey_robotstxt") is True
            and endereco.path == "/robots.txt"
            and request.callback is NO_CALLBACK
        ):
            self._stats.inc_value("observatorio/politica/robots_interno")
            return None

        # Qualquer requisição comum precisa ter sido criada
        # pela nossa fábrica autorizada.
        if request.meta.get("observatorio_requisicao_autorizada") is not True:
            self._bloquear(
                "sem_autorizacao",
                "a requisição não foi criada pela fábrica autorizada",
            )

        status = request.meta.get("observatorio_status_politica")

        # Não confiamos somente no marcador.
        # Também verificamos novamente o status da política.
        if not isinstance(status, str) or status not in STATUS_COLETA_PERMITIDOS:
            self._bloquear(
                "status_nao_permitido",
                "a política atual não permite esta coleta",
            )

        dominio_esperado = request.meta.get("observatorio_dominio")

        # Esta proteção também bloqueia um redirecionamento
        # que tente levar a coleta para outro domínio.
        if not isinstance(dominio_esperado, str) or dominio_esperado != dominio_url:
            if permite_redirecionamento_recurso(request):
                self._stats.inc_value("observatorio/politica/recurso_oficial_redirecionado")
            elif permite_api_publica_companheira(request):
                self._stats.inc_value("observatorio/politica/api_publica_companheira")
            else:
                self._bloquear(
                    "dominio_divergente",
                    "o domínio da requisição difere do domínio autorizado",
                )

        # A requisição passou por todas as verificações.
        self._stats.inc_value("observatorio/politica/requisicoes_autorizadas")

        # Retornar None em um downloader middleware significa:
        # "não substitua a requisição e permita que ela continue".
        return None
