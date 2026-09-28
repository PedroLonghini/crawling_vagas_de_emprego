"""Extração de processos seletivos publicados em portais municipais Instar."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from parsel import Selector

from observatorio_vagas.extraction.cnpj_documento import validar_cnpj

_CAMINHO_EDITAL = re.compile(r"^/portal/editais/0/3/\d+/?$")
_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
_CARGO = re.compile(
    r"(?:contrata(?:ç|c)ão\s+temporária\s+de|cargo(?:s)?(?:\s+de)?|funç(?:ão|oes))"
    r"\s+(.+?)(?=,\s+(?:para|com)|[.;]|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
_TITULO_PREFEITURA = re.compile(
    r"^(Prefeitura(?:\s+Municipal)?\s+de\s+.+?)"
    r"(?:\s+-\s+([A-Z]{2}))?\s+-\s+(?:PROCESSO|EDITAL)",
    flags=re.IGNORECASE,
)
_FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoPortalPublico:
    """Resultado da tentativa de ler uma página pública municipal."""

    vagas: tuple[dict[str, Any], ...]
    pagina_reconhecida: bool


def _html_texto(conteudo: str | bytes, codificacao: str) -> str:
    """Converte o corpo em texto sem esconder problemas de configuração."""

    if isinstance(conteudo, str):
        return conteudo

    if not isinstance(conteudo, bytes):
        raise TypeError("conteudo precisa ser texto ou bytes")

    if not codificacao.strip():
        raise ValueError("codificacao não pode ser vazia")

    return conteudo.decode(codificacao, errors="replace")


def _compactar_texto(valor: str | None) -> str | None:
    """Remove espaços repetidos de um texto opcional."""

    if valor is None:
        return None

    texto = " ".join(valor.split())

    return texto or None


def _detalhes_edital(seletor: Selector) -> dict[str, str]:
    """Lê pares como ``Fim das Inscrições`` e seu valor."""

    detalhes: dict[str, str] = {}

    for linha in seletor.css(".ed_linha_lista_detalhes"):
        nome = _compactar_texto(
            linha.css(".ed_nome_detalhe::text").get(),
        )
        valor = _compactar_texto(
            linha.css(".ed_descricao_detalhe *::text, .ed_descricao_detalhe::text").getall()
            and " ".join(
                linha.css(".ed_descricao_detalhe *::text, .ed_descricao_detalhe::text").getall()
            ),
        )

        if nome is not None and valor is not None:
            detalhes[nome.casefold()] = valor

    return detalhes


def _converter_data_portal(valor: str | None) -> str | None:
    """Converte ``31/08/2026 às 08h00`` para ISO 8601 com fuso."""

    if valor is None:
        return None

    correspondencia = re.search(
        r"(\d{2}/\d{2}/\d{4})(?:\s+às\s+(\d{2})h(\d{2}))?",
        valor,
        flags=re.IGNORECASE,
    )

    if correspondencia is None:
        return None

    data = correspondencia.group(1)
    hora = correspondencia.group(2) or "00"
    minuto = correspondencia.group(3) or "00"

    momento = datetime.strptime(
        f"{data} {hora}:{minuto}",
        "%d/%m/%Y %H:%M",
    ).replace(tzinfo=_FUSO_BRASILIA)

    return momento.isoformat()


def _normalizar_titulo_cargo(valor: str) -> str:
    """Melhora caixa alta sem transformar ``III`` em ``Iii``."""

    palavras = valor.strip().title().split()

    return " ".join(
        palavra.upper() if re.fullmatch(r"[ivxlcdm]+", palavra, re.IGNORECASE) else palavra
        for palavra in palavras
    )


def _empresa_do_titulo(titulo_pagina: str) -> tuple[str, str, str] | None:
    """Obtém nome, município e UF a partir do título institucional."""

    correspondencia = _TITULO_PREFEITURA.search(titulo_pagina)

    if correspondencia is None:
        return None

    nome = _compactar_texto(correspondencia.group(1))
    estado = (correspondencia.group(2) or "").upper()

    if nome is None or not estado:
        return None

    cidade = re.sub(
        r"^Prefeitura(?:\s+Municipal)?\s+de\s+",
        "",
        nome,
        flags=re.IGNORECASE,
    ).strip()

    return nome, cidade, estado


def extrair_job_postings_portal_publico(
    conteudo: str | bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoPortalPublico:
    """Converte um edital municipal Instar aberto em ``JobPosting``."""

    endereco = urlsplit(url)

    if endereco.scheme not in {"http", "https"} or not _CAMINHO_EDITAL.fullmatch(endereco.path):
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=False,
        )

    seletor = Selector(text=_html_texto(conteudo, codificacao))
    detalhes = _detalhes_edital(seletor)

    if not detalhes:
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=False,
        )

    situacao = detalhes.get("situação")

    if situacao is None or situacao.casefold() != "aberto":
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=True,
        )

    titulo_pagina = _compactar_texto(seletor.css("title::text").get())
    empresa = _empresa_do_titulo(titulo_pagina or "")

    if empresa is None:
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=True,
        )

    empresa_nome, cidade, estado = empresa
    texto_pagina = seletor.xpath("string(//body)").get() or ""
    cnpj_encontrado = _CNPJ.search(texto_pagina)

    if cnpj_encontrado is None or not validar_cnpj(cnpj_encontrado.group(0)):
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=True,
        )

    descricao_edital = _compactar_texto(
        seletor.css(".ed_descricao_edital::text, .ed_descricao_edital *::text").getall()
        and " ".join(
            seletor.css(".ed_descricao_edital::text, .ed_descricao_edital *::text").getall()
        )
    )

    if descricao_edital is None:
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=True,
        )

    cargo_encontrado = _CARGO.search(descricao_edital)

    if cargo_encontrado is None:
        return ResultadoExtracaoPortalPublico(
            vagas=(),
            pagina_reconhecida=True,
        )

    inicio_inscricoes = detalhes.get("início das inscrições")
    fim_inscricoes = detalhes.get("fim das inscrições")
    descricao = descricao_edital

    if inicio_inscricoes and fim_inscricoes:
        descricao = (
            f"{descricao_edital} Período de inscrições: "
            f"{inicio_inscricoes} até {fim_inscricoes}. "
            "Consulte a página oficial e o edital antes de se inscrever."
        )

    documento: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {
            "@type": "PropertyValue",
            "name": empresa_nome,
            "value": detalhes.get("nº do processo") or url,
        },
        "title": _normalizar_titulo_cargo(cargo_encontrado.group(1)),
        "description": descricao,
        "datePosted": _converter_data_portal(detalhes.get("publicado em")),
        "validThrough": _converter_data_portal(fim_inscricoes),
        "employmentType": "TEMPORARY",
        "url": url,
        "_observatorio_apply_url": url,
        "hiringOrganization": {
            "@type": "Organization",
            "name": empresa_nome,
            "description": (
                f"{empresa_nome} é o órgão público responsável pela "
                f"administração municipal de {cidade}, {estado}."
            ),
            "sameAs": f"{endereco.scheme}://{endereco.netloc}/",
            "taxID": cnpj_encontrado.group(0),
        },
        "industry": "Administração Pública",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": cidade,
                "addressRegion": estado,
                "addressCountry": "BR",
            },
        },
    }

    return ResultadoExtracaoPortalPublico(
        vagas=(documento,),
        pagina_reconhecida=True,
    )
