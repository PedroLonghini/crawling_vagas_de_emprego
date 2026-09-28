"""Extrator restrito às páginas de vaga publicadas pelo NIC.br."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from parsel import Selector


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoNicBr:
    """Resultado do extrator específico do quadro de vagas do NIC.br."""

    vagas: tuple[dict[str, Any], ...]
    dominio_reconhecido: bool


_MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
_PRAZO = re.compile(
    r"(?:currículo|curriculo|candidatura|inscri[çc][ãa]o)[^.]{0,120}?"
    r"at[ée]\s+(?:o\s+dia\s+)?(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})",
    re.IGNORECASE,
)
_CAPTACAO = re.compile(
    r"capta[çc][ãa]o\s+de\s+curr[íi]culos\s*:\s*\d{2}/\d{2}/\d{4}\s+a\s+(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)


def _texto(valor: str | None) -> str | None:
    if valor is None:
        return None
    resultado = " ".join(valor.split())
    return resultado or None


def _prazo(texto: str) -> str | None:
    encontrado = _PRAZO.search(texto)
    if encontrado is not None:
        dia, mes_nome, ano = encontrado.groups()
        mes = _MESES.get(mes_nome.casefold())
        if mes is not None:
            try:
                return date(int(ano), mes, int(dia)).isoformat()
            except ValueError:
                pass
    captacao = _CAPTACAO.search(texto)
    if captacao is None:
        return None
    try:
        ano, mes, dia = reversed(captacao.group(1).split("/"))
        return date(int(ano), int(mes), int(dia)).isoformat()
    except ValueError:
        return None


def _localidade(texto: str) -> str | None:
    """Aceita somente local explicitamente divulgado na própria vaga."""

    # No HTML do NIC.br as quebras de linha são normalizadas antes de chegar
    # aqui; por isso extraímos a cidade/UF, e não todo o parágrafo seguinte.
    encontrado = re.search(
        r"\bS(?:ã|a)o\s+Paulo(?:\s*[,–-]\s*SP|\s*\(SP\))?",
        texto,
        re.IGNORECASE,
    )
    if encontrado is None:
        return None
    return _texto(encontrado.group(0))


def extrair_job_posting_nic_br(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoNicBr:
    """Converte detalhes ``nic.br/vagas/view/<id>/`` em JobPosting interno."""

    partes = urlsplit(url)
    if partes.hostname not in {"nic.br", "www.nic.br"} or not re.fullmatch(
        r"/vagas/view/\d+/", partes.path
    ):
        return ResultadoExtracaoNicBr((), False)

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    titulo = _texto(seletor.css("main h6.post-vaga-title::text").get())
    conteudo = _texto(seletor.css("main").xpath("string(.)").get())
    if titulo is None or conteudo is None or len(conteudo) < 80:
        return ResultadoExtracaoNicBr((), True)

    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "identifier": partes.path.strip("/").rsplit("/", 1)[-1],
        "title": titulo,
        "description": conteudo,
        "url": url,
        "_observatorio_extrator": "nic_br",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "NIC.br",
            "taxID": "05.506.560/0001-36",
            "sameAs": "https://nic.br/",
            "description": (
                "Instituição privada sem fins lucrativos responsável por "
                "atividades de coordenação e registro da Internet no Brasil."
            ),
        },
    }
    localidade = _localidade(conteudo)
    if localidade:
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": localidade,
                "addressCountry": "BR",
            },
        }
    if re.search(r"\bregime\s+(?:CLT|de\s+contrata[çc][ãa]o\s*:\s*CLT)\b", conteudo, re.I):
        documento["employmentType"] = "CLT"
    if re.search(r"\bmodelo\s*:\s*presencial\b|\b(?:vaga|estágio) presencial\b", conteudo, re.I):
        documento["jobLocationType"] = "ON_SITE"
    prazo = _prazo(conteudo)
    if prazo:
        documento["validThrough"] = prazo
    # O destino oferecido pelo NIC.br é normalmente um e-mail. Ele permanece
    # no HTML bruto e na descrição, mas não é ``applyUrl``: o contrato da API
    # aceita apenas URLs HTTP/HTTPS.
    email = seletor.css("main a[href^='mailto:']::attr(href)").get()
    if email:
        documento["_observatorio_email_candidatura"] = email.removeprefix("mailto:")
    return ResultadoExtracaoNicBr((documento,), True)
