"""Conversão segura de respostas das APIs licenciadas de agregadores.

Este módulo não executa HTTP nem contém chaves. Ele apenas transforma a
resposta JSON já recebida em JSON-LD ``JobPosting``, o idioma comum da camada
de extração. Assim Adzuna e Jooble passam pelo mesmo processo de validação,
deduplicação e prontidão usado pelas páginas HTML.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class AdaptadorApiAgregadora(Protocol):
    """Contrato para uma resposta JSON de um provedor licenciado."""

    nome: str

    def extrair_job_postings(self, resposta: object) -> tuple[dict[str, Any], ...]:
        """Converte a resposta do provedor em documentos JSON-LD."""


@dataclass(frozen=True, slots=True)
class AdaptadorAdzunaApi:
    """Converte resultados da API Adzuna em vagas padronizadas."""

    nome: str = "adzuna_api"

    def extrair_job_postings(self, resposta: object) -> tuple[dict[str, Any], ...]:
        """Lê a coleção pública ``results`` sem supor campos opcionais."""

        dados = _mapeamento(resposta)
        resultados = dados.get("results")
        if not isinstance(resultados, list):
            return ()

        vagas: list[dict[str, Any]] = []
        for resultado in resultados:
            documento = _mapeamento(resultado)
            vaga = _montar_job_posting(
                provedor="adzuna",
                identificador=_texto(documento.get("id")),
                titulo=_texto(documento.get("title")),
                descricao=_texto(documento.get("description")),
                url=_texto(documento.get("redirect_url")),
                empresa=_texto(_mapeamento(documento.get("company")).get("display_name")),
                localidade=_texto(_mapeamento(documento.get("location")).get("display_name")),
                publicada_em=_texto(documento.get("created")),
                tipo_contrato=_juntar_textos(
                    _texto(documento.get("contract_type")),
                    _texto(documento.get("contract_time")),
                ),
                salario_min=_numero(documento.get("salary_min")),
                salario_max=_numero(documento.get("salary_max")),
                categoria=_texto(_mapeamento(documento.get("category")).get("label")),
                latitude=_numero(documento.get("latitude")),
                longitude=_numero(documento.get("longitude")),
            )
            if vaga is not None:
                vagas.append(vaga)
        return tuple(vagas)


@dataclass(frozen=True, slots=True)
class AdaptadorJoobleApi:
    """Converte resultados da API Jooble em vagas padronizadas."""

    nome: str = "jooble_api"

    def extrair_job_postings(self, resposta: object) -> tuple[dict[str, Any], ...]:
        """Lê a coleção ``jobs`` retornada pela API, tolerando lacunas."""

        dados = _mapeamento(resposta)
        resultados = dados.get("jobs")
        if not isinstance(resultados, list):
            return ()

        vagas: list[dict[str, Any]] = []
        for resultado in resultados:
            documento = _mapeamento(resultado)
            vaga = _montar_job_posting(
                provedor="jooble",
                identificador=_texto(documento.get("id")),
                titulo=_texto(documento.get("title")),
                descricao=_texto(documento.get("snippet")),
                url=_texto(documento.get("link")),
                empresa=_texto(documento.get("company")),
                localidade=_texto(documento.get("location")),
                publicada_em=_texto(documento.get("updated")),
                tipo_contrato=_texto(documento.get("type")),
                salario_min=None,
                salario_max=None,
                categoria=None,
                latitude=None,
                longitude=None,
            )
            if vaga is not None:
                vagas.append(vaga)
        return tuple(vagas)


def _montar_job_posting(
    *,
    provedor: str,
    identificador: str | None,
    titulo: str | None,
    descricao: str | None,
    url: str | None,
    empresa: str | None,
    localidade: str | None,
    publicada_em: str | None,
    tipo_contrato: str | None,
    salario_min: int | float | None,
    salario_max: int | float | None,
    categoria: str | None,
    latitude: int | float | None,
    longitude: int | float | None,
) -> dict[str, Any] | None:
    """Monta somente uma vaga que tenha identidade, título e URL pública."""

    if not identificador or not titulo or not url:
        return None

    vaga: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {
            "@type": "PropertyValue",
            "name": provedor,
            "value": identificador,
        },
        "title": titulo,
        "url": url,
    }
    if descricao:
        vaga["description"] = descricao
    if publicada_em:
        vaga["datePosted"] = publicada_em
    if tipo_contrato:
        vaga["employmentType"] = tipo_contrato
    if empresa:
        vaga["hiringOrganization"] = {"@type": "Organization", "name": empresa}
    if localidade:
        local: dict[str, Any] = {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressLocality": localidade},
        }
        if latitude is not None and longitude is not None:
            local["geo"] = {
                "@type": "GeoCoordinates",
                "latitude": latitude,
                "longitude": longitude,
            }
        vaga["jobLocation"] = local
    if salario_min is not None or salario_max is not None:
        vaga["baseSalary"] = {
            "@type": "MonetaryAmount",
            "value": {
                "@type": "QuantitativeValue",
                "minValue": salario_min,
                "maxValue": salario_max,
            },
        }
    if categoria:
        vaga["occupationalCategory"] = categoria
    return vaga


def _mapeamento(valor: object) -> Mapping[str, Any]:
    """Devolve um mapeamento seguro para dados externos imprevisíveis."""

    return valor if isinstance(valor, Mapping) else {}


def _texto(valor: object) -> str | None:
    """Normaliza um texto opcional sem transformar valores estranhos em texto."""

    if not isinstance(valor, str):
        return None
    normalizado = " ".join(valor.split())
    return normalizado or None


def _numero(valor: object) -> int | float | None:
    """Aceita números publicados, rejeitando booleanos e textos ambíguos."""

    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return valor


def _juntar_textos(*valores: str | None) -> str | None:
    """Une campos complementares sem repetir o mesmo valor."""

    unicos = tuple(dict.fromkeys(valor for valor in valores if valor))
    return ", ".join(unicos) or None
