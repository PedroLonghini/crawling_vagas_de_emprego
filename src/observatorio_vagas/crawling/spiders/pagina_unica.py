"""Spider mínimo para coletar uma única página informada pelo terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from scrapy import Request, Spider

from observatorio_vagas.crawling.adapters import converter_resposta_scrapy
from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.domain.enums import Fonte

# Estes imports são usados somente pelas anotações de tipo.
if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from scrapy.http import Response


class PaginaUnicaSpider(Spider):
    """Coleta uma página sem descobrir ou seguir outros endereços."""

    # Este é o nome que utilizaremos no terminal.
    name = "pagina_unica"

    # Mesmo que alguma configuração global seja alterada,
    # este spider continuará limitado a uma única página.
    custom_settings = {
        "CLOSESPIDER_PAGECOUNT": 1,
        "DEPTH_LIMIT": 1,
    }

    def __init__(
        self,
        url_inicial: str | None = None,
        fonte: str = Fonte.OUTRA.value,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Recebe e valida os argumentos informados no terminal."""

        # Permite que o Scrapy faça sua própria inicialização.
        super().__init__(
            *args,
            **kwargs,
        )

        # O spider não consegue trabalhar sem saber qual URL acessar.
        if url_inicial is None:
            raise ValueError("informe url_inicial ao executar o spider")

        # Remove espaços colocados acidentalmente antes ou depois da URL.
        url_normalizada = url_inicial.strip()

        # Divide a URL em partes como protocolo, domínio e caminho.
        endereco = urlsplit(url_normalizada)

        # Aceitamos somente endereços completos usando HTTP ou HTTPS.
        if endereco.scheme not in {"http", "https"} or not endereco.netloc:
            raise ValueError("url_inicial deve ser uma URL HTTP ou HTTPS completa")

        try:
            # Converte o texto informado no terminal para o enum Fonte.
            fonte_normalizada = Fonte(fonte)

        except ValueError as erro:
            # Se a fonte não existir, mostramos todas as opções permitidas.
            valores_permitidos = ", ".join(item.value for item in Fonte)

            raise ValueError(
                f"fonte inválida; use uma destas opções: {valores_permitidos}"
            ) from erro

        # Guardamos os valores já validados.
        self.url_inicial = url_normalizada
        self.fonte = fonte_normalizada

        # Limitamos este spider ao domínio recebido.
        #
        # Neste primeiro spider não seguimos links, mas a proteção
        # já fica preparada para futuras versões.
        if endereco.hostname is not None:
            self.allowed_domains = [
                endereco.hostname,
            ]

    async def start(
        self,
    ) -> AsyncIterator[Request]:
        """Produz a única requisição inicial desta execução."""

        # dont_filter=True garante que a URL inicial seja requisitada,
        # mesmo que apareça anteriormente na fila do Scrapy.
        yield Request(
            url=self.url_inicial,
            callback=self.parse,
            dont_filter=True,
        )

    def parse(
        self,
        response: Response,
    ) -> Iterator[RespostaBruta]:
        """Converte a resposta do Scrapy para o contrato da aplicação."""

        # O adaptador preserva:
        # - URL solicitada;
        # - URL final;
        # - status HTTP;
        # - corpo original;
        # - cabeçalhos;
        # - codificação;
        # - momento da coleta.
        #
        # Depois do yield, o objeto será enviado automaticamente
        # ao PipelineArmazenamentoBruto.
        yield converter_resposta_scrapy(
            resposta=response,
            fonte=self.fonte,
        )
