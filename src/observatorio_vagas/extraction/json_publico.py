"""Converte listagens JSON públicas de páginas próprias em JobPosting."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin, urlsplit

from observatorio_vagas.extraction.workday import eh_detalhe_workday, extrair_vaga_workday


@dataclass(frozen=True, slots=True)
class ResultadoJsonPublico:
    """Itens aproveitados de uma resposta JSON de vagas."""

    vagas: tuple[dict[str, Any], ...]


_CHAVES_TITULO = ("title", "jobTitle", "job_title", "name", "position", "vacancy")
_CHAVES_DESCRICAO = ("description", "jobDescription", "job_description", "details")
_CHAVES_ID = ("id", "jobId", "job_id", "vacancyId", "vacancy_id", "code")
_CHAVES_URL = ("detailUrl", "detail_url", "jobUrl", "job_url", "postingUrl", "url")
_CHAVES_LOCAL = ("location", "city", "locality", "workplace")
# Empresa que contrata: SmartRecruiters traz company.name; Sólides, companyName.
_CHAVES_EMPRESA = (
    "companyName", "company_name", "employerName", "employer_name", "hiringOrganization",
    "company", "employer",
)  # fmt: skip
_CHAVES_UF = ("state", "uf", "region", "addressRegion")
_CHAVES_PAIS = ("country", "countryCode", "addressCountry")
DOMINIO_API_ABLER = "hulk-smash.abler.com.br"


def _texto(valor: object) -> str | None:
    if isinstance(valor, (str, int, float)) and not isinstance(valor, bool):
        texto = str(valor).strip()
        return texto or None
    return None


def _objetos(valor: object) -> Iterator[Mapping[str, object]]:
    """Objetos candidatos a vaga, sem entrar dentro de uma vaga já encontrada.

    Dentro da vaga, estado, cidade, nível e benefícios também têm ``id`` e ``name``
    ({"id": 20, "name": "São Paulo"}); lidos como objetos soltos, viravam vagas
    ("São Paulo", "CLT", "Refeitório" — 13 de 20 numa página da Sólides).
    """

    if isinstance(valor, Mapping):
        yield valor
        if _parece_vaga(valor) and not _tem_lista_de_objetos(valor):
            return
        for filho in valor.values():
            yield from _objetos(filho)
    elif isinstance(valor, list):
        for filho in valor:
            yield from _objetos(filho)


def _parece_vaga(objeto: Mapping[str, object]) -> bool:
    titulo = _primeiro(objeto, _CHAVES_TITULO)
    return bool(titulo) and bool(
        _primeiro(objeto, _CHAVES_DESCRICAO) or any(objeto.get(c) for c in _CHAVES_URL)
    )


def _tem_lista_de_objetos(objeto: Mapping[str, object]) -> bool:
    """Envelope com uma lista de vagas dentro ({"name": "Empresa", "jobs": [...]})."""

    return any(
        isinstance(filho, list)
        and any(_parece_vaga(item) for item in filho if isinstance(item, Mapping))
        for filho in objeto.values()
    )


def _primeiro(objeto: Mapping[str, object], chaves: tuple[str, ...]) -> str | None:
    return next((_texto(objeto.get(chave)) for chave in chaves if _texto(objeto.get(chave))), None)


def _url_mesmo_dominio(valor: object, *, url_base: str) -> str | None:
    texto = _texto(valor)
    if texto is None:
        return None
    url = urljoin(url_base, texto)
    if urlsplit(url).hostname != urlsplit(url_base).hostname:
        return None
    return url


def _localidade(valor: object) -> str | None:
    if isinstance(valor, Mapping):
        return _primeiro(valor, ("name", "city", "locality", "label"))
    return _texto(valor)


def _nome(valor: object) -> str | None:
    """Texto solto ou o ``name`` de um objeto ({"name": "Red Bull"})."""

    if isinstance(valor, Mapping):
        return _primeiro(valor, ("name", "label", "title"))
    return _texto(valor)


def _empresa(objeto: Mapping[str, object]) -> str | None:
    return next((nome for chave in _CHAVES_EMPRESA if (nome := _nome(objeto.get(chave)))), None)


def _uf_e_pais(objeto: Mapping[str, object]) -> tuple[str | None, str | None]:
    """UF e país do objeto da vaga ou do seu ``location`` (Sólides: state.code)."""

    fontes = [objeto]
    if isinstance(objeto.get("location"), Mapping):
        fontes.append(objeto["location"])  # type: ignore[arg-type]
    uf = pais = None
    for fonte in fontes:
        for chave in _CHAVES_UF:
            valor = fonte.get(chave)
            if isinstance(valor, Mapping):
                valor = _primeiro(valor, ("code", "sigla", "abbreviation", "name"))
            uf = uf or _texto(valor)
        for chave in _CHAVES_PAIS:
            pais = pais or _nome(fonte.get(chave))
    return uf, pais


def extrair_vagas_json_publico(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoJsonPublico:
    """Lê formatos JSON comuns sem assumir uma API privada específica."""

    try:
        dados = json.loads(corpo.decode(codificacao, errors="replace"))
    except (TypeError, ValueError):
        return ResultadoJsonPublico(())

    if eh_detalhe_workday(url, dados):
        vaga_workday = extrair_vaga_workday(dados, url=url)
        return ResultadoJsonPublico((vaga_workday,) if vaga_workday else ())

    if urlsplit(url).hostname == DOMINIO_API_ABLER:
        vagas_abler = _extrair_vagas_abler(dados)
        if vagas_abler:
            return ResultadoJsonPublico(vagas_abler)

    vagas: dict[str, dict[str, Any]] = {}
    for objeto in _objetos(dados):
        titulo = _primeiro(objeto, _CHAVES_TITULO)
        if titulo is None or len(titulo) < 3:
            continue
        # Envelope (empresa, página de resultados) com a lista de vagas dentro.
        if _tem_lista_de_objetos(objeto):
            continue
        identificador = _primeiro(objeto, _CHAVES_ID)
        url_detalhe = next(
            (
                _url_mesmo_dominio(objeto.get(chave), url_base=url)
                for chave in _CHAVES_URL
                if objeto.get(chave)
            ),
            None,
        )
        descricao = _primeiro(objeto, _CHAVES_DESCRICAO)
        localidade = _localidade(
            next((objeto.get(chave) for chave in _CHAVES_LOCAL if objeto.get(chave)), None)
        )
        # Apenas um título solto não prova que este objeto é uma vaga.
        if (
            identificador is None
            and url_detalhe is None
            and descricao is None
            and localidade is None
        ):
            continue
        chave = identificador or url_detalhe or sha256(f"{url}|{titulo}".encode()).hexdigest()[:40]
        documento: dict[str, Any] = {
            "@type": "JobPosting",
            "identifier": chave,
            "title": titulo,
            "description": descricao or "",
            "url": url_detalhe or url,
            "_observatorio_apply_url": url_detalhe or url,
            "_observatorio_extrator": "json_publico",
        }
        empresa = _empresa(objeto)
        if empresa:
            documento["hiringOrganization"] = {"@type": "Organization", "name": empresa}
        if localidade:
            endereco: dict[str, str] = {"addressLocality": localidade}
            uf, pais = _uf_e_pais(objeto)
            if uf and uf != localidade:
                endereco["addressRegion"] = uf
            if pais:
                endereco["addressCountry"] = pais
            documento["jobLocation"] = {"@type": "Place", "address": endereco}
        vagas.setdefault(chave, documento)
    return ResultadoJsonPublico(tuple(vagas.values()))


def _extrair_vagas_abler(dados: object) -> tuple[dict[str, Any], ...]:
    """Converte a resposta pública da Abler sem perder a URL de candidatura.

    A API é hospedada em ``hulk-smash.abler.com.br``, enquanto ``full_url``
    aponta para a página pública da empresa onde o candidato se inscreve.
    """

    itens = dados.get("data") if isinstance(dados, Mapping) else None
    if not isinstance(itens, list):
        return ()
    vagas: dict[str, dict[str, Any]] = {}
    for item in itens:
        if not isinstance(item, Mapping):
            continue
        atributos = item.get("attributes")
        if not isinstance(atributos, Mapping):
            continue
        identificador = _texto(item.get("id")) or _texto(atributos.get("id"))
        titulo = _texto(atributos.get("title"))
        if not identificador or not titulo:
            continue
        url_candidatura = _texto(atributos.get("full_url")) or _texto(atributos.get("short_url"))
        if not url_candidatura or urlsplit(url_candidatura).scheme not in {"http", "https"}:
            continue
        descricao = _texto(atributos.get("description")) or ""
        cidade = _texto(atributos.get("city"))
        estado = _texto(atributos.get("state"))
        localidade = ", ".join(parte for parte in (cidade, estado) if parte)
        documento: dict[str, Any] = {
            "@type": "JobPosting",
            "identifier": f"abler-{identificador}",
            "title": titulo,
            "description": descricao,
            "url": url_candidatura,
            "_observatorio_apply_url": url_candidatura,
            "_observatorio_extrator": "abler_api_publica",
            # Atributos originais: a leitura completa usa salário, modalidade,
            # vínculo, nível, prazo e descrição da empresa que estão aqui.
            "_observatorio_atributos": dict(atributos),
        }
        empresa = _texto(atributos.get("company_name"))
        if empresa:
            documento["hiringOrganization"] = {"@type": "Organization", "name": empresa}
        if localidade:
            documento["jobLocation"] = {
                "@type": "Place",
                "address": {"addressLocality": localidade},
            }
        vagas.setdefault(documento["identifier"], documento)
    return tuple(vagas.values())
