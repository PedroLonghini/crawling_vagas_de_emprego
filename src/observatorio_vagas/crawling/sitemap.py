"""Descoberta limitada de detalhes de vagas em sitemaps públicos."""

import re
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from lxml import etree
from scrapy.exceptions import NotSupported
from scrapy.http import Response, TextResponse

SEGMENTOS_VAGA = frozenset(
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


# Um sitemap menor que isto não tem nenhuma URL (o teste de 10 mil fontes baixou
# 15 mil sitemaps vazios).
TAMANHO_MINIMO_SITEMAP = 100

# Sub-sitemaps com nome de vaga/carreira. Lojas e blogs têm sitemap-produtos,
# sitemap-posts, sitemap-news, sitemap-images: 95% dos 93 mil sitemaps do teste
# anterior não tinham nenhuma URL útil.
NOME_DE_SITEMAP_DE_VAGAS = re.compile(
    r"vaga|job|carreira|career|trabalh|oportunidad|emprego|opening|position|talent|recrut|hiring",
    re.IGNORECASE,
)
# Se nenhum nome indicar vagas, seguimos só os primeiros; e nunca mais que o teto.
SITEMAPS_SEM_PISTA = 2
MAXIMO_SUBSITEMAPS = 5


def criar_url_sitemap_padrao(resposta: Response) -> str:
    """Retorna o sitemap convencional do mesmo domínio da página inicial."""

    if not isinstance(resposta, Response):
        raise TypeError("resposta precisa ser uma Response do Scrapy")

    endereco = urlsplit(resposta.url)

    return urlunsplit(
        (
            endereco.scheme,
            endereco.netloc,
            "/sitemap.xml",
            "",
            "",
        )
    )


def eh_url_sitemap(url: str) -> bool:
    """Indica se a URL aponta para um mapa XML do próprio site."""

    caminho = urlsplit(url).path.casefold()

    return caminho.endswith(".xml") and "sitemap" in caminho


def eh_resposta_sitemap(resposta: Response) -> bool:
    """Identifica XML de sitemap por URL ou tipo de conteúdo."""

    if not isinstance(resposta, Response):
        raise TypeError("resposta precisa ser uma Response do Scrapy")

    tipo = resposta.headers.get(b"Content-Type", b"").decode(
        "latin-1",
        errors="replace",
    )

    return eh_url_sitemap(resposta.url) or "xml" in tipo.casefold()


def descobrir_urls_sitemap(resposta: Response) -> tuple[str, ...]:
    """Lê apenas mapas e URLs prováveis de detalhes de vaga do mesmo domínio."""

    if not isinstance(resposta, Response):
        raise TypeError("resposta precisa ser uma Response do Scrapy")

    if not isinstance(resposta, TextResponse) or not eh_resposta_sitemap(resposta):
        return ()

    if len(resposta.body) < TAMANHO_MINIMO_SITEMAP:
        return ()

    origem = (urlsplit(resposta.url).hostname or "").casefold()
    encontrados: list[str] = []
    urls_encontradas: set[str] = set()

    try:
        locais = resposta.xpath("//*[local-name()='loc']/text()").getall()
    except (etree.LxmlError, NotSupported, ValueError):
        return ()

    for valor in locais:
        if not isinstance(valor, str) or not valor.strip():
            continue

        url, _ = urldefrag(urljoin(resposta.url, valor.strip()))
        endereco = urlsplit(url)

        if endereco.scheme not in {"http", "https"}:
            continue

        if (endereco.hostname or "").casefold() != origem:
            continue

        if not (eh_url_sitemap(url) or _parece_detalhe_vaga(url)):
            continue

        if url in urls_encontradas:
            continue

        urls_encontradas.add(url)
        encontrados.append(url)

    return _limitar_subsitemaps(tuple(encontrados))


def _limitar_subsitemaps(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Mantém os detalhes e só os sub-sitemaps que parecem ser de vagas."""

    subsitemaps = [url for url in urls if eh_url_sitemap(url)]
    com_pista = [url for url in subsitemaps if NOME_DE_SITEMAP_DE_VAGAS.search(urlsplit(url).path)]
    escolhidos = set(com_pista[:MAXIMO_SUBSITEMAPS] or subsitemaps[:SITEMAPS_SEM_PISTA])

    return tuple(url for url in urls if not eh_url_sitemap(url) or url in escolhidos)


def _parece_detalhe_vaga(url: str) -> bool:
    """Evita baixar páginas institucionais listadas em um sitemap amplo."""

    segmentos = [segmento.casefold() for segmento in urlsplit(url).path.split("/") if segmento]

    return any(
        segmento in SEGMENTOS_VAGA and indice + 1 < len(segmentos)
        for indice, segmento in enumerate(segmentos)
    )
