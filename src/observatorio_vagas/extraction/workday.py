"""Detalhe de vaga da API pública CXS do Workday.

O crawler pede ``/wday/cxs/<tenant>/<site>/job/...`` (GET), que devolve
``jobPostingInfo`` com título, descrição, local, datas e código da vaga. A URL
pública ``/<locale>/<site>/job/...`` devolve só ``{"widget": "redirect"}``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

SUFIXO_WORKDAY = ".myworkdayjobs.com"


def eh_detalhe_workday(url: str, dados: object) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    return (
        host.endswith(SUFIXO_WORKDAY)
        and "/wday/cxs/" in urlsplit(url).path
        and isinstance(dados, Mapping)
        and isinstance(dados.get("jobPostingInfo"), Mapping)
    )


def _nome_empresa(bruto: object) -> str | None:
    """Remove o código interno do início: "020 Cisco Systems, Inc." -> "Cisco Systems, Inc."."""

    if not isinstance(bruto, str):
        return None
    # Só o padrão do código interno ("020 "); "99 Tecnologia" fica intacto.
    nome = re.sub(r"^\s*0\d{2}\s+", "", bruto).strip()
    return nome or None


def _identificador(tenant: str, codigo: str) -> str:
    """Até 50 caracteres sem nunca cortar o código da vaga."""

    completo = f"workday-{tenant}-{codigo}"
    if len(completo) <= 50:
        return completo
    curto = f"wd-{sha256(tenant.encode()).hexdigest()[:8]}-{codigo}"
    return curto if len(curto) <= 50 else f"wd-{sha256(completo.encode()).hexdigest()[:40]}"


def extrair_vaga_workday(dados: Mapping[str, Any], *, url: str) -> dict[str, Any] | None:
    info = dados["jobPostingInfo"]
    titulo = info.get("title")
    codigo = info.get("jobReqId") or info.get("jobPostingId")
    if not isinstance(titulo, str) or not titulo.strip() or not codigo:
        return None

    tenant = urlsplit(url).path.split("/")[3] if len(urlsplit(url).path.split("/")) > 3 else ""
    url_publica = info.get("externalUrl") if isinstance(info.get("externalUrl"), str) else url
    pais = info.get("country") if isinstance(info.get("country"), Mapping) else None
    pais_nome = pais.get("descriptor") if pais and isinstance(pais.get("descriptor"), str) else None

    endereco: dict[str, Any] = {}
    if isinstance(info.get("location"), str):
        endereco["addressLocality"] = info["location"]
    if pais_nome:
        endereco["addressCountry"] = (
            "BR" if pais_nome.casefold() in {"brazil", "brasil"} else pais_nome
        )

    documento: dict[str, Any] = {
        "@type": "JobPosting",
        # Estável: tenant + código da requisição na Workday.
        "identifier": _identificador(tenant, str(codigo)),
        "title": titulo.strip(),
        "description": info.get("jobDescription") or "",
        "url": url_publica,
        "_observatorio_apply_url": url_publica,
        "_observatorio_extrator": "workday_cxs",
        "_observatorio_atributos": {
            chave: info.get(chave)
            for chave in (
                "timeType",
                "remoteType",
                "startDate",
                "endDate",
                "jobReqId",
                "additionalLocations",
            )
            if info.get(chave) is not None
        },
    }
    organizacao = dados.get("hiringOrganization")
    nome = _nome_empresa(
        organizacao.get("name") if isinstance(organizacao, Mapping) else organizacao
    )
    if nome:
        documento["hiringOrganization"] = {"@type": "Organization", "name": nome}
    if endereco:
        documento["jobLocation"] = {"@type": "Place", "address": endereco}
    if isinstance(info.get("startDate"), str):
        documento["datePosted"] = info["startDate"]
    if isinstance(info.get("endDate"), str):
        documento["validThrough"] = info["endDate"]
    if isinstance(info.get("timeType"), str):
        documento["employmentType"] = info["timeType"]
    if isinstance(info.get("remoteType"), str):
        documento["jobLocationType"] = info["remoteType"]
    return documento
