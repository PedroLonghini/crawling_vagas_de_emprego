"""Descoberta de navegação HTML explícita, sem inventar URLs de páginas."""

import re
from urllib.parse import parse_qsl, urldefrag, urlsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.descoberta import (
    _normalizar_texto,
    _possui_palavra_excluida,
)

_PADROES_URL_ONCLICK = (
    re.compile(
        r"(?:loadMore|load_more|fetch|axios\.(?:get|post)|goToPage|irParaPagina)"
        r"\s*\(\s*[\"'](?P<url>[^\"'<>\s]+)[\"']",
        flags=re.IGNORECASE,
    ),
)


def descobrir_paginacao(resposta: Response) -> tuple[str, ...]:
    """Reconhece próxima página e links dentro de controles de paginação."""
    if not isinstance(resposta, TextResponse):
        return ()
    tipo = resposta.headers.get(b"Content-Type", b"").lower()
    if tipo and b"html" not in tipo:
        return ()
    urls: dict[str, None] = {}
    for elemento in resposta.css("a[href], link[rel~=next][href]"):
        texto = _normalizar_texto(
            elemento.attrib.get("aria-label")
            or elemento.attrib.get("title")
            or elemento.xpath("string(.)").get()
            or ""
        ).strip()
        rel = elemento.attrib.get("rel", "").casefold().split()
        if elemento.xpath("ancestor-or-self::*[@disabled or @aria-disabled='true']"):
            continue
        controle = elemento.xpath(
            "ancestor-or-self::*[contains(@class, 'pagination') or contains(@class, 'paginacao') "
            "or contains(@class, 'pager') or contains(@class, 'page-numbers') "
            "or @role='navigation']"
        )
        texto_sem_setas = texto.strip(" »›>→")
        proxima = texto_sem_setas in {
            "proxima",
            "proximo",
            "proxima pagina",
            "next",
            "next page",
            "carregar mais",
            "carregar mais vagas",
            "load more",
            "mostrar mais",
            "ver mais vagas",
        } or (bool(controle) and texto in {"»", "›", ">", "→"})
        numerica = (texto_sem_setas.isdecimal() or _parece_rotulo_numerico(texto)) and bool(
            controle
        )
        if "next" not in rel and not proxima and not numerica:
            continue
        url = urldefrag(resposta.urljoin(elemento.attrib["href"]))[0]
        if _possui_palavra_excluida(_normalizar_texto(url), ""):
            continue
        if urlsplit(url).scheme in {"http", "https"} and url != urldefrag(resposta.url)[0]:
            urls[url] = None

    # Alguns portais antigos usam botões com uma URL literal em onclick.
    # Nunca executamos JavaScript nem transformamos um número em uma URL.
    for elemento in resposta.css("[onclick]"):
        onclick = elemento.attrib.get("onclick") or ""
        correspondencia = next(
            (
                resultado
                for padrao in _PADROES_URL_ONCLICK
                if (resultado := padrao.search(onclick)) is not None
            ),
            None,
        )
        if correspondencia is None:
            continue
        url = urldefrag(resposta.urljoin(correspondencia.group("url")))[0]
        if _possui_palavra_excluida(_normalizar_texto(url), ""):
            continue
        if urlsplit(url).scheme in {"http", "https"} and url != urldefrag(resposta.url)[0]:
            urls[url] = None

    # Alguns componentes JavaScript usam botões em vez de links. Só aceitamos
    # atributos que já contêm uma URL ou um caminho explícito; um valor como
    # ``data-page=2`` não é suficiente para inventar uma rota de paginação.
    atributos_paginacao = (
        "data-next-url",
        "data-next",
        "data-next-page-url",
        "data-page-url",
        "data-load-more-url",
        "data-pagination-url",
    )
    seletor = ", ".join(f"[{atributo}]" for atributo in atributos_paginacao)
    for elemento in resposta.css(seletor):
        if elemento.xpath("ancestor-or-self::*[@disabled or @aria-disabled='true']"):
            continue
        valor = next(
            (
                elemento.attrib.get(atributo)
                for atributo in atributos_paginacao
                if elemento.attrib.get(atributo)
            ),
            "",
        ).strip()
        if not valor.startswith(("/", "?", "http://", "https://")):
            continue

        url = urldefrag(resposta.urljoin(valor))[0]
        if _possui_palavra_excluida(_normalizar_texto(url), ""):
            continue
        if urlsplit(url).scheme in {"http", "https"} and url != urldefrag(resposta.url)[0]:
            urls[url] = None

    return tuple(urls)


def _parece_rotulo_numerico(texto: str) -> bool:
    """Reconhece ``Página 2`` só dentro de um controle de paginação."""

    partes = texto.split()
    return len(partes) >= 2 and partes[0] in {"pagina", "page"} and partes[1].isdecimal()


def eh_link_listagem(url: str) -> bool:
    """Reconhece uma página que pode continuar descobrindo vagas.

    Além da página principal ``/vagas``, muitos sites usam endereços como
    ``/vagas?page=2`` ou ``/jobs/search?cursor=...``. Eles precisam continuar
    como ``inicial`` no spider; do contrário seriam tratados como detalhes e
    sua próxima página nunca seria examinada.
    """

    partes = urlsplit(url)
    segmentos = [segmento.casefold() for segmento in partes.path.split("/") if segmento]
    ultimo_segmento = segmentos[-1] if segmentos else ""
    segmentos_listagem = {
        "vagas",
        "jobs",
        "carreiras",
        "careers",
        "oportunidades",
        "opportunities",
        "trabalhe-conosco",
        "positions",
        "openings",
    }
    segmentos_busca = {"buscar", "busca", "search", "results", "resultado", "resultados"}
    parametros = {nome.casefold() for nome, valor in parse_qsl(partes.query) if valor}
    parametros_paginacao = {"page", "pagina", "p", "offset", "cursor", "start"}

    if ultimo_segmento in segmentos_listagem:
        return True

    if (
        len(segmentos) >= 3
        and segmentos[-2] in {"page", "pagina"}
        and ultimo_segmento.isdecimal()
        and any(s in segmentos_listagem for s in segmentos[:-2])
    ):
        return True

    if ultimo_segmento in segmentos_busca and any(
        segmento in segmentos_listagem for segmento in segmentos
    ):
        return True

    return bool(parametros & parametros_paginacao) and any(
        segmento in segmentos_listagem for segmento in segmentos
    )
