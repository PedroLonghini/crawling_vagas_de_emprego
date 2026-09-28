"""Descoberta de arquivos CSV publicados por catálogos CKAN."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.descoberta import LinkCandidatoVaga

_PADRAO_ANO = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


@dataclass(frozen=True, slots=True)
class AdaptadorCkan:
    """Seleciona recursos CSV atuais de um conjunto de dados abertos."""

    nome: str = "ckan_dados_abertos"

    def descobrir(
        self,
        resposta: Response,
    ) -> tuple[LinkCandidatoVaga, ...]:
        """Lê ``package_show`` e devolve recursos CSV ativos."""

        if not isinstance(resposta, Response):
            raise TypeError("resposta precisa ser uma Response do Scrapy")

        if not isinstance(resposta, TextResponse):
            return ()

        try:
            dados = json.loads(resposta.text)
        except (json.JSONDecodeError, TypeError):
            return _descobrir_recursos_html(resposta)

        recursos = _recursos_validos(dados)

        if not recursos:
            return ()

        # Conjuntos como o da UFPE mantêm um CSV para cada ano.
        # Nesses casos lemos somente o ano mais recente. Conjuntos sem ano
        # no nome, como as agências do ES, mantêm todos os recursos.
        anos = tuple(ano for recurso in recursos if (ano := _ano_recurso(recurso)) is not None)

        if anos:
            ano_atual = max(anos)
            recursos = tuple(
                recurso for recurso in recursos if _ano_recurso(recurso) in {None, ano_atual}
            )

        return tuple(
            LinkCandidatoVaga(
                url=recurso["url"],
                texto=recurso["nome"],
                evidencias=("recurso_csv_ckan",),
            )
            for recurso in recursos
        )


def _recursos_validos(
    dados: Any,
) -> tuple[dict[str, str], ...]:
    """Extrai somente recursos CSV ativos com endereço HTTP."""

    if not isinstance(dados, dict) or dados.get("success") is not True:
        return ()

    resultado = dados.get("result")

    if not isinstance(resultado, dict):
        return ()

    recursos = resultado.get("resources")

    if not isinstance(recursos, list):
        return ()

    selecionados: list[dict[str, str]] = []

    for recurso in recursos:
        if not isinstance(recurso, dict):
            continue

        formato = str(recurso.get("format") or "").strip().casefold()
        estado = str(recurso.get("state") or "active").strip().casefold()
        url = recurso.get("url")

        if formato != "csv" or estado != "active":
            continue

        if not isinstance(url, str) or not url.strip().casefold().startswith(
            ("http://", "https://")
        ):
            continue

        nome = recurso.get("name")
        selecionados.append(
            {
                "nome": nome.strip() if isinstance(nome, str) and nome.strip() else "Recurso CSV",
                "url": url.strip(),
            }
        )

    return tuple(selecionados)


def _ano_recurso(
    recurso: dict[str, str],
) -> int | None:
    """Obtém o ano indicado no nome ou na URL do recurso."""

    # UUIDs no caminho podem conter quatro dígitos parecidos com um ano.
    arquivo = urlsplit(recurso["url"]).path.rsplit("/", 1)[-1]
    correspondencia = _PADRAO_ANO.search(f"{recurso['nome']} {arquivo}")

    return int(correspondencia.group(1)) if correspondencia is not None else None


def _descobrir_recursos_html(resposta: TextResponse) -> tuple[LinkCandidatoVaga, ...]:
    """Segue a interface pública CKAN, inclusive quando robots bloqueia /api/.

    Não tenta contornar robots: cada URL ainda passa pelo middleware. Só links
    explícitos de recursos do conjunto são candidatos, nunca todo o portal.
    """
    candidatos: dict[str, LinkCandidatoVaga] = {}
    for recurso in resposta.css(".resource-item"):
        if not recurso.css('[data-format="csv"]'):
            continue
        links = recurso.css("a[href]")
        diretos = [a for a in links if urlsplit(a.attrib["href"]).path.lower().endswith(".csv")]
        texto = " ".join((recurso.css(".heading").xpath("string(.)").get() or "").split())
        for link in diretos or links:
            url = resposta.urljoin(link.attrib["href"])
            if "/resource/" not in urlsplit(url).path:
                continue
            evidencia = "recurso_csv_ckan" if diretos else "listagem_recurso_ckan"
            candidatos[url] = LinkCandidatoVaga(url, texto or "Recurso CKAN", (evidencia,))
    # Na página individual do recurso, o botão de download contém a URL real.
    for link in resposta.css("a.resource-url-analytics[href]"):
        url = resposta.urljoin(link.attrib["href"])
        if urlsplit(url).path.lower().endswith(".csv"):
            candidatos.setdefault(url, LinkCandidatoVaga(url, "CSV", ("recurso_csv_ckan",)))
    anos = [_ano_recurso({"nome": c.texto, "url": c.url}) for c in candidatos.values()]
    ultimo = max((ano for ano in anos if ano is not None), default=None)
    return tuple(
        candidato
        for candidato, ano in zip(candidatos.values(), anos, strict=True)
        if ano is None or ano == ultimo
    )
