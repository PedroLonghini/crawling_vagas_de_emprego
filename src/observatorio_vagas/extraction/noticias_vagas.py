"""Adaptadores conservadores para notícias com vagas explicitamente listadas.

Uma notícia não é uma página de vagas por si só. Este módulo só cria um
``JobPosting`` quando a própria matéria lista um cargo de forma inequívoca.
Ele também confirma a frase de permissão que foi registrada no catálogo: se a
matéria mudar e a permissão desaparecer, nada é extraído.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from parsel import Selector

_DOMINIO_PALOTINA = "palotina24horas.com.br"
_DOMINIO_ITAQUI = "itaqui.rs.gov.br"
_PERMISSAO = "reprodução permitida desde que seja mantido o crédito ao palotina 24 horas"
_PERMISSAO_ITAQUI = (
    "todo material produzido pela assessoria de comunicação pode ser reproduzido "
    "desde que citada a fonte"
)
_SINAIS_CONCURSO = re.compile(
    r"\b(?:concurso\s+p[uú]blico|processo\s+seletivo\s+simplificado|pss\s+n[º°o]?)\b",
    flags=re.IGNORECASE,
)
_PADRAO_CARGO_QUANTIDADE = re.compile(
    r"^\s*(?P<titulo>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ /&().,-]{2,100}?)"
    r"\s*[–-]\s*(?P<quantidade>\d{1,4})\s+vagas?\s*$",
    flags=re.IGNORECASE,
)
_PADRAO_CARGO_LISTA = re.compile(
    r"^\s*(?:[-•]\s*)?(?P<titulo>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ /&().,-]{2,100})\s*$"
)
_PADRAO_CVALE = re.compile(
    r"C\.\s*Vale.*?\b(?P<titulo>Auxiliar de Produção)\b",
    flags=re.IGNORECASE,
)
_PADRAO_ITAQUI = re.compile(
    r"^\s*(?:[-•]\s*)?(?P<titulo>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ /&().,-]{2,100}?)"
    r"\s*[–-]\s*(?P<quantidade>\d{1,4})\s+vagas?\s*:?\s*(?P<detalhes>.*)$",
    flags=re.IGNORECASE,
)
_PADRAO_CARGO_TEXTO = re.compile(
    r"\b(?:cargo|vaga)\s+de\s+(?P<titulo>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ /&().-]{2,70}?)"
    r"(?=\s+(?:da|do|na|no|em|com|para)\b|[,.;:])",
    flags=re.IGNORECASE,
)
_EXCLUIDOS_LISTA = frozenset(
    {
        "as oportunidades são para",
        "as vagas são para",
        "vagas disponíveis",
        "veja as vagas",
        "entrevista na agência",
        "entrevistas na agência",
        "confira as vagas",
        "agência do trabalhador de palotina",
    }
)


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoNoticiasVagas:
    """Resultado auditável da tentativa de converter uma notícia em vagas."""

    vagas: tuple[dict[str, Any], ...]
    pagina_reconhecida: bool


def _texto_compacto(valor: str | None) -> str | None:
    """Remove espaços repetidos e devolve ``None`` para texto vazio."""

    if valor is None:
        return None

    texto = " ".join(valor.split())
    return texto or None


def _texto_da_materia(seletor: Selector) -> str | None:
    """Obtém somente o conteúdo editorial, sem menu, rodapé ou scripts."""

    conteudo = seletor.css(".post_content, .entry-content, article, main, #conteudo, .conteudo")
    if not conteudo:
        conteudo = seletor.css("body")

    return _texto_compacto(" ".join(conteudo.xpath(".//text()[not(ancestor::script)]").getall()))


def _titulo_linhas(texto: str) -> tuple[str, ...]:
    """Lê cargos de linhas, aceitando somente títulos curtos e explícitos."""

    encontrados: list[str] = []
    vistos: set[str] = set()

    for linha in texto.splitlines():
        compacta = _texto_compacto(linha)
        if compacta is None:
            continue

        correspondencia = _PADRAO_CARGO_QUANTIDADE.fullmatch(compacta)
        if correspondencia is not None:
            candidato = correspondencia.group("titulo")
        else:
            correspondencia_lista = _PADRAO_CARGO_LISTA.fullmatch(compacta)
            candidato = correspondencia_lista.group("titulo") if correspondencia_lista else None

        titulo = _texto_compacto(candidato)
        if titulo is None or titulo.casefold() in _EXCLUIDOS_LISTA:
            continue

        # Frases completas, datas e endereços não são cargos.
        if len(titulo) > 80 or any(sinal in titulo for sinal in ".:;?"):
            continue

        chave = titulo.casefold()
        if chave not in vistos:
            vistos.add(chave)
            encontrados.append(titulo)

    return tuple(encontrados)


def _documento(
    *,
    titulo: str,
    descricao: str,
    url: str,
    empresa: str,
    localidade: str,
    regiao: str,
    fonte_nome: str,
    extrator: str,
    publicado_em: str | None,
) -> dict[str, Any]:
    """Monta um documento Schema.org com apenas fatos disponíveis."""

    identificador = sha256(f"{url}#{titulo.casefold()}".encode()).hexdigest()[:50]
    documento: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {
            "@type": "PropertyValue",
            "name": fonte_nome,
            "value": identificador,
        },
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": url,
        "_observatorio_extrator": extrator,
        "hiringOrganization": {"@type": "Organization", "name": empresa},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": localidade,
                "addressRegion": regiao,
                "addressCountry": "BR",
            },
        },
    }

    if publicado_em is not None:
        documento["datePosted"] = publicado_em
        validade = _validade_semanal(publicado_em)
        if validade is not None:
            documento["validThrough"] = validade

    return documento


def _validade_semanal(publicado_em: str) -> str | None:
    """Limita boletins semanais para impedir republicação de vagas antigas."""

    try:
        instante = datetime.fromisoformat(publicado_em.replace("Z", "+00:00"))
    except ValueError:
        return None

    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=UTC)

    return (instante + timedelta(days=7)).isoformat()


def _texto_pagina(seletor: Selector) -> str:
    """Texto integral usado apenas para confirmar licença e bloqueios."""

    textos = seletor.xpath("//body//text()[not(ancestor::script)]").getall()
    return _texto_compacto(" ".join(textos)) or ""


def _publicado_em(seletor: Selector) -> str | None:
    """Obtém uma data ISO publicada pela página, quando disponível."""

    for seletor_data in (
        "meta[property='article:published_time']::attr(content)",
        "meta[name='date']::attr(content)",
        "time::attr(datetime)",
    ):
        valor = _texto_compacto(seletor.css(seletor_data).get())
        if valor is not None:
            return valor

    return None


def extrair_job_postings_noticia_palotina24h(
    conteudo: str | bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoNoticiasVagas:
    """Extrai cargos que a matéria do Palotina 24 Horas enumera literalmente.

    Não converte o total divulgado (por exemplo, ``231 vagas``) em centenas
    de anúncios artificiais. Cada anúncio criado corresponde a um cargo que
    aparece, por extenso, na matéria original.
    """

    endereco = urlsplit(url)
    if (
        endereco.scheme not in {"http", "https"}
        or (endereco.hostname or "").casefold().removeprefix("www.") != _DOMINIO_PALOTINA
    ):
        return ResultadoExtracaoNoticiasVagas((), False)

    if isinstance(conteudo, bytes):
        html = conteudo.decode(codificacao, errors="replace")
    elif isinstance(conteudo, str):
        html = conteudo
    else:
        raise TypeError("conteudo precisa ser texto ou bytes")

    seletor = Selector(text=html)
    materia = _texto_da_materia(seletor)
    pagina = _texto_pagina(seletor)
    if materia is None or _PERMISSAO not in pagina.casefold():
        return ResultadoExtracaoNoticiasVagas((), True)

    if _SINAIS_CONCURSO.search(materia):
        return ResultadoExtracaoNoticiasVagas((), True)

    materia_normalizada = materia.casefold()
    lista_palotina = "vaga" in materia_normalizada and "palotina" in materia_normalizada
    if not lista_palotina and not _PADRAO_CVALE.search(materia):
        return ResultadoExtracaoNoticiasVagas((), True)

    publicado_em = _publicado_em(seletor)
    descricao = (
        f"Fonte: Palotina 24 Horas. Reprodução permitida com crédito à fonte. "
        f"Matéria original: {url}. {materia}"
    )

    if endereco.path.endswith("c-vale-abre-inscricoes-para-jovem-aprendiz-administrativo/"):
        correspondencia = _PADRAO_CVALE.search(materia)
        cargos = (correspondencia.group("titulo"),) if correspondencia else ()
        empresa = "C.Vale"
        localidade = "Assis Chateaubriand e Maripá"
    elif "itaipulandia" in endereco.path:
        cargos = _titulo_linhas(_preservar_linhas(seletor))
        empresa = "Prefeitura de Itaipulândia"
        localidade = "Itaipulândia"
    else:
        cargos = _titulo_linhas(_preservar_linhas(seletor))
        empresa = "Empregador não divulgado (Agência do Trabalhador de Palotina)"
        localidade = "Palotina"

    return ResultadoExtracaoNoticiasVagas(
        tuple(
            _documento(
                titulo=cargo,
                descricao=descricao,
                url=url,
                empresa=empresa,
                localidade=localidade,
                regiao="PR",
                fonte_nome="Palotina 24 Horas",
                extrator="noticia_palotina24h",
                publicado_em=publicado_em,
            )
            for cargo in cargos
        ),
        True,
    )


def extrair_job_postings_noticia_itaqui(
    conteudo: str | bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoNoticiasVagas:
    """Extrai vagas privadas divulgadas pelo FGTAS/Sine de Itaqui.

    A autorização é conferida na própria página e notícias sobre concursos ou
    seleções públicas permanecem bloqueadas.
    """

    endereco = urlsplit(url)
    if (
        endereco.scheme not in {"http", "https"}
        or (endereco.hostname or "").casefold().removeprefix("www.") != _DOMINIO_ITAQUI
    ):
        return ResultadoExtracaoNoticiasVagas((), False)

    if isinstance(conteudo, bytes):
        html = conteudo.decode(codificacao, errors="replace")
    elif isinstance(conteudo, str):
        html = conteudo
    else:
        raise TypeError("conteudo precisa ser texto ou bytes")

    seletor = Selector(text=html)
    pagina = _texto_pagina(seletor)
    materia = _texto_da_materia(seletor)
    if materia is None or _PERMISSAO_ITAQUI not in pagina.casefold():
        return ResultadoExtracaoNoticiasVagas((), True)

    if _SINAIS_CONCURSO.search(materia):
        return ResultadoExtracaoNoticiasVagas((), True)

    texto_normalizado = materia.casefold()
    if "fgtas/sine" not in texto_normalizado or "vaga" not in texto_normalizado:
        return ResultadoExtracaoNoticiasVagas((), True)

    cargos: list[str] = []
    vistos: set[str] = set()
    for linha in _preservar_linhas(seletor).splitlines():
        correspondencia = _PADRAO_ITAQUI.fullmatch(linha)
        if correspondencia is None:
            continue

        titulo = _texto_compacto(correspondencia.group("titulo"))
        if titulo is None or titulo.casefold() in vistos:
            continue

        vistos.add(titulo.casefold())
        cargos.append(titulo)

    if not cargos:
        correspondencia = _PADRAO_CARGO_TEXTO.search(materia)
        if correspondencia is not None:
            titulo = _texto_compacto(correspondencia.group("titulo"))
            if titulo is not None:
                cargos.append(titulo)

    publicado_em = _publicado_em(seletor)
    descricao = (
        f"Fonte: Prefeitura Municipal de Itaqui. Reprodução autorizada com citação "
        f"da fonte. Notícia original: {url}. {materia}"
    )

    return ResultadoExtracaoNoticiasVagas(
        tuple(
            _documento(
                titulo=cargo,
                descricao=descricao,
                url=url,
                empresa="Empregador não divulgado (FGTAS/Sine Itaqui)",
                localidade="Itaqui",
                regiao="RS",
                fonte_nome="Prefeitura Municipal de Itaqui",
                extrator="noticia_fgtas_sine_itaqui",
                publicado_em=publicado_em,
            )
            for cargo in cargos
        ),
        True,
    )


def _preservar_linhas(seletor: Selector) -> str:
    """Mantém quebras de linha para não confundir lista de cargos com texto."""

    conteudo = seletor.css(".post_content, .entry-content, article, main, #conteudo, .conteudo")
    if not conteudo:
        conteudo = seletor.css("body")

    return "\n".join(
        texto.strip() for texto in conteudo.xpath(".//text()").getall() if texto.strip()
    )
