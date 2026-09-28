"""Descoberta conservadora para HTML e respostas JSON públicas."""

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.descoberta import (
    PALAVRAS_VAGA,
    LinkCandidatoVaga,
    descobrir_links_candidatos,
)
from observatorio_vagas.crawling.urls import normalizar_url_vaga
from observatorio_vagas.extraction.json_ld import extrair_job_postings_json_ld

# Aplicações modernas frequentemente deixam URLs de detalhe no estado inicial
# de JavaScript, mesmo quando os cards ainda não aparecem como links HTML.
# O padrão abaixo encontra somente valores entre aspas que são URLs absolutas
# ou caminhos absolutos; depois aplicamos as mesmas regras de domínio seguro.
PADRAO_URL_EM_SCRIPT = re.compile(
    r"[\"'](?P<url>https?://[^\"'<>\s]+|/[^\"'<>\s]+)[\"']",
    flags=re.IGNORECASE,
)

# Cards antigos podem navegar usando uma URL literal em ``onclick``. O padrão
# não interpreta JavaScript: aceita somente os formatos usuais de atribuição
# de localização e abertura de janela, seguidos de uma string HTTP ou caminho.
PADRAO_URL_EM_ONCLICK = re.compile(
    r"(?:window\.)?location(?:\.href)?\s*=\s*[\"'](?P<url>https?://[^\"'<>{}\s]+|/[^\"'<>{}\s]+)[\"']"
    r"|(?:window\.)?location\.(?:assign|replace)\(\s*[\"'](?P<localizacao>https?://[^\"'<>{}\s]+|/[^\"'<>{}\s]+)[\"']"
    r"|window\.open\(\s*[\"'](?P<janela>https?://[^\"'<>{}\s]+|/[^\"'<>{}\s]+)[\"']"
    r"|(?:router|navigate)\.(?:push|replace)\(\s*[\"'](?P<rota>https?://[^\"'<>{}\s]+|/[^\"'<>{}\s]+)[\"']",
    flags=re.IGNORECASE,
)

# Estados serializados por frameworks podem escapar pontuação de URL para
# evitar que o HTML seja interpretado como marcação. Decodificamos somente a
# pequena lista de caracteres que compõem URLs; não interpretamos JavaScript.
_ESCAPES_URL_SERIALIZADA = {
    "0026": "&",
    "002d": "-",
    "002f": "/",
    "003a": ":",
    "003d": "=",
    "003f": "?",
    "005f": "_",
}

SEGMENTOS_DETALHE_VAGA = frozenset(
    {
        "job",
        "jobs",
        "opening",
        "openings",
        "opportunity",
        "opportunities",
        "oportunidade",
        "oportunidades",
        "position",
        "positions",
        "vaga",
        "vagas",
    }
)

# Algumas organizações publicam vagas abaixo da mesma rota de carreira, por
# exemplo ``/trabalhe-conosco/nome-da-posicao/``. Nesses casos o título do
# cargo não necessariamente contém "vaga", "job" ou palavras equivalentes.
SEGMENTOS_LISTAGEM_CARREIRA = frozenset(
    {
        "carreira",
        "carreiras",
        "trabalhe-conosco",
        "trabalhe_conosco",
    }
)

_CHAVES_URL_DETALHE = frozenset(
    {
        "detailurl",
        "joburl",
        "postingurl",
        "url",
        "vacancyurl",
    }
)
_CHAVES_URL_PAGINACAO = frozenset(
    {
        "next",
        "nextpage",
        "nextpageurl",
        "nexturl",
    }
)
_CHAVES_TITULO_JSON = frozenset({"jobtitle", "name", "position", "title"})

# Endpoints públicos encontrados no estado inicial de uma página de carreira.
# Não tentamos montar URLs a partir de IDs nem chamamos APIs externas: a URL
# precisa estar literalmente publicada no HTML e continuar no mesmo domínio.
_PADRAO_ENDPOINT_JSON_PUBLICO = re.compile(
    r"[\"'](?P<url>https?://[^\"'< >]+|/[^\"'< >]+)[\"']",
    flags=re.IGNORECASE,
)
_SINAL_ENDPOINT_VAGAS = re.compile(r"\b(job|jobs|vaga|vagas|vacanc|career|careers|opening)\b", re.I)


