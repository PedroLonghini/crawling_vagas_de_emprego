"""Descoberta de vagas públicas em páginas de carreiras da Gupy."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.adaptadores.generico import (
    AdaptadorGenericoHTML,
)
from observatorio_vagas.crawling.descoberta import LinkCandidatoVaga

PADRAO_CAMINHO_VAGA_GUPY = re.compile(r"^/jobs/(?P<id>[0-9]+)/?$")


@dataclass(frozen=True, slots=True)
class AdaptadorGupy:
    """Lê o estado público do Next.js e os links renderizados pela Gupy."""

    nome: str = "gupy"
    fallback: AdaptadorGenericoHTML = field(
        default_factory=AdaptadorGenericoHTML,
    )

    def descobrir(
        self,
        resposta: Response,
    ) -> tuple[LinkCandidatoVaga, ...]:
        """Descobre vagas da Gupy e completa o resultado com o fallback HTML."""

        if not isinstance(resposta, Response):
            raise TypeError("resposta precisa ser uma Response do Scrapy")

        if not isinstance(resposta, TextResponse):
            return ()

        encontrados: list[LinkCandidatoVaga] = []
        urls_encontradas: set[str] = set()

        # O estado do Next.js é a fonte mais estável da listagem atual.
        for candidato in _descobrir_no_next_data(resposta):
            _adicionar_sem_repeticao(
                candidato,
                encontrados=encontrados,
                urls_encontradas=urls_encontradas,
            )

        # Os links SSR mantêm o adaptador funcional se o formato do JSON mudar.
        for candidato in _descobrir_em_ancoras(resposta):
            _adicionar_sem_repeticao(
                candidato,
                encontrados=encontrados,
                urls_encontradas=urls_encontradas,
            )

        # O mecanismo genérico pode reconhecer uma página que ainda não recebeu
        # uma regra de plataforma. Os candidatos Gupy permanecem na frente para
        # não perder vagas quando o limite de páginas do catálogo for aplicado.
        for candidato in self.fallback.descobrir(resposta):
            url_gupy = _normalizar_url_vaga(
                resposta.url,
                candidato.url,
            )

            if url_gupy is not None and url_gupy in urls_encontradas:
                continue

            _adicionar_sem_repeticao(
                candidato,
                encontrados=encontrados,
                urls_encontradas=urls_encontradas,
            )

        return tuple(encontrados)


def _descobrir_no_next_data(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Lê somente a coleção pública ``pageProps.jobs`` da página Gupy."""

    texto_json = resposta.css("script#__NEXT_DATA__::text").get()

    if not texto_json:
        return ()

    try:
        dados = json.loads(texto_json)

    except (json.JSONDecodeError, TypeError):
        return ()

    page_props = _obter_mapeamento(
        _obter_mapeamento(dados).get("props"),
    ).get("pageProps")
    vagas = _obter_mapeamento(page_props).get("jobs")

    if not isinstance(vagas, list):
        return ()

    encontrados: list[LinkCandidatoVaga] = []

    for vaga in vagas:
        documento = _obter_mapeamento(vaga)
        identificador = documento.get("id")

        if isinstance(identificador, bool):
            continue

        texto_id = str(identificador).strip()

        if not texto_id.isdecimal():
            continue

        url = _normalizar_url_vaga(
            resposta.url,
            f"/jobs/{texto_id}",
        )

        if url is None:
            continue

        titulo = documento.get("title")
        texto = titulo.strip() if isinstance(titulo, str) else ""

        encontrados.append(
            LinkCandidatoVaga(
                url=url,
                texto=texto,
                evidencias=("gupy_next_data",),
            )
        )

    return tuple(encontrados)


def _descobrir_em_ancoras(
    resposta: TextResponse,
) -> tuple[LinkCandidatoVaga, ...]:
    """Reconhece URLs públicas ``/jobs/<id>`` mesmo sem palavras no texto."""

    encontrados: list[LinkCandidatoVaga] = []

    for elemento in resposta.css("a[href]"):
        href = (elemento.attrib.get("href") or "").strip()
        url = _normalizar_url_vaga(
            resposta.url,
            href,
        )

        if url is None:
            continue

        texto_original = elemento.xpath("string(.)").get() or ""
        texto = " ".join(texto_original.split())

        encontrados.append(
            LinkCandidatoVaga(
                url=url,
                texto=texto,
                evidencias=("padrao_url_gupy",),
            )
        )

    return tuple(encontrados)


def _normalizar_url_vaga(
    url_base: str,
    referencia: str,
) -> str | None:
    """Produz uma URL canônica somente para uma vaga do mesmo domínio."""

    if not referencia:
        return None

    url_absoluta = urljoin(
        url_base,
        referencia,
    )
    url_sem_fragmento, _ = urldefrag(url_absoluta)
    endereco = urlsplit(url_sem_fragmento)
    endereco_base = urlsplit(url_base)

    if endereco.scheme not in {"http", "https"}:
        return None

    if (endereco.hostname or "").casefold() != (endereco_base.hostname or "").casefold():
        return None

    correspondencia = PADRAO_CAMINHO_VAGA_GUPY.fullmatch(endereco.path)

    if correspondencia is None:
        return None

    caminho = f"/jobs/{correspondencia.group('id')}"

    # Parâmetros de rastreamento e fragmentos não identificam outra vaga.
    return urlunsplit(
        (
            endereco.scheme.casefold(),
            endereco.netloc.casefold(),
            caminho,
            "",
            "",
        )
    )


def _obter_mapeamento(
    valor: Any,
) -> Mapping[str, Any]:
    """Retorna um mapeamento seguro ou uma coleção vazia."""

    return valor if isinstance(valor, Mapping) else {}


def _adicionar_sem_repeticao(
    candidato: LinkCandidatoVaga,
    *,
    encontrados: list[LinkCandidatoVaga],
    urls_encontradas: set[str],
) -> None:
    """Mantém a ordem e elimina URLs já encontradas por outra estratégia."""

    if candidato.url in urls_encontradas:
        return

    urls_encontradas.add(candidato.url)
    encontrados.append(candidato)
