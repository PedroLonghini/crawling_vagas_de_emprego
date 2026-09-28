"""Adaptadores de descoberta HTML e de respostas de provedores licenciados."""

from observatorio_vagas.crawling.adaptadores.agregadores import (
    AdaptadorAdzunaApi,
    AdaptadorJoobleApi,
)
from observatorio_vagas.crawling.adaptadores.base import AdaptadorDescoberta
from observatorio_vagas.crawling.adaptadores.empresa_direta import (
    AdaptadorEmpresaDireta,
)
from observatorio_vagas.crawling.adaptadores.registro import selecionar_adaptador

__all__ = [
    "AdaptadorDescoberta",
    "AdaptadorAdzunaApi",
    "AdaptadorEmpresaDireta",
    "AdaptadorJoobleApi",
    "selecionar_adaptador",
]