@dataclass(frozen=True, slots=True)
class AdaptadorGenericoHTML:
    """Aplica as regras conservadoras de palavras e links HTML."""

    nome: str = "geral_html"

    def descobrir(
        self,
        resposta: Response,
    ) -> tuple[LinkCandidatoVaga, ...]:
        """Une links HTML, JSON-LD e URLs de respostas JSON públicas."""

        if not isinstance(resposta, Response):
            raise TypeError("resposta precisa ser uma Response do Scrapy")

        if not isinstance(resposta, TextResponse):
            return ()

        encontrados: list[LinkCandidatoVaga] = []
        urls_encontradas: set[str] = set()

        # Algumas fontes documentam uma API pública no próprio catálogo. A
        # resposta já recebida pode listar as URLs de detalhes ou a próxima
        # página. Lemos somente URLs literais do mesmo domínio e nunca
        # inferimos rotas a partir de IDs ou números de página.
        candidatos_json = _descobrir_urls_em_resposta_json(resposta)
        for candidato in candidatos_json:
            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        # Seletores CSS não se aplicam a um JsonResponse. Nesta modalidade as
        # URLs explicitamente declaradas acima já são a única fonte segura de
        # descoberta para o adaptador genérico.
        tipo = resposta.headers.get(b"Content-Type", b"").decode("latin-1", errors="replace")
        if "json" in tipo.casefold():
            return _normalizar_candidatos(encontrados)

        # Algumas páginas de carreira não possuem cards HTML completos, mas
        # expõem uma lista de JobPosting no JSON-LD. Essas URLs representam
        # detalhes públicos e podem ser visitadas pelo mesmo fluxo seguro.
        resultado_json_ld = extrair_job_postings_json_ld(resposta.text)

        for vaga in resultado_json_ld.vagas:
            url = _url_de_detalhe_json_ld(resposta, vaga.get("url"))

            if url is None or url in urls_encontradas:
                continue

            titulo = vaga.get("title") or vaga.get("name") or "Vaga publicada em JSON-LD"
            texto = (
                " ".join(titulo.split()) if isinstance(titulo, str) else "Vaga publicada em JSON-LD"
            )
            urls_encontradas.add(url)
            encontrados.append(
                LinkCandidatoVaga(
                    url=url,
                    texto=texto,
                    evidencias=("job_posting_json_ld",),
                )
            )

        # Além do JSON-LD, páginas React, Vue e Next.js podem conter uma lista
        # de URLs no estado serializado da página. Não interpretamos nem
        # executamos JavaScript: apenas lemos o texto já recebido pelo crawler.
        for candidato in _descobrir_urls_de_detalhe_em_scripts(resposta):
            if candidato.url in urls_encontradas:
                continue

            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        # Alguns cards de interfaces JavaScript não são links HTML. A navegação
        # acontece ao clicar em um elemento com ``data-url`` ou ``data-href``.
        # Ler esses atributos públicos não executa JavaScript e permite seguir
        # a mesma validação conservadora de domínio e de indícios de vaga.
        for candidato in _descobrir_urls_de_detalhe_em_data_atributos(resposta):
            if candidato.url in urls_encontradas:
                continue

            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        for candidato in _descobrir_urls_de_detalhe_em_onclick(resposta):
            if candidato.url in urls_encontradas:
                continue

            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        # Em páginas de carreira, uma rota filha da própria listagem é um
        # indício estrutural suficiente para uma oportunidade. Isso cobre
        # páginas que chamam vagas de "processos seletivos" privados, sem
        # liberar rotas externas ou páginas genéricas do domínio.
        for candidato in _descobrir_urls_filhos_da_pagina_de_carreiras(resposta):
            if candidato.url in urls_encontradas:
                continue

            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        for candidato in descobrir_links_candidatos(resposta):
            if candidato.url in urls_encontradas:
                continue

            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        # Um card com JobPosting explícito pode usar /123 sem palavra "vaga".
        for card in resposta.css('[itemtype$="/JobPosting"]'):
            for link in card.css('a[itemprop="url"][href]'):
                url = _url_de_detalhe_json_ld(resposta, link.attrib.get("href"))
                if url:
                    encontrados.append(
                        LinkCandidatoVaga(
                            url=url,
                            texto=" ".join((link.xpath("string(.)").get() or "").split()),
                            evidencias=("card_job_posting",),
                        )
                    )

        return _normalizar_candidatos(encontrados)


