"""Extração segura de metadados institucionais da empresa."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from unicodedata import combining, normalize
from urllib.parse import urljoin, urlsplit

from parsel import Selector

# Seções semânticas usadas por páginas tradicionais.
_SELETORES_DESCRICAO = (
    '//*[@id="school-overview"]/p',
    '//*[@id="company-overview"]/p',
    '//*[@id="employer-overview"]/p',
    '//*[contains(@class, "company-description")]/p',
    '//*[contains(@class, "employer-description")]/p',
)

# Algumas páginas não usam classes semânticas, mas expõem um título claro
# imediatamente antes do texto institucional. Só títulos explícitos entram
# nesta lista: termos como "Sobre a vaga" permanecem fora para não misturar a
# descrição do trabalho com a da organização.
_TITULOS_DESCRICAO_EMPRESA = frozenset(
    {
        "sobre a empresa",
        "quem somos",
        "sobre nos",
        "a empresa",
    }
)

# Links explicitamente identificados como site da organização.
_SELETORES_SITE = (
    '//*[@data-link-type="school_website"]/@href',
    '//*[@data-link-type="company_website"]/@href',
    '//*[@data-link-type="employer_website"]/@href',
)

# Imagens apresentadas no cabeçalho da empresa.
_SELETORES_LOGO = (
    '//*[contains(@class, "header-with-logo-logo")]//img/@src',
    '//*[contains(@class, "company-logo")]//img/@src',
    '//*[contains(@class, "employer-logo")]//img/@src',
)

# Uma página institucional comum nem sempre possui as classes utilizadas por
# páginas de carreiras. O resumo ``meta description`` é uma alternativa útil,
# mas só deve ser usado quando já sabemos que a página é o site oficial da
# empresa. Por isso ele não participa do extrator chamado durante a leitura
# normal de vagas.
_SELETORES_META_DESCRICAO = (
    '//meta[translate(@name, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz") '
    '= "description"]/@content',
    '//meta[translate(@property, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz") '
    '= "og:description"]/@content',
)


@dataclass(frozen=True, slots=True)
class MetadadosEmpresaHtml:
    """Informações institucionais encontradas no HTML."""

    descricao: str | None
    site: str | None
    logo_url: str | None
    evidencias: tuple[str, ...]


def _normalizar_texto(
    valor: str | None,
) -> str | None:
    """Remove espaços repetidos e devolve somente textos úteis."""

    if valor is None:
        return None

    texto = " ".join(
        valor.split(),
    )

    return texto or None


def _normalizar_texto_html(
    valor: object,
) -> str | None:
    """Remove marcação HTML de uma descrição institucional."""

    if not isinstance(valor, str) or not valor.strip():
        return None

    seletor = Selector(
        text=valor,
    )

    texto = _normalizar_texto(
        " ".join(
            seletor.xpath("//text()").getall(),
        )
    )

    if texto is None or len(texto) < 40:
        return None

    return texto


def _normalizar_titulo(
    valor: str | None,
) -> str | None:
    """Normaliza um título para comparação sem diferenças de acentuação."""

    texto = _normalizar_texto(valor)

    if texto is None:
        return None

    sem_acentos = "".join(
        caractere for caractere in normalize("NFKD", texto) if not combining(caractere)
    )

    return sem_acentos.casefold().strip(" .:-–—") or None


def _normalizar_url(
    valor: str | None,
    *,
    url_base: str,
) -> str | None:
    """Transforma uma URL relativa em absoluta e valida o protocolo."""

    texto = _normalizar_texto(
        valor,
    )

    if texto is None:
        return None

    url = urljoin(
        url_base,
        texto,
    )

    partes = urlsplit(
        url,
    )

    if partes.scheme not in {
        "http",
        "https",
    }:
        return None

    if partes.hostname is None:
        return None

    return url


def _extrair_descricao(
    seletor: Selector,
) -> str | None:
    """Encontra uma descrição dentro de uma seção institucional."""

    for xpath in _SELETORES_DESCRICAO:
        paragrafos: list[str] = []

        for elemento in seletor.xpath(
            xpath,
        ):
            texto = _normalizar_texto(
                elemento.xpath(
                    "string(.)",
                ).get(),
            )

            if texto is not None:
                paragrafos.append(
                    texto,
                )

        descricao = "\n\n".join(
            paragrafos,
        ).strip()

        if len(descricao) >= 40:
            return descricao

    return None


def _extrair_descricao_por_titulo(
    seletor: Selector,
) -> str | None:
    """Extrai o primeiro bloco logo após um título institucional explícito."""

    titulos = seletor.xpath("//h1 | //h2 | //h3 | //h4 | //h5 | //h6")

    for titulo in titulos:
        titulo_normalizado = _normalizar_titulo(titulo.xpath("string(.)").get())

        if titulo_normalizado not in _TITULOS_DESCRICAO_EMPRESA:
            continue

        # A exigência de ser o irmão seguinte limita a captura ao bloco que o
        # autor da página vinculou ao título, sem varrer o restante da vaga.
        bloco = titulo.xpath("following-sibling::*[self::p or self::div or self::article][1]")

        if not bloco:
            continue

        descricao = _normalizar_texto(bloco.xpath("string(.)").get())

        if descricao is not None and len(descricao) >= 40:
            return descricao

    return None


def _extrair_primeira_url(
    seletor: Selector,
    *,
    seletores: tuple[str, ...],
    url_base: str,
) -> str | None:
    """Obtém a primeira URL HTTP válida dos seletores informados."""

    for xpath in seletores:
        for valor in seletor.xpath(
            xpath,
        ).getall():
            url = _normalizar_url(
                valor,
                url_base=url_base,
            )

            if url is not None:
                return url

    return None


def _extrair_meta_descricao(
    seletor: Selector,
) -> str | None:
    """Obtém o resumo institucional publicado nos metadados HTML."""

    for xpath in _SELETORES_META_DESCRICAO:
        for valor in seletor.xpath(xpath).getall():
            descricao = _normalizar_texto(valor)

            if descricao is not None and len(descricao) >= 40:
                return descricao

    return None


def _obter_mapeamento(
    valor: object,
) -> Mapping[str, Any] | None:
    """Retorna o valor somente quando ele for um objeto."""

    return valor if isinstance(valor, Mapping) else None


def _extrair_metadados_next_data(
    seletor: Selector,
    *,
    url_base: str,
) -> MetadadosEmpresaHtml | None:
    """Extrai os dados institucionais do estado público do Next.js."""

    conteudo = seletor.xpath(
        '//script[@id="__NEXT_DATA__"]/text()',
    ).get()

    if conteudo is None:
        return None

    try:
        raiz = json.loads(
            conteudo,
        )

    except json.JSONDecodeError:
        return None

    raiz_mapeada = _obter_mapeamento(
        raiz,
    )

    if raiz_mapeada is None:
        return None

    props = _obter_mapeamento(
        raiz_mapeada.get("props"),
    )

    page_props = (
        _obter_mapeamento(
            props.get("pageProps"),
        )
        if props is not None
        else None
    )

    job = (
        _obter_mapeamento(
            page_props.get("job"),
        )
        if page_props is not None
        else None
    )

    carreira = (
        _obter_mapeamento(
            job.get("careerPage"),
        )
        if job is not None
        else None
    )

    if carreira is None:
        return None

    descricao = _normalizar_texto_html(
        carreira.get("about"),
    )

    redes_sociais = _obter_mapeamento(
        carreira.get("socialLinks"),
    )

    site_original = carreira.get(
        "urlSite",
    )

    if (
        not isinstance(site_original, str) or not site_original.strip()
    ) and redes_sociais is not None:
        site_original = redes_sociais.get(
            "urlSite",
        )

    site = _normalizar_url(
        site_original if isinstance(site_original, str) else None,
        url_base=url_base,
    )

    logo_original = carreira.get(
        "urlLogo",
    )

    logo_url = _normalizar_url(
        logo_original if isinstance(logo_original, str) else None,
        url_base=url_base,
    )

    evidencias: list[str] = []

    if descricao is not None:
        evidencias.append(
            "next_data_descricao_empresa",
        )

    if site is not None:
        evidencias.append(
            "next_data_site_empresa",
        )

    if logo_url is not None:
        evidencias.append(
            "next_data_logo_empresa",
        )

    if not evidencias:
        return None

    return MetadadosEmpresaHtml(
        descricao=descricao,
        site=site,
        logo_url=logo_url,
        evidencias=tuple(
            evidencias,
        ),
    )


def extrair_metadados_empresa_html(
    conteudo: bytes | str,
    *,
    url_base: str,
) -> MetadadosEmpresaHtml | None:
    """Extrai informações institucionais da página já coletada."""

    html = (
        conteudo.decode(
            "utf-8",
            errors="replace",
        )
        if isinstance(conteudo, bytes)
        else conteudo
    )

    seletor = Selector(
        text=html,
    )

    descricao = _extrair_descricao(
        seletor,
    )
    evidencia_descricao: str | None = "secao_institucional" if descricao is not None else None

    if descricao is None:
        descricao = _extrair_descricao_por_titulo(seletor)
        evidencia_descricao = "titulo_secao_institucional" if descricao is not None else None

    site = _extrair_primeira_url(
        seletor,
        seletores=_SELETORES_SITE,
        url_base=url_base,
    )

    logo_url = _extrair_primeira_url(
        seletor,
        seletores=_SELETORES_LOGO,
        url_base=url_base,
    )

    evidencias: list[str] = []

    if evidencia_descricao is not None:
        evidencias.append(evidencia_descricao)

    if site is not None:
        evidencias.append(
            "link_site_institucional",
        )

    if logo_url is not None:
        evidencias.append(
            "imagem_cabecalho_empresa",
        )

    metadados_next = _extrair_metadados_next_data(
        seletor,
        url_base=url_base,
    )

    if metadados_next is not None:
        if descricao is None and metadados_next.descricao is not None:
            descricao = metadados_next.descricao
            evidencias.append(
                "next_data_descricao_empresa",
            )

        if site is None and metadados_next.site is not None:
            site = metadados_next.site
            evidencias.append(
                "next_data_site_empresa",
            )

        if logo_url is None and metadados_next.logo_url is not None:
            logo_url = metadados_next.logo_url
            evidencias.append(
                "next_data_logo_empresa",
            )

    if not evidencias:
        return None

    return MetadadosEmpresaHtml(
        descricao=descricao,
        site=site,
        logo_url=logo_url,
        evidencias=tuple(
            evidencias,
        ),
    )


def extrair_metadados_site_institucional_html(
    conteudo: bytes | str,
    *,
    url_base: str,
) -> MetadadosEmpresaHtml | None:
    """Extrai dados de um site oficial informado e revisado pela operação.

    Diferente de :func:`extrair_metadados_empresa_html`, esta função pode usar
    ``meta description``. Ela existe para o enriquecimento explícito de uma
    empresa e não deve ser aplicada automaticamente a páginas de vaga, pois
    nelas a meta descrição costuma falar da vaga, não da organização.
    """

    resultado = extrair_metadados_empresa_html(
        conteudo,
        url_base=url_base,
    )

    if resultado is not None and resultado.descricao is not None:
        return resultado

    html = conteudo.decode("utf-8", errors="replace") if isinstance(conteudo, bytes) else conteudo
    seletor = Selector(text=html)
    descricao = _extrair_meta_descricao(seletor)

    if descricao is None:
        return resultado

    evidencias = list(resultado.evidencias) if resultado is not None else []
    evidencias.append("meta_description_site_institucional")

    return MetadadosEmpresaHtml(
        descricao=descricao,
        site=resultado.site
        if resultado is not None
        else _normalizar_url(url_base, url_base=url_base),
        logo_url=resultado.logo_url if resultado is not None else None,
        evidencias=tuple(evidencias),
    )


def _campo_vazio(
    valor: object,
) -> bool:
    """Verifica se um campo estruturado está vazio."""

    if valor is None:
        return True

    return isinstance(valor, str) and not valor.strip()


def _url_http_absoluta(
    valor: object,
) -> bool:
    """Verifica se o valor representa uma URL HTTP absoluta."""

    if not isinstance(valor, str):
        return False

    texto = valor.strip()

    if not texto:
        return False

    partes = urlsplit(
        texto,
    )

    return (
        partes.scheme
        in {
            "http",
            "https",
        }
        and partes.hostname is not None
    )


def enriquecer_job_posting_com_empresa(
    documento: Mapping[str, Any],
    metadados: MetadadosEmpresaHtml | None,
) -> dict[str, Any]:
    """Preenche lacunas da organização sem perder dados válidos."""

    documento_enriquecido = dict(
        documento,
    )

    if metadados is None:
        return documento_enriquecido

    organizacao_original = documento.get(
        "hiringOrganization",
    )

    if not isinstance(
        organizacao_original,
        Mapping,
    ):
        return documento_enriquecido

    organizacao = dict(
        organizacao_original,
    )

    if metadados.descricao is not None and _campo_vazio(
        organizacao.get("description"),
    ):
        organizacao["description"] = metadados.descricao

    if metadados.site is not None and not _url_http_absoluta(
        organizacao.get("sameAs"),
    ):
        organizacao["sameAs"] = metadados.site

    if metadados.logo_url is not None and not _url_http_absoluta(
        organizacao.get("logo"),
    ):
        organizacao["logo"] = metadados.logo_url

    documento_enriquecido["hiringOrganization"] = organizacao

    return documento_enriquecido
