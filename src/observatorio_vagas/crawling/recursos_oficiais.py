"""Exceções mínimas para recursos licenciados hospedados fora do portal CKAN."""

import re
from urllib.parse import urlsplit

from scrapy import Request

_RECURSO_ES = re.compile(
    r"/dataset/(?:c43d6653-634a-4e6e-bd66-c9ca3f6ee106|vagas-de-emprego)"
    r"/resource/(?P<id>[a-f0-9-]{36})/download/(?P<arquivo>[a-z0-9_-]+\.csv)"
)
_API_ABLER = re.compile(r"/api/company/v1/careers_pages/[a-z0-9_-]+/vacancies$")
_API_SMARTRECRUITERS = re.compile(r"/v1/companies/[A-Za-z0-9_-]+/postings$")


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
    if (
        request.meta.get("observatorio_dominio") == "jobs.smartrecruiters.com"
        and request.cb_kwargs.get("fonte") == "pagina_carreiras"
    ):
        return (
            origem.scheme == "https"
            and origem.netloc == "jobs.smartrecruiters.com"
            and destino.scheme == "https"
            and destino.netloc == "careers.smartrecruiters.com"
            and origem.path == destino.path
            and origem.query == destino.query
        )
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
    destino = urlsplit(request.url)
    if request.meta.get("observatorio_dominio") == "ats.abler.com.br":
        return (
            destino.scheme == "https"
            and destino.netloc == "hulk-smash.abler.com.br"
            and _API_ABLER.fullmatch(destino.path) is not None
        )
    return (
        request.meta.get("observatorio_dominio") == "jobs.smartrecruiters.com"
        and destino.scheme == "https"
        and destino.netloc == "api.smartrecruiters.com"
        and _API_SMARTRECRUITERS.fullmatch(destino.path) is not None
    )