def descobrir_endpoints_json_publicos(resposta: Response) -> tuple[str, ...]:
    """Encontra listagens JSON declaradas pela própria página de carreira.

    O resultado é deliberadamente pequeno e conservador. Ele só permite GET
    para o mesmo domínio e exige sinais de vagas tanto no script quanto no
    caminho do endpoint. Assim, não transforma telemetria, autenticação ou
    APIs administrativas em tráfego do crawler.
    """

    if not isinstance(resposta, TextResponse):
        return ()

    encontrados: dict[str, None] = {}
    for script in resposta.css("script::text").getall():
        conteudo = _desescapar_url_serializada(script)
        if not _SINAL_ENDPOINT_VAGAS.search(conteudo):
            continue
        for correspondencia in _PADRAO_ENDPOINT_JSON_PUBLICO.finditer(conteudo):
            url = _url_de_detalhe_json_ld(resposta, correspondencia.group("url"))
            if url is None:
                continue
            caminho = urlsplit(url).path.casefold()
            if "/api/" not in caminho or not _SINAL_ENDPOINT_VAGAS.search(caminho):
                continue
            encontrados.setdefault(url, None)
    return tuple(encontrados)


def _normalizar_candidatos(
    candidatos: list[LinkCandidatoVaga],
) -> tuple[LinkCandidatoVaga, ...]:
    """Remove aliases e destinos administrativos em um ponto único."""

    unicos = {}
    for candidato in candidatos:
        url = normalizar_url_vaga(candidato.url)
        caminho = urlsplit(url).path.casefold().rstrip("/")
        # Formulários e compartilhamentos não são detalhes de vagas.
        if any(x in caminho for x in ("/sharer", "/formulario-curriculo", "/intent/tweet")):
            continue
        if caminho.endswith(("/apply", "/login", "/signin")):
            continue
        unicos.setdefault(url, LinkCandidatoVaga(url, candidato.texto, candidato.evidencias))
    return tuple(unicos.values())


def _url_de_detalhe_json_ld(
    resposta: Response,
    valor: object,
) -> str | None:
    """Aceita somente URL HTTP do mesmo domínio da página atual."""

    if not isinstance(valor, str) or not valor.strip():
        return None

    url, _ = urldefrag(urljoin(resposta.url, valor.strip()))
    endereco = urlsplit(url)
    origem = urlsplit(resposta.url)

    if endereco.scheme not in {"http", "https"}:
        return None

    if (endereco.hostname or "").casefold() != (origem.hostname or "").casefold():
        return None

    return None if url == urldefrag(resposta.url)[0] else url


