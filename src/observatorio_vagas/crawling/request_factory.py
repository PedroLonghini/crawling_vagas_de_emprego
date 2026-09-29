"""Criação segura das requisições utilizadas pelo crawler."""

import json
import re
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import Any
from urllib.parse import (
    parse_qsl,
    urldefrag,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from scrapy import Request
from scrapy.http import Response

from observatorio_vagas.crawling.catalog import AlvoColeta
from observatorio_vagas.crawling.filtro_conteudo import eh_conteudo_nao_empregaticio
from observatorio_vagas.crawling.urls import normalizar_url_vaga
from observatorio_vagas.domain.enums import Fonte, StatusPoliticaFonte
from observatorio_vagas.domain.politica_fonte import encontrar_restricao_dominio

# Representa uma função do spider que receberá a resposta.
CallbackScrapy = Callable[..., Any]


# Somente esses dois estados permitem coleta.
STATUS_COLETA_PERMITIDOS = frozenset(
    {
        StatusPoliticaFonte.APROVADA.value,
        StatusPoliticaFonte.SOMENTE_COLETA.value,
    }
)

DOMINIO_API_SOLIDES = "apigw.solides.com.br"
SUFIXO_PORTAL_SOLIDES = ".vagas.solides.com.br"
DOMINIO_SENIOR = "platform.senior.com.br"
DOMINIO_ABLER = "ats.abler.com.br"
DOMINIO_API_ABLER = "hulk-smash.abler.com.br"
DOMINIO_SMARTRECRUITERS = "jobs.smartrecruiters.com"
DOMINIO_API_SMARTRECRUITERS = "api.smartrecruiters.com"
DOMINIO_BRADESCO = "banco.bradesco"
DOMINIO_CSOD_BRADESCO = "bradesco.csod.com"
DOMINIO_SICOOB = "www.sicoob.com.br"
DOMINIO_EMPREGARE_SICOOB = "sicoob.empregare.com"
SUFIXO_WORKDAY = ".myworkdayjobs.com"
AGENTE_USUARIO_LULLY = "Mozilla/5.0 (compatible; ObservatorioVagas/1.0; +https://empregos.com.br)"


# Estas informações precisam ser copiadas para todas
# as páginas de detalhes.
CHAVES_CONTEXTO = (
    "observatorio_requisicao_autorizada",
    "observatorio_alvo_id",
    "observatorio_dominio",
    "observatorio_limite_paginas",
    "observatorio_status_politica",
    "observatorio_publicacao_autorizada_na_coleta",
    "observatorio_licenca_nome",
    "observatorio_licenca_url",
    "observatorio_atribuicao_obrigatoria",
)


def criar_requisicao_inicial(
    *,
    alvo: AlvoColeta,
    callback: CallbackScrapy,
    data_referencia: date | None = None,
) -> Request | None:
    """Cria uma requisição somente quando o alvo está autorizado."""

    if not isinstance(alvo, AlvoColeta):
        raise TypeError("alvo precisa ser um AlvoColeta")

    _validar_callback(callback)

    # Fontes desativadas, pendentes ou bloqueadas
    # não produzem nenhuma requisição.
    if not alvo.habilitado_para_coleta:
        return None

    url = _url_inicial_atualizada(
        alvo,
        data_referencia=data_referencia,
    )

    return Request(
        url=url,
        callback=callback,
        headers=_cabecalhos_especificos(url),
        # Dois alvos podem usar a mesma URL com contextos distintos.
        # A deduplicação segura ocorre dentro de cada alvo.
        dont_filter=True,
        # Estes valores serão entregues para o método parse.
        cb_kwargs={
            "alvo_id": alvo.alvo_id,
            "empresa_nome": alvo.empresa_nome,
            "fonte": alvo.fonte.value,
        },
        meta={
            # Marcador utilizado pela segunda barreira.
            "observatorio_requisicao_autorizada": True,
            # Identificação do alvo.
            "observatorio_alvo_id": alvo.alvo_id,
            # Único domínio permitido.
            "observatorio_dominio": alvo.dominio,
            # Quantidade máxima de páginas deste alvo.
            "observatorio_limite_paginas": (alvo.limite_paginas),
            # Situação jurídica e operacional da fonte.
            "observatorio_status_politica": (alvo.politica.status.value),
            # Situação da publicação no momento da coleta.
            "observatorio_publicacao_autorizada_na_coleta": (alvo.habilitado_para_publicacao),
            # Evidência que explica por que a republicação foi ou não liberada.
            "observatorio_licenca_nome": alvo.politica.licenca_nome,
            "observatorio_licenca_url": alvo.politica.licenca_url,
            "observatorio_atribuicao_obrigatoria": (alvo.politica.atribuicao_obrigatoria),
            # Esta é a primeira página da empresa.
            "observatorio_numero_pagina": 1,
            # Permite distinguir listagem e detalhe.
            "observatorio_tipo_pagina": "inicial",
        },
    )


def _url_inicial_atualizada(
    alvo: AlvoColeta,
    *,
    data_referencia: date | None,
) -> str:
    """Limita consultas do Querido Diário a uma janela móvel recente."""

    if alvo.fonte is not Fonte.QUERIDO_DIARIO:
        endereco = urlsplit(alvo.url_inicial)
        if endereco.hostname == DOMINIO_SMARTRECRUITERS:
            segmentos = [parte for parte in endereco.path.split("/") if parte]
            if len(segmentos) == 1 and segmentos[0].replace("-", "").replace("_", "").isalnum():
                empresa = segmentos[0]
                return (
                    f"https://{DOMINIO_API_SMARTRECRUITERS}/v1/companies/"
                    f"{empresa}/postings?limit=100&offset=0"
                )
        return alvo.url_inicial

    if data_referencia is not None and not isinstance(data_referencia, date):
        raise TypeError("data_referencia precisa ser uma data")

    endereco = urlsplit(alvo.url_inicial)
    parametros = parse_qsl(
        endereco.query,
        keep_blank_values=True,
    )

    if any(nome == "published_since" for nome, _ in parametros):
        return alvo.url_inicial

    inicio = (data_referencia or date.today()) - timedelta(days=7)
    parametros.append(("published_since", inicio.isoformat()))

    return urlunsplit(
        (
            endereco.scheme,
            endereco.netloc,
            endereco.path,
            urlencode(parametros),
            endereco.fragment,
        )
    )


def criar_requisicoes_detalhe(
    *,
    resposta: Response,
    urls: Iterable[str],
    callback: CallbackScrapy,
    urls_agendadas: set[str] | None = None,
) -> tuple[Request, ...]:
    """Cria páginas de detalhe respeitando domínio e limite do alvo."""

    if not isinstance(resposta, Response):
        raise TypeError("resposta precisa ser uma Response do Scrapy")

    # Uma string também é tecnicamente iterável em Python.
    #
    # Sem esta proteção, o programa poderia tentar usar cada
    # letra da URL como se fosse uma URL diferente.
    if isinstance(urls, (str, bytes)) or not isinstance(
        urls,
        Iterable,
    ):
        raise TypeError("urls precisa ser uma coleção de textos")

    _validar_callback(callback)

    try:
        # Recupera a requisição que originou esta resposta.
        requisicao_origem = resposta.request

    except AttributeError:
        # Sem a requisição original não conseguimos provar
        # que a resposta foi autorizada.
        return ()

    meta_origem = requisicao_origem.meta

    # A resposta precisa ter nascido na fábrica autorizada.
    if meta_origem.get("observatorio_requisicao_autorizada") is not True:
        return ()

    status = meta_origem.get("observatorio_status_politica")

    # Verificamos novamente a política.
    if status not in STATUS_COLETA_PERMITIDOS:
        return ()

    numero_pagina = meta_origem.get("observatorio_numero_pagina")

    # Listagens adicionais precisam do orçamento compartilhado pelo spider.
    # Detalhes continuam sem permissão para criar novas cadeias.
    if numero_pagina != 1 and not (
        urls_agendadas is not None and meta_origem.get("observatorio_tipo_pagina") == "inicial"
    ):
        return ()

    limite_paginas = meta_origem.get("observatorio_limite_paginas")

    # bool precisa ser verificado separadamente porque
    # Python também considera True e False como inteiros.
    if (
        isinstance(limite_paginas, bool)
        or not isinstance(limite_paginas, int)
        or limite_paginas < 1
    ):
        return ()

    dominio_esperado = meta_origem.get("observatorio_dominio")

    if not isinstance(dominio_esperado, str):
        return ()

    dominio_esperado = dominio_esperado.casefold()

    # Guardará somente URLs válidas e únicas.
    urls_validas: list[str] = []

    # O conjunto permite detectar rapidamente repetições.
    urls_encontradas = set(urls_agendadas or ())
    urls_encontradas.add(urldefrag(resposta.url)[0])

    for url in urls:
        if not isinstance(url, str):
            raise TypeError("cada URL de detalhe precisa ser um texto")

        texto = url.strip()

        if not texto:
            continue

        # Transforma links relativos como /vagas/123
        # em endereços completos.
        url_absoluta = urljoin(
            resposta.url,
            texto,
        )

        # /vagas/123#descricao e /vagas/123 são
        # consideradas a mesma página.
        url_sem_fragmento = normalizar_url_vaga(url_absoluta)

        endereco = urlsplit(url_sem_fragmento)

        dominio_url = (endereco.hostname or "").casefold()

        # Somente HTTP e HTTPS são permitidos.
        #
        # Isso elimina mailto:, javascript: e outros esquemas.
        if endereco.scheme not in {
            "http",
            "https",
        }:
            continue

        # Concursos públicos não fazem parte do produto. Bloquear já no link
        # evita gastar uma requisição somente para descartá-la no callback.
        if eh_conteudo_nao_empregaticio(url=url_sem_fragmento):
            continue

        # Uma página descoberta também passa pela lista central. Isso impede
        # que um link proibido contorne o catálogo por meio de outra página.
        if encontrar_restricao_dominio(dominio_url) is not None:
            continue

        # Links externos não podem utilizar a autorização concedida para o
        # domínio atual. A única exceção é a API pública que o próprio portal
        # Sólides carrega para listar vagas; ela nunca é usada para outros
        # domínios nem para candidatura.
        if dominio_url != dominio_esperado and not _eh_api_publica_companheira(
            dominio_origem=dominio_esperado,
            dominio_destino=dominio_url,
        ):
            continue

        if url_sem_fragmento in urls_encontradas:
            continue

        urls_encontradas.add(url_sem_fragmento)

        urls_validas.append(url_sem_fragmento)

    # A página inicial já consumiu uma posição.
    #
    # Exemplo:
    # limite_paginas = 3
    # 1 página inicial + 2 páginas de detalhes.
    quantidade_detalhes = max(
        limite_paginas - (len(urls_agendadas) if urls_agendadas is not None else 1),
        0,
    )

    # Copia apenas os nossos metadados.
    #
    # Não copiamos todas as informações internas do Scrapy.
    contexto = {chave: meta_origem[chave] for chave in CHAVES_CONTEXTO if chave in meta_origem}

    requisicoes: list[Request] = []

    for numero, url in enumerate(
        urls_validas[:quantidade_detalhes],
        start=(len(urls_agendadas) + 1 if urls_agendadas is not None else 2),
    ):
        meta = {
            **contexto,
            "observatorio_numero_pagina": numero,
            "observatorio_tipo_pagina": "detalhe_vaga",
        }

        metodo, corpo, cabecalhos = _configuracao_requisicao_especial(url)
        requisicoes.append(
            Request(
                url=url,
                method=metodo,
                body=corpo,
                headers=cabecalhos,
                callback=callback,
                # A mesma página pode pertencer a configurações distintas.
                dont_filter=True,
                # Mantém alvo_id, empresa_nome e fonte.
                cb_kwargs=dict(requisicao_origem.cb_kwargs),
                meta=meta,
            )
        )

        if urls_agendadas is not None:
            urls_agendadas.add(url)

    # Tupla evita que outra parte do programa altere
    # acidentalmente a coleção produzida.
    return tuple(requisicoes)


def _eh_api_publica_companheira(*, dominio_origem: str, dominio_destino: str) -> bool:
    """Permite somente as APIs públicas que abastecem a página autorizada."""

    return (
        dominio_destino == DOMINIO_API_SOLIDES and dominio_origem.endswith(SUFIXO_PORTAL_SOLIDES)
    ) or (dominio_origem == DOMINIO_ABLER and dominio_destino == DOMINIO_API_ABLER) or (
        dominio_origem == DOMINIO_SMARTRECRUITERS
        and dominio_destino == DOMINIO_API_SMARTRECRUITERS
    ) or (
        dominio_origem == DOMINIO_BRADESCO and dominio_destino == DOMINIO_CSOD_BRADESCO
    ) or (
        dominio_origem == DOMINIO_SICOOB and dominio_destino == DOMINIO_EMPREGARE_SICOOB
    )


def _cabecalhos_especificos(url: str) -> dict[str, str]:
    """Fornece o agente identificado exigido pelo WAF público da Lully."""

    dominio = (urlsplit(url).hostname or "").casefold()
    if dominio in {
        DOMINIO_API_SOLIDES,
        DOMINIO_SENIOR,
        DOMINIO_API_ABLER,
        DOMINIO_API_SMARTRECRUITERS,
    }:
        return {"Accept": "application/json, text/plain;q=0.9, */*;q=0.8"}
    if dominio.endswith(SUFIXO_WORKDAY):
        return {"Accept": "application/json, text/plain;q=0.9, */*;q=0.8"}
    if dominio in {"lullyhair.com.br", "www.lullyhair.com.br"}:
        return {"User-Agent": AGENTE_USUARIO_LULLY}
    return {}


def _configuracao_requisicao_especial(url: str) -> tuple[str, bytes | None, dict[str, str]]:
    """Cria POSTs apenas para as consultas públicas Senior e Workday."""

    endereco = urlsplit(url)
    parametros = dict(parse_qsl(endereco.query, keep_blank_values=True))
    cabecalhos = _cabecalhos_especificos(url)
    if (
        (endereco.hostname or "").casefold().endswith(SUFIXO_WORKDAY)
        and re.fullmatch(r"/wday/cxs/[^/]+/[^/]+/jobs", endereco.path)
    ):
        try:
            limite = int(parametros.get("limit", "20"))
            deslocamento = int(parametros.get("offset", "0"))
        except ValueError:
            return "GET", None, cabecalhos
        if limite < 1 or deslocamento < 0:
            return "GET", None, cabecalhos
        return (
            "POST",
            json.dumps(
                {
                    "appliedFacets": {},
                    "limit": limite,
                    "offset": deslocamento,
                    "searchText": "",
                }
            ).encode("utf-8"),
            {**cabecalhos, "Content-Type": "application/json"},
        )
    if endereco.hostname != DOMINIO_SENIOR:
        return "GET", None, cabecalhos
    tenant = parametros.get("tenant", "")
    tenant_domain = parametros.get("tenantdomain", "")
    if not tenant or not tenant_domain:
        return "GET", None, cabecalhos
    if parametros.get("observatorio_senior_listagem") == "1":
        dados = {
            "q": "",
            "hqId": "",
            "currentDate": date.today().isoformat(),
            "order": "HIGHLIGHT",
            "page": int(parametros.get("page", "0")),
            "size": 50,
        }
    elif parametros.get("observatorio_senior_detalhe") == "1" and parametros.get("vacancy_id"):
        dados = {"id": parametros["vacancy_id"], "currentDate": date.today().isoformat()}
    else:
        return "GET", None, cabecalhos
    return (
        "POST",
        json.dumps(dados).encode("utf-8"),
        {
            **cabecalhos,
            "Content-Type": "application/json",
            "x-tenant": tenant,
            "X-TenantDomain": tenant_domain,
        },
    )


def _validar_callback(
    callback: CallbackScrapy,
) -> None:
    """Garante que o Scrapy recebeu uma função de retorno."""

    if not callable(callback):
        raise TypeError("callback precisa ser uma função chamável")
