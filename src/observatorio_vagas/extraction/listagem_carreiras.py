"""Leitura conservadora de cards e seletores em páginas próprias de carreira."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin, urlsplit

from parsel import Selector

# Sites que NÃO são da empresa que contrata: agregadores, portais, consultorias e
# redes sociais. Nelas "o site é da empresa" é falso, e a vaga fica sem empresa.
DOMINIOS_DE_TERCEIROS = (
    "jobijoba.com.br", "cargos.com.br", "bne.com.br", "trabalhabrasil.com.br",
    "empregandobrasil.com.br", "vagas.com.br", "infojobs.com.br", "catho.com.br",
    "indeed.com", "linkedin.com", "glassdoor.com", "talent.com", "emploive.com",
    "melhoresempregos.com", "recrutei.com.br", "jobbrazil.com", "programathor.com.br",
    "michaelpage.com.br", "robertahalf.com", "roberthalf.com", "hays.com.br",
    "randstad.com.br", "adecco.com.br", "manpower.com.br", "talentbrand.com.br",
    "hubclubgo.com.br", "drjobpro.com", "sine.com.br", "empregos.com.br",
    "lever.co", "greenhouse.io", "gupy.io", "smartrecruiters.com", "myworkdayjobs.com",
    "abler.com.br", "quickin.io", "solides.com.br", "kenoby.com", "recruitee.com", "breezy.hr",
    "bamboohr.com", "ashbyhq.com", "pandape.infojobs.com.br", "inhire.app",
    "facebook.com", "instagram.com", "google.com", "youtube.com",
)  # fmt: skip
# Páginas de carreira da própria empresa ("aba trabalhe conosco").
CAMINHO_DE_CARREIRA = re.compile(
    r"trabalhe[-_ ]?conosco|trabalhe[-_ ]?na|carreira|career|vaga|oportunidade|jobs?\b|"
    r"talent|recrut|seja[-_ ]?(nosso|parte)|fa[cç]a[-_ ]?parte|work[-_ ]?with|junte[-_ ]?se",
    re.IGNORECASE,
)


def site_e_da_propria_empresa(url: str) -> bool:
    """A página é a aba de carreira de uma empresa (e não portal, agregador ou plataforma)?"""

    from observatorio_vagas.crawling.plataformas import plataforma_do_host

    partes = urlsplit(url)
    host = (partes.hostname or "").casefold().removeprefix("www.")
    if not host or any(host == d or host.endswith("." + d) for d in DOMINIOS_DE_TERCEIROS):
        return False
    if plataforma_do_host(host) is not None:
        return False
    return bool(CAMINHO_DE_CARREIRA.search(partes.path))


def atribuir_empresa_do_site(
    vagas: tuple[dict[str, Any], ...],
    *,
    url: str,
    empresa_nome: str | None,
    corpo: bytes,
    codificacao: str = "utf-8",
) -> tuple[dict[str, Any], ...]:
    """Na aba de carreira da empresa, a empresa é o dono do site (não vem na descrição).

    Só preenche quando a vaga não traz empresa. O nome vem do ``og:site_name`` da página
    e, na falta dele, do nome da fonte no catálogo.
    """

    if not vagas or not site_e_da_propria_empresa(url):
        return vagas

    nome = None
    try:
        seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
        nome = _texto(seletor.css('meta[property="og:site_name"]::attr(content)').get())
    except (ValueError, LookupError):
        nome = None
    nome = nome or _texto(empresa_nome)
    if not nome:
        return vagas

    host = (urlsplit(url).hostname or "").removeprefix("www.")
    resultado = []
    for vaga in vagas:
        if vaga.get("hiringOrganization"):
            resultado.append(vaga)
            continue
        resultado.append(
            {
                **vaga,
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": nome,
                    "sameAs": f"https://{host}",
                },
                "_observatorio_empresa_origem": "site_proprio",
            }
        )
    return tuple(resultado)


@dataclass(frozen=True, slots=True)
class ResultadoListagemCarreiras:
    vagas: tuple[dict[str, Any], ...]


_IGNORAR = re.compile(r"^(selecione|escolha|todos|outra|outros|banco de talentos|cadastro)$", re.I)

# Menus que realmente listam cargos a que o candidato se candidata ("cargo
# pretendido", "vaga", "desired position"). A palavra solta "cargo" ou "job" NÃO
# basta: ela aparece em filtros de busca (job_type, job_category_filter,
# salary_min), em formulários de contato (cargo do contato) e em menus de site.
_MENU_DE_VAGAS = re.compile(
    r"cargo[-_ ]?pretendido|vaga[-_ ]?pretendida|desired[-_ ]?position|"
    r"position[-_ ]?desired|[-_ ]vaga\b|^vaga\b|\bvagas?[-_ ]?(id|select|desejada)|"
    r"posi[cç][aã]o[-_ ]?pretendida|applying[-_ ]?for|apply[-_ ]?position",
    re.I,
)
# Se o menu parece ser filtro, ordenação ou atributo da vaga, não é uma lista de vagas.
_MENU_DE_FILTRO = re.compile(
    r"filter|filtro|categor|area|[aá]rea|location|local|cidade|estado|state|city|salar|"
    r"compensation|currency|order|sort|radius|search|busca|type|tipo|curso|min\b|max\b|"
    r"seeker|menu|nivel|n[ií]vel|level|regime|contrato|modalidade",
    re.I,
)
# Opções que descrevem um atributo (salário, UF, contrato, modalidade), não um cargo.
_OPCAO_DE_ATRIBUTO = re.compile(
    r"^(\d+([.,]\d+)?\s*(k|mil|mi)?\+?|m[ií]n(imo)?\.?|m[aá]x(imo)?\.?|[a-z]{2}|r\$.*|usd|brl|eur|"
    r"clt.*|pj|est[aá]gio|aprendiz|tempor[aá]rio|cooperado|freelancer?|aut[oô]nomo|"
    r"remoto.*|presencial.*|h[ií]brido.*|home ?office|integral|meio per[ií]odo|"
    r"todos? (os |as )?.*|todas? (os |as )?.*|all .*|\d+ ou mais)$",
    re.I,
)
MAXIMO_OPCOES = 150
# Placeholders de menu: "Selecione...", "-- Vaga --", "Escolha a vaga", "Outras vagas".
_PLACEHOLDER = re.compile(
    r"^[\W_]*(selecion|escolh|choose|select|outras? vagas?|vaga[\W_]*$|op[cç][aã]o|"
    r"banco de talentos|cadastr)|^\[.*\]$",
    re.I,
)
_SINAL_CANDIDATURA = re.compile(r"candidat|envie|enviar curr[ií]culo|apply|whatsapp", re.I)


def _texto(valor: str | None) -> str:
    return " ".join((valor or "").split())


def _documento(
    *, titulo: str, descricao: str, url: str, aplicacao: str, extrator: str
) -> dict[str, Any]:  # noqa: E501
    return {
        "@type": "JobPosting",
        "identifier": sha256(f"{url}|{titulo}|{aplicacao}".encode()).hexdigest()[:40],
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": aplicacao,
        "_observatorio_extrator": extrator,
    }


def extrair_vagas_listagem_carreiras(
    corpo: bytes, *, url: str, codificacao: str = "utf-8"
) -> ResultadoListagemCarreiras:  # noqa: E501
    """Extrai somente opções e cards com sinais explícitos de recrutamento."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: dict[str, dict[str, Any]] = {}
    for campo in seletor.css("select"):
        identificador = " ".join(
            (campo.attrib.get("id", ""), campo.attrib.get("name", ""))
        ).casefold()  # noqa: E501
        if not _MENU_DE_VAGAS.search(identificador) or _MENU_DE_FILTRO.search(identificador):
            continue
        if len(campo.css("option")) > MAXIMO_OPCOES:
            continue
        aplicacao = f"{url}#{campo.attrib.get('id', 'formulario-vaga')}"
        for opcao in campo.css("option"):
            titulo = _texto(opcao.xpath("string(.)").get())
            if (
                len(titulo) < 3
                or _IGNORAR.fullmatch(titulo.casefold())
                or _OPCAO_DE_ATRIBUTO.fullmatch(titulo)
                or _PLACEHOLDER.search(titulo)
            ):
                continue
            documento = _documento(
                titulo=titulo,
                descricao="Vaga publicada no formulário de carreira da empresa.",
                url=url,
                aplicacao=aplicacao,
                extrator="seletor_vagas",
            )
            vagas.setdefault(documento["identifier"], documento)
    for card in seletor.css("[data-job-id], [data-vaga-id], .job, .vaga, .vacancy, .opening"):
        texto = _texto(card.xpath("string(.)").get())
        titulo = _texto(
            card.css("h1, h2, h3, h4, [data-job-title], [data-vaga-title]").xpath("string(.)").get()
        )  # noqa: E501
        if len(titulo) < 3 or len(texto) < len(titulo) or not _SINAL_CANDIDATURA.search(texto):
            continue
        link = card.css("a[href]")
        candidatura = next(
            (
                urljoin(url, item.attrib["href"])
                for item in link
                if _SINAL_CANDIDATURA.search(
                    _texto(item.xpath("string(.)").get()) + " " + item.attrib.get("href", "")
                )  # noqa: E501
            ),
            url,
        )
        documento = _documento(
            titulo=titulo,
            descricao=texto[:4000],
            url=url,
            aplicacao=candidatura,
            extrator="card_carreiras",
        )
        vagas.setdefault(documento["identifier"], documento)
    return ResultadoListagemCarreiras(tuple(vagas.values()))