def _descobrir_urls_em_resposta_json(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Lê listagens JSON explicitamente recebidas pelo crawler.

    A função não tenta adivinhar a estrutura do provedor: aceita apenas
    chaves convencionais de URL, um título como evidência complementar e a
    página seguinte declarada pelo próprio JSON.
    """

    tipo = resposta.headers.get(b"Content-Type", b"").decode("latin-1", errors="replace")
    if "json" not in tipo.casefold():
        return ()

    try:
        dados = json.loads(resposta.text)
    except (TypeError, ValueError):
        return ()

    detalhes: dict[str, LinkCandidatoVaga] = {}
    paginacao: dict[str, LinkCandidatoVaga] = {}
    for objeto in _objetos_json(dados):
        chaves = {_normalizar_chave_json(chave): valor for chave, valor in objeto.items()}
        titulo = next(
            (
                valor
                for chave, valor in chaves.items()
                if chave in _CHAVES_TITULO_JSON and isinstance(valor, str) and valor.strip()
            ),
            "",
        )
        texto = " ".join(titulo.split()) if isinstance(titulo, str) else ""
        for chave, valor in chaves.items():
            if chave not in _CHAVES_URL_DETALHE | _CHAVES_URL_PAGINACAO:
                continue
            url = _url_de_detalhe_json_ld(resposta, valor)
            if url is None:
                continue
            if chave in _CHAVES_URL_PAGINACAO:
                paginacao.setdefault(
                    url,
                    LinkCandidatoVaga(url, "Próxima página declarada em JSON", ("paginacao_json",)),
                )
                continue
            if not _parece_url_de_detalhe_de_vaga(url) and not any(
                palavra in texto.casefold() for palavra in PALAVRAS_VAGA
            ):
                continue
            detalhes.setdefault(
                url,
                LinkCandidatoVaga(
                    url,
                    texto or "Vaga descoberta em resposta JSON pública",
                    ("url_de_detalhe_em_json",),
                ),
            )
    # Detalhes primeiro permite ao spider registrar os cards antes da próxima
    # página, mantendo o relatório previsível mesmo que a ordem das chaves no
    # JSON varie entre provedores.
    return tuple((*detalhes.values(), *paginacao.values()))


def _objetos_json(valor: object) -> Iterator[Mapping[str, object]]:
    """Percorre somente objetos e listas JSON, sem executar conteúdo recebido."""

    if isinstance(valor, Mapping):
        yield valor
        for filho in valor.values():
            yield from _objetos_json(filho)
    elif isinstance(valor, list):
        for filho in valor:
            yield from _objetos_json(filho)


def _normalizar_chave_json(chave: object) -> str:
    """Compara ``detail_url`` e ``detailUrl`` sem alterar os dados originais."""

    return re.sub(r"[^a-z0-9]", "", chave.casefold()) if isinstance(chave, str) else ""


def _descobrir_urls_de_detalhe_em_scripts(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Localiza URLs de detalhes de vaga no estado serializado da página."""

    encontrados: list[LinkCandidatoVaga] = []
    urls_encontradas: set[str] = set()

    for script in resposta.css("script::text").getall():
        conteudo = _desescapar_url_serializada(script)

        for correspondencia in PADRAO_URL_EM_SCRIPT.finditer(conteudo):
            url = _url_de_detalhe_json_ld(
                resposta,
                correspondencia.group("url"),
            )

            if url is None or url in urls_encontradas:
                continue

            if not _parece_url_de_detalhe_de_vaga(url):
                continue

            urls_encontradas.add(url)
            encontrados.append(
                LinkCandidatoVaga(
                    url=url,
                    texto="Vaga descoberta no estado público da página",
                    evidencias=("url_de_detalhe_em_script",),
                )
            )

    return tuple(encontrados)


def _descobrir_urls_filhos_da_pagina_de_carreiras(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Descobre detalhes publicados abaixo de uma rota explícita de carreira."""

    segmentos_origem = tuple(
        segmento.casefold() for segmento in urlsplit(resposta.url).path.split("/") if segmento
    )

    if not set(segmentos_origem) & SEGMENTOS_LISTAGEM_CARREIRA:
        return ()

    encontrados: list[LinkCandidatoVaga] = []
    urls_encontradas: set[str] = set()

    for link in resposta.css("a[href]"):
        url = _url_de_detalhe_json_ld(resposta, link.attrib.get("href"))
        if url is None or url in urls_encontradas:
            continue

        segmentos_destino = tuple(
            segmento.casefold() for segmento in urlsplit(url).path.split("/") if segmento
        )

        if (
            len(segmentos_destino) <= len(segmentos_origem)
            or segmentos_destino[: len(segmentos_origem)] != segmentos_origem
        ):
            continue

        texto = " ".join((link.xpath("string(.)").get() or "").split())
        urls_encontradas.add(url)
        encontrados.append(
            LinkCandidatoVaga(
                url=url,
                texto=texto or "Oportunidade publicada na página de carreiras",
                evidencias=("rota_filha_da_pagina_de_carreiras",),
            )
        )

    return tuple(encontrados)


def _descobrir_urls_de_detalhe_em_data_atributos(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Lê URLs públicas de cards que não usam uma âncora HTML."""

    encontrados: list[LinkCandidatoVaga] = []
    urls_encontradas: set[str] = set()

    atributos_url = (
        "data-href",
        "data-url",
        "data-job-url",
        "data-job-detail-url",
        "data-detail-url",
        "data-posting-url",
    )
    seletor = ", ".join(f"[{atributo}]" for atributo in atributos_url)
    for elemento in resposta.css(seletor):
        valor = next(
            (
                elemento.attrib.get(atributo)
                for atributo in atributos_url
                if elemento.attrib.get(atributo)
            ),
            None,
        )
        url = _url_de_detalhe_json_ld(resposta, valor)

        if url is None or url in urls_encontradas:
            continue

        texto = " ".join((elemento.xpath("string(.)").get() or "").split())
        texto_normalizado = texto.casefold()

        if not _parece_url_de_detalhe_de_vaga(url) and not any(
            palavra in texto_normalizado for palavra in PALAVRAS_VAGA
        ):
            continue

        urls_encontradas.add(url)
        encontrados.append(
            LinkCandidatoVaga(
                url=url,
                texto=texto or "Vaga descoberta em atributo público do card",
                evidencias=("url_de_detalhe_em_data_atributo",),
            )
        )

    return tuple(encontrados)


def _descobrir_urls_de_detalhe_em_onclick(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Lê URLs literais de cards que usam navegação declarada em ``onclick``."""

    encontrados: list[LinkCandidatoVaga] = []
    urls_encontradas: set[str] = set()

    for elemento in resposta.css("[onclick]"):
        onclick = elemento.attrib.get("onclick") or ""
        correspondencia = PADRAO_URL_EM_ONCLICK.search(onclick)
        if correspondencia is None:
            continue

        url = _url_de_detalhe_json_ld(
            resposta,
            next(
                valor
                for valor in correspondencia.group(
                    "url",
                    "localizacao",
                    "janela",
                    "rota",
                )
                if valor is not None
            ),
        )
        if url is None or url in urls_encontradas:
            continue

        texto = " ".join((elemento.xpath("string(.)").get() or "").split())
        if not _parece_url_de_detalhe_de_vaga(url) and not any(
            palavra in texto.casefold() for palavra in PALAVRAS_VAGA
        ):
            continue

        urls_encontradas.add(url)
        encontrados.append(
            LinkCandidatoVaga(
                url=url,
                texto=texto or "Vaga descoberta em navegação pública do card",
                evidencias=("url_de_detalhe_em_onclick",),
            )
        )

    return tuple(encontrados)


def _parece_url_de_detalhe_de_vaga(url: str) -> bool:
    """Exige rota de vaga seguida por um identificador ou slug de detalhe."""

    segmentos = [segmento.casefold() for segmento in urlsplit(url).path.split("/") if segmento]

    return any(
        segmento in SEGMENTOS_DETALHE_VAGA and indice + 1 < len(segmentos)
        for indice, segmento in enumerate(segmentos)
    )


def _desescapar_url_serializada(conteudo: str) -> str:
    """Normaliza escapes de URL presentes em JSON/estado público do HTML."""

    def substituir(correspondencia: re.Match[str]) -> str:
        return _ESCAPES_URL_SERIALIZADA.get(
            correspondencia.group(1).casefold(),
            correspondencia.group(0),
        )

    return re.sub(r"\\u([0-9a-fA-F]{4})", substituir, conteudo.replace(r"\/", "/"))
