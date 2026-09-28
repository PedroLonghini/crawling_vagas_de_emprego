"""Seleção explícita do adaptador associado a cada tipo de fonte."""

from observatorio_vagas.crawling.adaptadores.base import AdaptadorDescoberta
from observatorio_vagas.crawling.adaptadores.ckan import AdaptadorCkan
from observatorio_vagas.crawling.adaptadores.empresa_direta import (
    AdaptadorEmpresaDireta,
)
from observatorio_vagas.crawling.adaptadores.generico import (
    AdaptadorGenericoHTML,
)
from observatorio_vagas.crawling.adaptadores.gupy import AdaptadorGupy
from observatorio_vagas.crawling.adaptadores.pandape import AdaptadorPandape
from observatorio_vagas.crawling.adaptadores.querido_diario import (
    AdaptadorQueridoDiario,
)
from observatorio_vagas.domain.enums import Fonte

ADAPTADOR_GENERICO = AdaptadorGenericoHTML()
ADAPTADOR_CKAN = AdaptadorCkan()
ADAPTADOR_EMPRESA_DIRETA = AdaptadorEmpresaDireta()
ADAPTADOR_GUPY = AdaptadorGupy()
ADAPTADOR_PANDAPE = AdaptadorPandape()
ADAPTADOR_QUERIDO_DIARIO = AdaptadorQueridoDiario()

ADAPTADORES_POR_FONTE: dict[Fonte, AdaptadorDescoberta] = {
    Fonte.CKAN: ADAPTADOR_CKAN,
    Fonte.PAGINA_CARREIRAS: ADAPTADOR_EMPRESA_DIRETA,
    Fonte.GUPY: ADAPTADOR_GUPY,
    Fonte.PANDAPE: ADAPTADOR_PANDAPE,
    Fonte.QUERIDO_DIARIO: ADAPTADOR_QUERIDO_DIARIO,
}


def selecionar_adaptador(
    fonte: Fonte,
) -> AdaptadorDescoberta:
    """Retorna a integração específica ou o adaptador HTML genérico."""

    if not isinstance(fonte, Fonte):
        raise TypeError("fonte precisa ser um valor do enum Fonte")

    return ADAPTADORES_POR_FONTE.get(
        fonte,
        ADAPTADOR_GENERICO,
    )
