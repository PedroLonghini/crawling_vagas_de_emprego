"""Pipelines executados para cada item produzido pelos spiders."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.crawling.raw_storage import (
    ArmazenamentoBrutoLocal,
)

if TYPE_CHECKING:
    from scrapy.crawler import Crawler


# Utilizamos o logger do próprio módulo.
#
# Assim, process_item não precisa mais receber o spider,
# argumento que foi descontinuado pelo Scrapy 2.18.
LOGGER = logging.getLogger(__name__)


class PipelineArmazenamentoBruto:
    """Salva respostas brutas antes das etapas de extração."""

    def __init__(
        self,
        diretorio_base: Path,
    ) -> None:
        """Prepara o armazenamento utilizado pelo pipeline."""

        self._armazenamento = ArmazenamentoBrutoLocal(
            diretorio_base=diretorio_base,
        )

    @classmethod
    def from_crawler(
        cls,
        crawler: Crawler,
    ) -> PipelineArmazenamentoBruto:
        """Cria o pipeline usando as configurações do Scrapy."""

        diretorio_configurado = crawler.settings.get(
            "RAW_STORAGE_DIRECTORY",
            "data/raw",
        )

        return cls(
            diretorio_base=Path(str(diretorio_configurado)),
        )

    def process_item(
        self,
        item: object,
    ) -> object:
        """Armazena respostas brutas e deixa outros itens seguirem."""

        # Outros tipos de item poderão existir no futuro.
        #
        # Este pipeline cuida somente de RespostaBruta.
        if not isinstance(item, RespostaBruta):
            return item

        resultado = self._armazenamento.salvar(
            resposta=item,
        )

        # DEBUG evita poluir o terminal quando milhões
        # de páginas forem armazenadas.
        LOGGER.debug(
            "Resposta bruta armazenada em %s",
            resultado.referencia,
        )

        return item
