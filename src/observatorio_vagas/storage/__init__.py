"""Contratos independentes da tecnologia de armazenamento.

As implementações concretas do MongoDB pertencem ao subpacote
``observatorio_vagas.storage.mongodb``. Esta interface expõe somente os
tipos compartilhados pelo domínio e pelas implementações de persistência.
"""

from observatorio_vagas.storage.contracts import (
    RepositorioAnuncios,
    RepositorioColetas,
    RepositorioEmpresas,
    RepositorioPublicacoesEmpregos,
    RepositorioVagas,
    ResultadoEscrita,
    ResultadoReservaPublicacao,
)

__all__ = [
    "RepositorioAnuncios",
    "RepositorioColetas",
    "RepositorioEmpresas",
    "RepositorioPublicacoesEmpregos",
    "RepositorioVagas",
    "ResultadoEscrita",
    "ResultadoReservaPublicacao",
]
