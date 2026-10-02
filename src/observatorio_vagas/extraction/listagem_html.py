"""Vagas em páginas HTML próprias que nenhum extrator específico reconhece.

Só entra quando os outros extratores não acharam nada. Três padrões:

1. tabela com coluna "Cargo", "Vaga" ou "Função": cada linha é uma vaga;
2. bloco depois de um título "Vagas em aberto/abertas/disponíveis": cada item
   (subtítulo, item de lista, link ou destaque) é uma vaga, até o próximo título
   do mesmo nível;
3. página de detalhe com "Descrição da vaga": uma vaga, título do cabeçalho.

Para evitar falsos positivos, os padrões 2 e 3 exigem o título que anuncia as
vagas, e itens genéricos ("Candidatar-se", "Ver vaga") são descartados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import etree
from lxml import html as lxml_html

from observatorio_vagas.extraction.leitura.texto_livre import normalizar

_PARSER = lxml_html.HTMLParser(encoding="utf-8")

_TITULO_LISTA = re.compile(
    r"^\W*(confira (as |nossas )?)?(nossas )?(vagas?|oportunidades)"
    r"( (em aberto|abertas?|disponive(l|is)|ativas))?( no momento| agora)?\W*$"
    r"|^\W*(vagas|oportunidades) (em aberto|abertas?|disponive(l|is))\b.{0,40}$"
)
_TITULO_DETALHE = re.compile(r"^\W*(descricao|detalhes) da vaga\W*$")
_COLUNA_CARGO = re.compile(r"^(cargo|vaga|funcao|posicao|oportunidade)s?$")
_GENERICO = re.compile(
    r"^(candidatar-?se|quero me candidatar|ver (a )?vaga|ver detalhes|saiba mais|"
    r"inscreva-?se|enviar curriculo|cadastre.*|banco de talentos|clique.*|"
    r"todas as vagas|ver (todas as )?vagas( abertas)?|vagas( abertas)?|filtrar.*|"
    r"area|cidade|local|modelo|cargo|tipo|contrato|\d+ (vagas?|oportunidades?).*)\W*$"
)
_CIDADE_UF = re.compile(r"\b([A-ZÀ-Ý][\wÀ-ÿ'. ]{2,40})\s*[-/–]\s*([A-Z]{2})\b")
_CABECALHOS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(frozen=True, slots=True)
class ResultadoListagemHtml:
    vagas: tuple[dict[str, Any], ...]


def _texto(elemento: Any) -> str:
    return " ".join(" ".join(elemento.itertext()).split())


def _parece_titulo(texto: str) -> bool:
    texto_n = normalizar(texto).strip(" -–:•")
    if not 3 <= len(texto) <= 90 or len(texto.split()) > 12:
        return False
    if _GENERICO.match(texto_n) or _TITULO_LISTA.match(texto_n):
        return False
    # Frase de descrição, não título.
    return not (texto.rstrip().endswith((".", "!", "?", ":")) and len(texto.split()) > 6)


def _vaga(titulo: str, *, url_pagina: str, href: str | None, texto_card: str) -> dict[str, Any]:
    destino = urljoin(url_pagina, href) if href else url_pagina
    if urlsplit(destino).hostname != urlsplit(url_pagina).hostname or destino.startswith(
        ("mailto:", "javascript:")
    ):
        destino = url_pagina
    titulo = titulo.strip(" -–:•")
    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "identifier": sha256(f"{url_pagina}|{normalizar(titulo)}".encode()).hexdigest()[:40],
        "title": titulo,
        "description": texto_card if len(texto_card) > len(titulo) + 20 else "",
        "url": destino,
        "_observatorio_apply_url": destino,
        "_observatorio_extrator": "listagem_html",
    }
    local = _CIDADE_UF.search(texto_card)
    if local:
        documento["jobLocation"] = {
            "@type": "Place",
            "address": {"addressLocality": local.group(1).strip(), "addressRegion": local.group(2)},
        }
    return documento


def _card(item: Any, *, limite: int = 600) -> Any:
    """Sobe até o maior ancestral que ainda contém só esta vaga."""

    atual = item
    while atual.getparent() is not None:
        pai = atual.getparent()
        if len(_texto(pai)) > limite or len(pai) > 12:
            break
        atual = pai
    return atual


def _tabelas(raiz: Any, url: str) -> list[dict[str, Any]]:
    vagas = []
    for tabela in raiz.iter("table"):
        linhas = tabela.xpath(".//tr")
        if len(linhas) < 2:
            continue
        cabecalho = [normalizar(_texto(celula)) for celula in linhas[0].xpath("./th|./td")]
        coluna = next((i for i, nome in enumerate(cabecalho) if _COLUNA_CARGO.match(nome)), None)
        if coluna is None:
            continue
        for linha in linhas[1:]:
            celulas = linha.xpath("./td|./th")
            if len(celulas) <= coluna:
                continue
            titulo = _texto(celulas[coluna])
            if not _parece_titulo(titulo):
                continue
            links = linha.xpath(".//a[@href]")
            vagas.append(
                _vaga(titulo, url_pagina=url, href=links[0].get("href") if links else None, texto_card=_texto(linha))
            )
    return vagas


def _depois_do_titulo(raiz: Any, url: str) -> list[dict[str, Any]]:
    """Itens que vêm depois de "Vagas em aberto", até o próximo título igual ou maior."""

    vagas: list[dict[str, Any]] = []
    for marcador in raiz.iter(*_CABECALHOS, "strong", "p", "span", "div"):
        if len(marcador) > 3 or not _TITULO_LISTA.match(normalizar(_texto(marcador))):
            continue
        nivel = int(marcador.tag[1]) if marcador.tag in _CABECALHOS else 7
        vistos: set[str] = set()

        for elemento in marcador.itersiblings() if marcador.getnext() is not None else marcador.getparent().itersiblings():
            if elemento.tag in _CABECALHOS and int(elemento.tag[1]) <= nivel and _texto(elemento):
                if not _parece_titulo(_texto(elemento)) or elemento.tag == "h1":
                    break
            candidatos = [elemento] if elemento.tag in (*_CABECALHOS, "li", "a", "strong") else []
            candidatos += elemento.xpath(".//h2|.//h3|.//h4|.//h5|.//li|.//strong|.//a")
            for candidato in candidatos:
                titulo = _texto(candidato)
                chave = normalizar(titulo)
                if chave in vistos or not _parece_titulo(titulo):
                    continue
                # Um link só vale se o texto parecer título de vaga, não "Ver vaga".
                card = _card(candidato)
                links = [candidato] if candidato.tag == "a" else card.xpath(".//a[@href]")
                vistos.add(chave)
                vagas.append(
                    _vaga(
                        titulo,
                        url_pagina=url,
                        href=links[0].get("href") if links else None,
                        texto_card=_texto(card),
                    )
                )
        if vagas:
            break
    return vagas


def _detalhe(raiz: Any, url: str) -> list[dict[str, Any]]:
    marcador = next(
        (e for e in raiz.iter(*_CABECALHOS, "strong", "b") if _TITULO_DETALHE.match(normalizar(_texto(e)))),
        None,
    )
    if marcador is None:
        return []
    titulos = [t for t in raiz.xpath("//h1|//h2") if _parece_titulo(_texto(t)) and t is not marcador]
    if not titulos:
        return []
    partes = []
    for elemento in marcador.itersiblings():
        partes.append(_texto(elemento))
    descricao = "\n".join(p for p in partes if p) or _texto(marcador.getparent())
    vaga = _vaga(_texto(titulos[0]), url_pagina=url, href=None, texto_card=descricao)
    vaga["description"] = descricao
    return [vaga]


def extrair_vagas_listagem_html(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
    detalhe: bool = False,
) -> ResultadoListagemHtml:
    """Vagas de tabelas, blocos "Vagas em aberto" ou página de detalhe simples."""

    try:
        texto = corpo.decode(codificacao or "utf-8", errors="replace")
        raiz = lxml_html.document_fromstring(texto.encode("utf-8"), parser=_PARSER)
    except (etree.ParserError, ValueError, LookupError):
        return ResultadoListagemHtml(())

    for inutil in raiz.xpath("//script|//style|//noscript|//nav|//footer|//header[not(.//h1)]"):
        inutil.drop_tree()

    vagas = (_detalhe(raiz, url) if detalhe else []) or _tabelas(raiz, url) or _depois_do_titulo(raiz, url)

    unicas: dict[str, dict[str, Any]] = {}
    for vaga in vagas:
        unicas.setdefault(vaga["identifier"], vaga)
    return ResultadoListagemHtml(tuple(unicas.values()))
