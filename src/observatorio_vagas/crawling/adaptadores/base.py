"""Contrato compartilhado pelos adaptadores de descoberta."""

from typing import Protocol

from scrapy.http import Response

from observatorio_vagas.crawling.descoberta import LinkCandidatoVaga


class AdaptadorDescoberta(Protocol):
    """Descobre páginas de vagas sem executar novas requisições."""

    nome: str

    def descobrir(
        self,
        resposta: Response,
    ) -> tuple[LinkCandidatoVaga, ...]:
        """Retorna links encontrados no conteúdo já baixado."""

        ...
