"""Extrator restrito às vagas em inglês publicadas pelo Tor Project."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from parsel import Selector


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoTorProject:
    """Resultado do extrator específico do quadro de vagas do Tor Project."""

    vagas: tuple[dict[str, Any], ...]
    dominio_reconhecido: bool


_DOMINIOS = {"tor.eff.org", "www.tor.eff.org"}
_CAMINHO_VAGA = re.compile(r"/about/jobs/(?P<slug>[a-z0-9-]+)/$")
_PRAZO = re.compile(
    r"\bdeadline\s*:\s*([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)


def _texto(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = " ".join(valor.split())
    return texto or None


def eh_url_tor_project_vaga(url: str) -> bool:
    """Aceita apenas detalhes canônicos, em inglês, de vagas do Tor Project."""

    partes = urlsplit(url)
    caminho = _CAMINHO_VAGA.fullmatch(partes.path)
    return (
        partes.hostname in _DOMINIOS
        and caminho is not None
        and caminho.group("slug") != "board-of-directors"
    )


def _prazo(texto: str) -> str | None:
    encontrado = _PRAZO.search(texto)
    if encontrado is None:
        return None
    try:
        return datetime.strptime(encontrado.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def extrair_job_posting_tor_project(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoTorProject:
    """Converte detalhes canônicos de ``tor.eff.org/about/jobs`` em JobPosting.

    A página de vagas possui versões em outros idiomas e uma página do conselho
    misturada à navegação. Ambas são reconhecidas e devolvem vazio, para nunca
    caírem no fallback HTML genérico como se fossem anúncios.
    """

    partes = urlsplit(url)
    caminho_de_vagas = "/about/jobs" in partes.path
    if partes.hostname not in _DOMINIOS or not caminho_de_vagas:
        return ResultadoExtracaoTorProject((), False)

    caminho = _CAMINHO_VAGA.fullmatch(partes.path)
    if caminho is None or caminho.group("slug") == "board-of-directors":
        return ResultadoExtracaoTorProject((), True)

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    # O título fica no cabeçalho visual, logo antes de ``main`` no HTML real.
    titulo = _texto(seletor.css("h2.display-3::text").get())
    conteudo = _texto(seletor.css("main").xpath("string(.)").get())
    url_candidatura = seletor.xpath(
        "//main//h2[translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='how to apply']"
        "/following::a[1]/@href"
    ).get()
    if (
        titulo is None
        or conteudo is None
        or len(conteudo) < 250
        or not isinstance(url_candidatura, str)
        or not url_candidatura.startswith(("https://", "http://"))
    ):
        return ResultadoExtracaoTorProject((), True)

    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "identifier": caminho.group("slug"),
        "title": titulo,
        "description": conteudo,
        "url": url,
        "jobLocationType": "TELECOMMUTE",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Remote",
                "addressCountry": "Worldwide",
            },
        },
        "_observatorio_extrator": "tor_project",
        "_observatorio_apply_url": url_candidatura,
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Tor Project",
            "sameAs": "https://www.torproject.org/",
            "description": (
                "Organização sem fins lucrativos que desenvolve tecnologias de "
                "privacidade e anonimato para a Internet."
            ),
        },
    }
    prazo = _prazo(conteudo)
    if prazo:
        documento["validThrough"] = prazo
    return ResultadoExtracaoTorProject((documento,), True)
