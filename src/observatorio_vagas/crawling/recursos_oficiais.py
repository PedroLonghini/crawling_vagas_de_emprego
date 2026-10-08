"""Exceções mínimas para recursos hospedados fora do domínio da fonte.

As regras das plataformas de vagas (APIs companheiras e redirecionamentos
oficiais) vêm do registro ``plataformas.toml``; aqui ficam só a barreira e a
exceção do CKAN do Espírito Santo.
"""

import re
from urllib.parse import urlsplit

from scrapy import Request

from observatorio_vagas.crawling.plataformas import (
    permite_companheiro,
    permite_redirecionamento,
)

_RECURSO_ES = re.compile(
    r"/dataset/(?:c43d6653-634a-4e6e-bd66-c9ca3f6ee106|vagas-de-emprego)"
    r"/resource/(?P<id>[a-f0-9-]{36})/download/(?P<arquivo>[a-z0-9_-]+\.csv)"
)


def permite_redirecionamento_recurso(request: Request) -> bool:
    """Autoriza só o mesmo CSV do conjunto SETADES no armazenamento oficial.

    Não autoriza links arbitrários, outro dataset, outro recurso nem uma cadeia
    passando por domínio desconhecido. robots.txt continua sendo aplicado.
    Evidência: o próprio recurso público retorna Location para este endereço.
    """
    origens = request.meta.get("redirect_urls")
    if not isinstance(origens, list) or not origens or not isinstance(origens[-1], str):
        return False
    origem, destino = urlsplit(origens[-1]), urlsplit(request.url)
    dominio_meta = request.meta.get("observatorio_dominio")
    if (
        isinstance(dominio_meta, str)
        and request.cb_kwargs.get("fonte") == "pagina_carreiras"
        and permite_redirecionamento(
            dominio_origem=dominio_meta,
            url_origem=origens[-1],
            url_destino=request.url,
        )
    ):
        return True
    if request.meta.get("observatorio_dominio") != "dados.es.gov.br":
        return False
    if request.cb_kwargs.get("fonte") != "ckan":
        return False
    if (
        origem.scheme != "https"
        or origem.netloc != "dados.es.gov.br"
        or destino.scheme != "https"
        or destino.netloc != "one.s3.es.gov.br"
    ):
        return False
    recurso = _RECURSO_ES.fullmatch(origem.path)
    return recurso is not None and destino.path == (
        f"/pr-ckan-store/resources/{recurso['id']}/{recurso['arquivo']}"
    )


def permite_api_publica_companheira(request: Request) -> bool:
    """Autoriza somente as listagens públicas vinculadas ao portal original.

    A exceção não concede permissão a outros endpoints da plataforma e exige
    que o alvo original seja a página de carreira autorizada.
    """

    if request.cb_kwargs.get("fonte") != "pagina_carreiras":
        return False
    dominio_meta = request.meta.get("observatorio_dominio")
    return isinstance(dominio_meta, str) and permite_companheiro(
        dominio_origem=dominio_meta, url=request.url
    )
