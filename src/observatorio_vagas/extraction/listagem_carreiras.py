"""Leitura conservadora de cards e seletores em páginas próprias de carreira."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin

from parsel import Selector


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
    r"^[\W_]*(selecion|escolh|choose|select|outras? vagas?|vaga[\W_]*$|op[cç][aã]o)",
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
