"""Regras globais de conteúdo que o crawler nunca transforma em vaga."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import unquote, urlsplit

_TERMOS_CONCURSO_PUBLICO = (
    "concurso público",
    "concurso publico",
    "processo seletivo público",
    "processo seletivo publico",
    "edital de concurso",
    "seleção pública",
    "selecao publica",
    "cargo público",
    "cargo publico",
)

# Editais para comprar um serviço não representam uma posição de trabalho.
# As expressões são intencionalmente específicas: "consultor de vendas", por
# exemplo, continua sendo uma vaga legítima e não deve ser bloqueada.
_TERMOS_CONTRATACAO_DE_SERVICO = (
    "edital para contratacao de consultoria",
    "edital para contratação de consultoria",
    "edital para consultoria",
    "edital de contratacao para consultoria",
    "edital de contratação para consultoria",
    "chamamento publico para consultoria",
    "chamamento público para consultoria",
    "solicitacao de propostas para consultoria",
    "solicitação de propostas para consultoria",
    "termo de referencia para contratacao",
    "termo de referência para contratação",
    "contratacao de empresa para prestacao de servicos",
    "contratação de empresa para prestação de serviços",
)


def eh_concurso_publico(
    *,
    url: str,
    conteudo: str | bytes = "",
    codificacao: str = "utf-8",
) -> bool:
    """Identifica concurso pela URL ou pelo texto recebido."""

    caminho = unquote(urlsplit(url).path).casefold()
    segmentos_caminho = set(re.split(r"[/_.-]+", caminho))

    # Um endereço explicitamente chamado /concurso ou /concursos já é
    # suficiente para economizar o download. Para texto da página, porém,
    # continuamos exigindo expressões mais específicas, pois "seleção" pode
    # aparecer em processos privados legítimos.
    if {"concurso", "concursos"} & segmentos_caminho:
        return True

    partes = [caminho, unquote(urlsplit(url).query)]
    if isinstance(conteudo, bytes):
        partes.append(conteudo.decode(codificacao, errors="replace"))
    elif isinstance(conteudo, str):
        partes.append(conteudo)

    texto = " ".join(partes).casefold().replace("-", " ").replace("_", " ")
    return any(termo in texto for termo in _TERMOS_CONCURSO_PUBLICO)


def eh_conteudo_nao_empregaticio(
    *,
    url: str,
    conteudo: str | bytes = "",
    codificacao: str = "utf-8",
) -> bool:
    """Bloqueia concursos e contratações de serviço que não são vagas."""

    if eh_concurso_publico(
        url=url,
        conteudo=conteudo,
        codificacao=codificacao,
    ):
        return True

    texto_url = _normalizar_texto(
        " ".join(
            (
                unquote(urlsplit(url).path),
                unquote(urlsplit(url).query),
            )
        )
    )
    texto_titulo = _extrair_titulo_html(
        conteudo,
        codificacao=codificacao,
    )

    return any(
        termo in texto_url or termo in texto_titulo for termo in _TERMOS_CONTRATACAO_DE_SERVICO
    )


def _extrair_titulo_html(
    conteudo: str | bytes,
    *,
    codificacao: str,
) -> str:
    """Lê título da página, sem deixar links relacionados causar bloqueio."""

    if isinstance(conteudo, bytes):
        texto = conteudo.decode(codificacao, errors="replace")
    elif isinstance(conteudo, str):
        texto = conteudo
    else:
        return ""

    # Conteúdos textuais sem HTML podem ser avaliados diretamente. Em HTML,
    # olhamos apenas título e Open Graph, nunca cards ou links relacionados.
    if "<" not in texto:
        return _normalizar_texto(texto)

    correspondencia = re.search(
        r"<title[^>]*>(?P<titulo>.*?)</title>",
        texto,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if correspondencia is not None:
        return _normalizar_texto(correspondencia.group("titulo"))

    correspondencia = re.search(
        r"<meta[^>]+(?:property|name)=[\"'](?:og:)?title[\"'][^>]+content=[\"'](?P<titulo>.*?)[\"']",
        texto,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if correspondencia is not None:
        return _normalizar_texto(correspondencia.group("titulo"))

    return ""


def _normalizar_texto(texto: str) -> str:
    """Remove marcação, entidades e diferenças superficiais de uma frase."""

    sem_tags = re.sub(r"<[^>]+>", " ", unescape(texto))

    return " ".join(sem_tags.split()).casefold().replace("-", " ").replace("_", " ")
