"""Vagas próprias em artigos institucionais e seções de newsletters.

Domínios e containers são explícitos: uma mudança de layout produz zero
vagas, nunca o texto inteiro da página como fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from parsel import Selector

from observatorio_vagas.crawling.filtro_conteudo import eh_concurso_publico

PERFIS = {
    "www.dataprivacybr.org": (
        "Data Privacy Brasil",
        "section.section--single-content article",
        "h1.news__item__title",
    ),
    "dataprivacybr.org": (
        "Data Privacy Brasil",
        "section.section--single-content article",
        "h1.news__item__title",
    ),
    "ok.org.br": ("Open Knowledge Brasil", "article section.conteudo", "main h1"),
    "internetlab.org.br": ("InternetLab", ".single-content__text", "h1.single-header__title"),
    "news.fiquemsabendo.com.br": ("Fiquem Sabendo", ".body.markup", "h1.post-title"),
    "nupef.org.br": (
        "Instituto Nupef",
        ".jet-listing-dynamic-field__content:has(p)",
        "p.elementor-heading-title",
    ),
    "www.transparencia.org.br": (
        "Transparência Brasil",
        "article > section:not([class])",
        "h1",
    ),
}

# Páginas de carreira de empresas que exibem cartões de vagas diretamente no
# HTML. Cada perfil é explícito para que uma mudança de layout gere zero
# anúncios, em vez de transformar conteúdo institucional em vaga.
PERFIS_CARREIRAS_ESTATICAS = {
    "www.sejapagmaisbrasil.com": "PagMais",
}

# Perfis JobConvo já validados. A página de cada empresa é identificada pelo
# parâmetro career_page, não pelo domínio compartilhado da plataforma.
PERFIS_JOBCONVO = {
    "6476f49c-0260-4215-bd5e-e67da2dfc1a9": ("Lear", r"\blear\b", None),
    "ade2a055-b11d-4ae3-b1c0-e95fb5065909": (
        "Nova Página",
        r"nova\s+p[áa]gina",
        ("CAJAMAR", "SP"),
    ),
}
_CARGO = re.compile(r"\bvaga(?:\s+(?:de|para)\s+|\s*:\s*)(.+)", re.I)
# As licenças destas fontes cobrem conteúdo próprio, não anúncios de terceiros.
_EMPREGADOR_PROPRIO = {
    "nupef.org.br": r"(?:instituto\s+)?nupef",
    "www.transparencia.org.br": r"transpar[êe]ncia\s+brasil",
}
_ENCERRADA = re.compile(r"inscri[çc][õo]es\s+encerradas|vaga\s+encerrada", re.I)
_MESES = dict(
    zip(
        (
            "janeiro",
            "fevereiro",
            "março",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
        ),
        range(1, 13),
        strict=True,
    )
)


@dataclass(frozen=True, slots=True)
class ResultadoOrganizacoes:
    """Reconhecer o domínio impede fallback inseguro mesmo sem resultados."""

    vagas: tuple[dict[str, Any], ...]
    dominio_reconhecido: bool


def _texto(no: Selector) -> str:
    return " ".join(
        no.xpath(".//text()[not(ancestor::script) and not(ancestor::style)]").getall()
    ).strip()


def _publicado(seletor: Selector) -> str | None:
    valor = seletor.css('meta[property="article:published_time"]::attr(content)').get()
    valor = valor or seletor.css("time::attr(datetime)").get()
    if not valor:
        # Yoast inclui datas de Article, que não são JobPosting.
        for script in seletor.css('script[type="application/ld+json"]::text').getall():
            try:
                dado = json.loads(script)
            except (ValueError, TypeError):
                continue
            objetos = dado.get("@graph", [dado]) if isinstance(dado, dict) else []
            if not isinstance(objetos, list):
                continue
            for objeto in objetos:
                if isinstance(objeto, dict) and objeto.get("@type") in (
                    "Article",
                    "NewsArticle",
                    "WebPage",
                ):
                    valor = objeto.get("datePublished")
                    break
    try:
        return date.fromisoformat(valor[:10]).isoformat() if isinstance(valor, str) else None
    except ValueError:
        return None


def _prazo(texto: str, publicado: str | None) -> str | None:
    # Não confundir ano de conclusão do curso com prazo de candidatura.
    for trecho in re.finditer(
        r"(?:inscri[çc][õo]es|candidaturas|prazo)[^.!?]{0,100}?at[ée]\s+(?:o dia\s+)?"
        r"(?:o\s+)?(?:dia\s+)?(\d{1,2})(?:/(\d{1,2})(?:/(\d{4}))?"
        r"|\s+de\s+(\w+)(?:\s+de\s+(\d{4}))?)",
        texto,
        re.I,
    ):
        dia, mes, ano, mes_nome, ano_nome = trecho.groups()
        numero_mes = int(mes) if mes else _MESES.get((mes_nome or "").casefold())
        numero_ano = ano or ano_nome or (publicado[:4] if publicado else None)
        if numero_mes and numero_ano:
            try:
                return date(int(numero_ano), numero_mes, int(dia)).isoformat()
            except ValueError:
                continue
    return None


def _cargo(texto: str) -> str | None:
    encontrado = _CARGO.search(texto)
    if not encontrado or len(texto) > 180 or _ENCERRADA.search(texto):
        return None
    titulo = encontrado[1].split(":", 1)[0].strip(" .")
    return titulo if len(titulo) >= 4 else None


def extrair_vagas_organizacoes(
    corpo: bytes, *, url: str, codificacao: str = "utf-8"
) -> ResultadoOrganizacoes:
    """Separa cargos e preserva evidências sem executar JS ou seguir formulários."""
    dominio = (urlsplit(url).hostname or "").casefold()
    perfil = PERFIS.get(dominio)
    if perfil is None:
        return ResultadoOrganizacoes((), False)
    vazio = ResultadoOrganizacoes((), True)
    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    empresa, css_corpo, css_titulo = perfil
    containers = seletor.css(css_corpo)
    titulos = seletor.css(css_titulo)
    if not containers or not titulos:
        return vazio
    titulo_pagina = _texto(titulos[0])
    if _ENCERRADA.search(titulo_pagina):
        return vazio
    container = containers[0]
    empregador = _EMPREGADOR_PROPRIO.get(dominio)
    if empregador:
        contexto = " ".join((titulo_pagina + " " + _texto(container)).split())
        # Menção incidental à organização numa notícia não identifica o empregador.
        contratacao = rf"\b{empregador}\s+(?:est[aá]\s+)?(?:abre|busca|contrata|seleciona)\b"
        if not re.search(contratacao, contexto, re.I):
            return vazio
    filhos = list(container.xpath("./*"))
    secoes: list[tuple[str, list[Selector]]] = []
    inicios: list[tuple[int, str]] = []
    newsletter = dominio == "news.fiquemsabendo.com.br"
    for indice, filho in enumerate(filhos):
        texto = _texto(filho)
        cabecalho = filho.root.tag in {"h2", "h3", "h4"}
        destaque = filho.root.tag == "p" and bool(filho.css("strong, b"))
        cargo = _cargo(texto) if cabecalho or destaque else None
        if cargo and (not newsletter or cabecalho):
            inicios.append((indice, cargo))
    if inicios:
        for indice, cargo in inicios:
            fim = next(
                (
                    i
                    for i in range(indice + 1, len(filhos))
                    if filhos[i].root.tag in {"h2", "h3", "h4"}
                    or any(i == inicio for inicio, _ in inicios)
                ),
                len(filhos),
            )
            # O contexto institucional da InternetLab contém prazo/modelo comuns.
            comuns = filhos[: inicios[0][0]] if dominio == "internetlab.org.br" else []
            secoes.append((cargo, comuns + filhos[indice + 1 : fim]))
    elif not newsletter:
        cargo = _cargo(titulo_pagina)
        if cargo:
            secoes.append((cargo, [container]))
    publicado = _publicado(seletor)
    vagas: dict[str, dict[str, Any]] = {}
    for cargo, nos in secoes:
        descricao = " ".join(_texto(no) for no in nos).strip()
        if len(descricao) < 80 or _ENCERRADA.search(descricao):
            continue
        if eh_concurso_publico(url=url, conteudo=cargo + " " + descricao):
            continue
        identificador = sha256(f"{url}\n{cargo.casefold()}".encode()).hexdigest()
        documento: dict[str, Any] = {
            "@type": "JobPosting",
            "identifier": identificador,
            "title": cargo,
            "description": descricao,
            "url": url,
            "hiringOrganization": {"@type": "Organization", "name": empresa},
        }
        if publicado:
            documento["datePosted"] = publicado
        prazo = _prazo(descricao, publicado)
        if prazo:
            documento["validThrough"] = prazo
        for no in nos:
            for link in no.css("a[href]"):
                if re.search(r"candidat|inscri|formul[aá]rio|acesse aqui", _texto(link), re.I):
                    destino = urljoin(url, link.attrib["href"])
                    if urlsplit(destino).scheme in {"https", "http"}:
                        documento.setdefault("_observatorio_apply_url", destino)
        vagas.setdefault(identificador, documento)
    return ResultadoOrganizacoes(tuple(vagas.values()), True)


def extrair_vagas_carreiras_estaticas(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoOrganizacoes:
    """Extrai cartões de vagas de páginas de carreira previamente validadas."""

    dominio = (urlsplit(url).hostname or "").casefold()
    empresa = PERFIS_CARREIRAS_ESTATICAS.get(dominio)

    if empresa is None:
        return ResultadoOrganizacoes((), False)

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    vagas: list[dict[str, Any]] = []

    # Na PagMais, cada título está em h3. A descrição é o primeiro bloco irmão
    # do cartão que o contém. A estrutura foi verificada na página pública.
    for cabecalho in seletor.css("h3"):
        titulo = _texto(cabecalho)
        descricao = _texto(cabecalho.xpath("../../following-sibling::*[1]"))

        if not titulo or len(descricao) < 80:
            continue

        identificador = sha256(f"{url}\n{titulo.casefold()}".encode()).hexdigest()
        vagas.append(
            {
                "@type": "JobPosting",
                "identifier": identificador,
                "title": titulo,
                "description": descricao,
                "url": url,
                "jobLocationType": "TELECOMMUTE",
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "@type": "PostalAddress",
                        "addressCountry": "BR",
                    },
                },
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": empresa,
                },
            }
        )

    return ResultadoOrganizacoes(tuple(vagas), True)


def extrair_vagas_teleperformance(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoOrganizacoes:
    """Extrai detalhes públicos do portal de carreiras da Teleperformance.

    A descrição não é inserida como texto visível na resposta: ela é mantida
    codificada no valor do campo ``#dh`` e renderizada pelo JavaScript do
    portal. Lemos somente esse conteúdo já presente no HTML baixado.
    """

    partes = urlsplit(url)
    if partes.hostname != "portaldevagas.teleperformance.com.br":
        return ResultadoOrganizacoes((), False)

    if partes.path.casefold().rstrip("/") != "/vagacandidatura/vagasdetail":
        return ResultadoOrganizacoes((), True)

    if not parse_qs(partes.query).get("idVaga"):
        return ResultadoOrganizacoes((), True)

    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    cabecalhos = seletor.css("h1.vagaDetails__title")
    titulo = " ".join(_texto(cabecalhos[0]).split()) if cabecalhos else ""
    descricao_codificada = seletor.css("input#dh::attr(value)").get()
    if descricao_codificada is None:
        return ResultadoOrganizacoes((), True)

    conteudo = Selector(text=unescape(descricao_codificada))
    descricao = " ".join(_texto(conteudo).split())
    if len(titulo) < 4 or len(descricao) < 80:
        return ResultadoOrganizacoes((), True)

    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "identifier": sha256(url.encode()).hexdigest(),
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": url,
        "hiringOrganization": {"@type": "Organization", "name": "Teleperformance"},
    }

    # O portal informa o local no texto da vaga, por exemplo
    # ``Local: Lapa – São Paulo/SP``. A região antes do travessão é bairro,
    # portanto a última parte é a cidade que deve ser publicada.
    local = re.search(r"\bLocal\s*:\s*([^|;\n]{1,120}?)/\s*([A-Z]{2})\b", descricao, re.I)
    if local:
        cidade = re.split(r"\s*[–—-]\s*", local[1].strip())[-1].strip(" .")
        endereco: dict[str, str] = {
            "@type": "PostalAddress",
            "addressCountry": "BR",
            "addressRegion": local[2].upper(),
        }
        if cidade:
            endereco["addressLocality"] = cidade
        documento["jobLocation"] = {"@type": "Place", "address": endereco}

    # Não inventamos ``datePosted``: esse portal não publica a data da vaga.
    return ResultadoOrganizacoes((documento,), True)


def extrair_vagas_jobconvo(
    corpo: bytes,
    *,
    url: str,
    codificacao: str = "utf-8",
) -> ResultadoOrganizacoes:
    """Extrai detalhes JobConvo mesmo quando o JSON-LD público é malformado."""

    partes = urlsplit(url)
    if partes.hostname != "app.jobconvo.com":
        return ResultadoOrganizacoes((), False)
    carreira = parse_qs(partes.query).get("career_page", [""])[0]
    perfil = PERFIS_JOBCONVO.get(carreira)
    if perfil is None:
        return ResultadoOrganizacoes((), False)

    if not partes.path.startswith("/job/"):
        return ResultadoOrganizacoes((), True)

    empresa, padrao_empresa, localizacao_padrao = perfil
    seletor = Selector(text=corpo.decode(codificacao, errors="replace"))
    titulo_bruto = seletor.css('meta[property="og:title"]::attr(content)').get() or ""
    descricao = seletor.css('meta[property="og:description"]::attr(content)').get() or ""
    titulo = re.sub(r"\s+-\s+" + re.escape(empresa) + r"\s*$", "", titulo_bruto, flags=re.I)
    titulo = re.sub(r"\s+-\s+LEAR\b.*$", "", titulo, flags=re.I)
    titulo = " ".join(titulo.split())
    descricao = " ".join(descricao.split())
    # A descrição pode mencionar uma empresa parceira; o título da vaga é a
    # evidência que separa o empregador próprio de uma vaga de terceiros.
    if len(titulo) < 4 or len(descricao) < 80 or not re.search(padrao_empresa, titulo_bruto, re.I):
        return ResultadoOrganizacoes((), True)

    html = corpo.decode(codificacao, errors="replace")
    identificador = sha256(f"{url}\n{titulo.casefold()}".encode()).hexdigest()
    documento: dict[str, Any] = {
        "@type": "JobPosting",
        "identifier": identificador,
        "title": titulo,
        "description": descricao,
        "url": url,
        "_observatorio_apply_url": url,
        "hiringOrganization": {"@type": "Organization", "name": empresa},
    }
    for campo, chave in (("datePosted", "datePosted"), ("validThrough", "validThrough")):
        encontrado = re.search(rf'"{campo}"\s*:\s*"(\d{{4}}-\d{{2}}-\d{{2}})', html)
        if encontrado:
            documento[chave] = encontrado[1]
    tipo = re.search(r'"employmentType"\s*:\s*"([A-Z_]+)"', html)
    if tipo:
        documento["employmentType"] = tipo[1]
    cidade = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', html)
    estado = re.search(r'"addressRegion"\s*:\s*"([A-Z]{2})"', html)
    nome_cidade = cidade[1] if cidade else (localizacao_padrao or (None, None))[0]
    sigla_estado = estado[1] if estado else (localizacao_padrao or (None, None))[1]
    if nome_cidade or sigla_estado:
        endereco: dict[str, str] = {"@type": "PostalAddress", "addressCountry": "BR"}
        if nome_cidade:
            endereco["addressLocality"] = nome_cidade
        if sigla_estado:
            endereco["addressRegion"] = sigla_estado
        documento["jobLocation"] = {"@type": "Place", "address": endereco}
    return ResultadoOrganizacoes((documento,), True)
