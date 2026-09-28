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
_SINAL_CANDIDATURA = re.compile(r"candidat|envie|enviar curr[ií]culo|apply|whatsapp", re.I)


def _texto(valor: str | None) -> str:
    return " ".join((valor or "").split())


def _documento(*, titulo: str, descricao: str, url: str, aplicacao: str, extrator: str) -> dict[str, Any]:
    return {
        "@type": "JobPosting",
        "identifier": sha256(f"{url}|{titulo}|{aplicacao}".encode()).hexdigest()[:40],
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": aplicacao,
        "_observatorio_extrator": extrator,
    }


def extrair_vagas_listagem_carreiras(corpo: bytes, *, url: str, codificacao: str = "utf-8") -> ResultadoListagemCarreiras:
    """Extrai somente opções e cards com sinais explícitos de recrutamento."""

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: dict[str, dict[str, Any]] = {}
    for campo in seletor.css("select"):
        identificador = " ".join((campo.attrib.get("id", ""), campo.attrib.get("name", ""))).casefold()
        if not any(palavra in identificador for palavra in ("vaga", "job", "cargo", "position")):
            continue
        aplicacao = f"{url}#{campo.attrib.get('id', 'formulario-vaga')}"
        for opcao in campo.css("option"):
            titulo = _texto(opcao.xpath("string(.)").get())
            if len(titulo) < 3 or _IGNORAR.fullmatch(titulo.casefold()):
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
        titulo = _texto(card.css("h1, h2, h3, h4, [data-job-title], [data-vaga-title]").xpath("string(.)").get())
        if len(titulo) < 3 or len(texto) < len(titulo) or not _SINAL_CANDIDATURA.search(texto):
            continue
        link = card.css("a[href]")
        candidatura = next(
            (
                urljoin(url, item.attrib["href"])
                for item in link
                if _SINAL_CANDIDATURA.search(_texto(item.xpath("string(.)").get()) + " " + item.attrib.get("href", ""))
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
