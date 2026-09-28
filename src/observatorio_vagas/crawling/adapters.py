"""Conversões entre objetos do Scrapy e contratos da aplicação."""

from __future__ import annotations

from datetime import datetime

from scrapy.http import Request, Response

from observatorio_vagas.crawling.contracts import RespostaBruta
from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.enums import Fonte, TipoPaginaColeta


def _decodificar_cabecalho(valor: bytes) -> str:
    """Transforma bytes de cabeçalho HTTP em texto."""

    # Latin-1 permite representar diretamente todos os bytes
    # que podem aparecer em cabeçalhos HTTP tradicionais.
    return valor.decode(
        "latin-1",
        errors="replace",
    )


def _ler_contexto_pagina(
    requisicao: Request | None,
) -> tuple[int, TipoPaginaColeta]:
    """Recupera o número e o tipo da página solicitada."""

    # Uma resposta pode existir sem uma Request associada.
    #
    # Nesse caso, consideramos que foi uma coleta avulsa.
    if requisicao is None:
        return 1, TipoPaginaColeta.AVULSA

    # A fábrica de requisições coloca essas informações
    # dentro dos metadados internos do Scrapy.
    numero_pagina = requisicao.meta.get(
        "observatorio_numero_pagina",
        1,
    )

    tipo_pagina_original = requisicao.meta.get(
        "observatorio_tipo_pagina",
        TipoPaginaColeta.AVULSA.value,
    )

    # bool é verificado separadamente porque True e False
    # também são considerados inteiros pelo Python.
    if isinstance(numero_pagina, bool) or not isinstance(numero_pagina, int) or numero_pagina < 1:
        raise ValueError(
            "metadado observatorio_numero_pagina inválido",
        )

    # Convertemos o texto recebido para uma opção conhecida.
    #
    # Se aparecer algo diferente de inicial, detalhe_vaga
    # ou avulsa, a coleta falhará de maneira clara.
    try:
        tipo_pagina = TipoPaginaColeta(tipo_pagina_original)
    except (TypeError, ValueError) as erro:
        raise ValueError(
            "metadado observatorio_tipo_pagina inválido",
        ) from erro

    return numero_pagina, tipo_pagina


def converter_resposta_scrapy(
    resposta: Response,
    fonte: Fonte,
    coletado_em: datetime | None = None,
    *,
    alvo_id: str | None = None,
    empresa_nome: str | None = None,
) -> RespostaBruta:
    """Transforma uma resposta do Scrapy em resposta bruta."""

    # A resposta pode possuir uma requisição associada.
    #
    # Quando existe redirecionamento:
    # - resposta.request.url é a URL solicitada;
    # - resposta.url é a URL final.
    try:
        requisicao = resposta.request
    except AttributeError:
        requisicao = None

    url_solicitada = resposta.url if requisicao is None else requisicao.url
    if requisicao is not None:
        origens = requisicao.meta.get("redirect_urls")
        if isinstance(origens, list) and origens and isinstance(origens[0], str):
            # A URL pública é estável; o destino pode ser um link assinado que expira.
            url_solicitada = origens[0]

    # Recuperamos o papel desta página dentro da coleta.
    numero_pagina, tipo_pagina = _ler_contexto_pagina(requisicao)

    # Preservamos todos os valores dos cabeçalhos.
    #
    # getlist é importante porque um cabeçalho pode aparecer
    # mais de uma vez na mesma resposta.
    cabecalhos: list[tuple[str, str]] = []

    for nome_bytes in resposta.headers:
        for valor_bytes in resposta.headers.getlist(nome_bytes):
            cabecalhos.append(
                (
                    _decodificar_cabecalho(nome_bytes),
                    _decodificar_cabecalho(valor_bytes),
                )
            )

    # Content-Type informa se recebemos HTML, JSON ou outro formato.
    tipo_conteudo_bytes = resposta.headers.get(b"Content-Type")

    tipo_conteudo = _decodificar_cabecalho(tipo_conteudo_bytes) if tipo_conteudo_bytes else None

    # HtmlResponse e TextResponse possuem encoding.
    #
    # Uma resposta binária comum pode não possuir esse atributo.
    codificacao = getattr(
        resposta,
        "encoding",
        None,
    )

    # Nos testes podemos fornecer uma data fixa.
    #
    # Durante uma coleta real, usamos o horário atual em UTC.
    instante_coleta = coletado_em if coletado_em is not None else agora_utc()

    return RespostaBruta(
        fonte=fonte,
        url_solicitada=url_solicitada,
        url_final=resposta.url,
        status_http=resposta.status,
        corpo=resposta.body,
        alvo_id=alvo_id,
        empresa_nome=empresa_nome,
        numero_pagina=numero_pagina,
        tipo_pagina=tipo_pagina,
        tipo_conteudo=tipo_conteudo,
        codificacao=codificacao,
        cabecalhos=tuple(cabecalhos),
        coletado_em=instante_coleta,
    )
