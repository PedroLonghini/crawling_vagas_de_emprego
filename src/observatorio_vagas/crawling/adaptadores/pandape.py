"""Descoberta de vagas públicas em páginas de carreiras da Pandapé."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from scrapy.http import Response, TextResponse

from observatorio_vagas.crawling.adaptadores.generico import (
    AdaptadorGenericoHTML,
)
from observatorio_vagas.crawling.descoberta import LinkCandidatoVaga

PADRAO_CAMINHO_VAGA_PANDAPE = re.compile(
    r"^/detail/(?P<id>[0-9]+)/?$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AdaptadorPandape:
    """Reconhece detalhes públicos ``/Detail/<id>`` da Pandapé."""

    nome: str = "pandape"
    fallback: AdaptadorGenericoHTML = field(
        default_factory=AdaptadorGenericoHTML,
    )

    def descobrir(
        self,
        resposta: Response,
    ) -> tuple[LinkCandidatoVaga, ...]:
        """Descobre vagas no HTML já baixado, sem executar requisições."""

        if not isinstance(resposta, Response):
            raise TypeError("resposta precisa ser uma Response do Scrapy")

        if not isinstance(resposta, TextResponse):
            return ()

        encontrados: list[LinkCandidatoVaga] = []
        urls_encontradas: set[str] = set()

        for elemento in resposta.css("a[href]"):
            url = _normalizar_url_vaga(
                resposta.url,
                elemento.attrib.get("href") or "",
            )

            if url is None or url in urls_encontradas:
                continue

            texto_original = elemento.xpath("string(.)").get() or ""
            texto = " ".join(texto_original.split())
            urls_encontradas.add(url)
            encontrados.append(
                LinkCandidatoVaga(
                    url=url,
                    texto=texto,
                    evidencias=("padrao_url_pandape",),
                )
            )

        # Mantém a descoberta conservadora já usada pelos sites desconhecidos.
        # Links Pandapé ficam na frente para não perder vagas quando o catálogo
        # limita a quantidade de páginas de detalhe.
        for candidato in self.fallback.descobrir(resposta):
            url_pandape = _normalizar_url_vaga(
                resposta.url,
                candidato.url,
            )

            if url_pandape is not None and url_pandape in urls_encontradas:
                continue

            if candidato.url in urls_encontradas:
                continue

            caminho = urlsplit(candidato.url).path.casefold()

            # Rotas de candidatura manipulam dados pessoais e uma URL de
            # detalhe inválida não deve contornar o reconhecedor estrito.
            if caminho.startswith(("/apply/", "/applyct/", "/detail/")):
                continue

            urls_encontradas.add(candidato.url)
            encontrados.append(candidato)

        return tuple(encontrados)


def _normalizar_url_vaga(
    url_base: str,
    referencia: str,
) -> str | None:
    """Produz uma URL canônica de vaga somente no domínio autorizado."""

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

    correspondencia = PADRAO_CAMINHO_VAGA_PANDAPE.fullmatch(endereco.path)

    if correspondencia is None:
        return None

    return urlunsplit(
        (
            endereco.scheme.casefold(),
            endereco.netloc.casefold(),
            f"/Detail/{correspondencia.group('id')}",
            "",
            "",
        )
    )
