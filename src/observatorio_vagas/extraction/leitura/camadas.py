"""Inventário de todas as camadas de uma página de vaga.

Antes de preencher qualquer campo, a página inteira é lida e separada em
camadas: dados estruturados, JSON embutido, meta tags, cabeçalho (título e
rótulos), corpo por seção, ações (candidatura, e-mail) e rodapé.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from lxml import etree
from lxml import html as lxml_html

from observatorio_vagas.extraction.leitura.json_embutido import extrair_estados_embutidos
from observatorio_vagas.extraction.leitura.texto_livre import normalizar

_PARSER = lxml_html.HTMLParser(encoding="utf-8")

# Títulos de seção reconhecidos -> nome canônico da seção.
SECOES = {
    "sobre_empresa": r"sobre (a|nos|a empresa|o grupo|a companhia)|quem somos"
    r"|about (us|the company)|a empresa",
    "atividades": r"atividades|responsabilidades|atribuicoes|o que voce (vai|ira) fazer|desafios|"
    r"responsibilities|what you.?ll do|descricao da vaga|sobre a vaga|a vaga",
    "requisitos": r"requisitos|qualificacoes|pre.?requisitos|o que (buscamos|esperamos)|"
    r"requirements|qualifications|perfil",
    "diferenciais": r"diferenciais|desejavel|desejaveis|sera um diferencial|nice to have|plus",
    "beneficios": r"beneficios|o que oferecemos|benefits|perks",
    "jornada": r"jornada|horario|escala|carga horaria",
    "local": r"local( de trabalho)?|localizacao|endereco|onde (voce vai )?trabalhar",
    "remuneracao": r"remuneracao|salario|faixa salarial|bolsa",
}

# Rótulos de cabeçalho "Rótulo: valor" que valem a pena separar.
_ROTULO_VALOR = re.compile(
    r"^\s*(?P<rotulo>[A-Za-zÀ-ÿ /()]{3,40}?)\s*[:：]\s*(?P<valor>.{1,120}?)\s*$"
)

# Texto de interface que não pertence à vaga.
# Só linhas que são inteiramente interface; "Elaborar o menu" continua.
LIXO_INTERFACE = re.compile(
    r"^\W*(aceitar( todos os)? cookies.{0,40}|usamos cookies.{0,80}|politica de privacidade|"
    r"⭐?\s*destaque|[✕×]|compartilhe esta vaga|candidate-se agora|faca login.{0,20}|"
    r"entrar com .{0,20}|voltar para (as )?vagas|ver todas as vagas|menu|pesquisar vagas)\W*$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Secao:
    nome: str
    titulo: str
    texto: str


@dataclass(slots=True)
class InventarioPagina:
    """Tudo o que foi encontrado na página, por camada."""

    url: str
    json_ld: list[dict[str, Any]] = field(default_factory=list)
    json_embutido: dict[str, Any] = field(default_factory=dict)
    json_embutido_ilegivel: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    titulo_h1: str | None = None
    titulo_documento: str | None = None
    rotulos: list[tuple[str, str]] = field(default_factory=list)
    secoes: list[Secao] = field(default_factory=list)
    texto_corpo: str = ""
    texto_rodape: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    classes_cabecalho: dict[str, str] = field(default_factory=dict)

    def camadas(self) -> list[str]:
        nomes = []
        if self.json_ld:
            nomes.append("json_ld")
        if self.json_embutido:
            nomes.append("json_embutido")
        if self.meta:
            nomes.append("meta")
        if self.titulo_h1 or self.rotulos or self.classes_cabecalho:
            nomes.append("cabecalho")
        if self.secoes or self.texto_corpo:
            nomes.append("corpo")
        if self.links or self.emails:
            nomes.append("acoes")
        if self.texto_rodape:
            nomes.append("rodape")
        return nomes

    def secao(self, nome: str) -> Secao | None:
        return next((secao for secao in self.secoes if secao.nome == nome), None)

    def rotulo(self, *padroes: str) -> tuple[str, str] | None:
        for rotulo, valor in self.rotulos:
            rotulo_n = normalizar(rotulo)
            if any(re.search(padrao, rotulo_n) for padrao in padroes):
                return rotulo, valor
        return None


def _texto(elemento: Any) -> str:
    return " ".join(" ".join(elemento.itertext()).split())


def _job_postings(dado: Any) -> list[dict[str, Any]]:
    """JobPosting em qualquer nível do JSON-LD (@graph, listas, mainEntity)."""

    encontrados: list[dict[str, Any]] = []
    # Fila (e não pilha): preserva a ordem em que aparecem na página.
    pilha = deque([dado])
    while pilha:
        atual = pilha.popleft()
        if isinstance(atual, list):
            pilha.extend(atual)
        elif isinstance(atual, dict):
            tipo = atual.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if "JobPosting" in tipos:
                encontrados.append(atual)
            pilha.extend(
                v
                for k, v in atual.items()
                if k in {"@graph", "mainEntity", "item", "itemListElement"}
            )
    return encontrados


def classificar_secao(titulo: str) -> str | None:
    titulo_n = normalizar(titulo).strip(" :-–")
    if len(titulo_n) > 60:
        return None
    for nome, padrao in SECOES.items():
        if re.fullmatch(rf"(\W*\w{{0,3}}\W*)?({padrao})\b.{{0,25}}", titulo_n):
            return nome
    return None


def secoes_do_texto(texto: str) -> list[Secao]:
    """Divide um texto já sem HTML em seções pelos subtítulos em linhas próprias."""

    secoes: list[Secao] = []
    atual = Secao("introducao", "", "")
    partes: list[str] = []

    for linha in texto.split("\n"):
        nome = classificar_secao(linha) if 0 < len(linha.strip()) <= 60 else None
        if nome:
            atual.texto = "\n".join(partes).strip()
            if atual.texto or atual.titulo:
                secoes.append(atual)
            atual, partes = Secao(nome, linha.strip(), ""), []
        else:
            partes.append(linha)

    atual.texto = "\n".join(partes).strip()
    if atual.texto or atual.titulo:
        secoes.append(atual)
    return secoes


def montar_inventario(
    corpo: bytes, *, url: str, codificacao: str | None = None
) -> InventarioPagina:
    """Lê todas as camadas de uma página HTML."""

    inventario = InventarioPagina(url=url)
    try:
        html = corpo.decode(codificacao or "utf-8", errors="replace")
    except LookupError:
        html = corpo.decode("utf-8", errors="replace")

    inventario.json_embutido, inventario.json_embutido_ilegivel = extrair_estados_embutidos(html)

    try:
        raiz = lxml_html.document_fromstring(html.encode("utf-8"), parser=_PARSER)
    except (etree.ParserError, ValueError):
        return inventario

    for script in raiz.xpath('//script[@type="application/ld+json"]'):
        try:
            inventario.json_ld.extend(_job_postings(json.loads(script.text or "")))
        except json.JSONDecodeError:
            continue

    for meta in raiz.xpath("//meta[@content]"):
        chave = meta.get("property") or meta.get("name")
        if chave and (chave.startswith("og:") or chave in {"description", "author"}):
            inventario.meta.setdefault(chave, meta.get("content", "").strip())
    for link in raiz.xpath('//link[@rel="canonical"][@href]'):
        inventario.meta["canonical"] = link.get("href")

    titulos = raiz.xpath("//title")
    inventario.titulo_documento = _texto(titulos[0]) if titulos else None

    # Remove o que não é conteúdo antes de ler o texto.
    for inutil in raiz.xpath("//script|//style|//noscript|//template|//svg"):
        inutil.drop_tree()

    h1 = raiz.xpath("//h1")
    inventario.titulo_h1 = _texto(h1[0]) if h1 else None

    # Cabeçalhos com rótulos em classes (Lever: location, commitment...).
    for elemento in raiz.xpath('//*[contains(@class, "posting-categor")]//*[@class]'):
        classes = elemento.get("class", "").split()
        texto = _texto(elemento)
        for classe in classes:
            if (
                classe in {"location", "department", "commitment", "workplaceTypes", "team"}
                and texto
            ):
                inventario.classes_cabecalho.setdefault(classe, texto.rstrip(" /"))

    rodapes = raiz.xpath("//footer")
    inventario.texto_rodape = " ".join(_texto(rodape) for rodape in rodapes)
    for rodape in rodapes:
        rodape.drop_tree()
    for navegacao in raiz.xpath("//nav|//header[not(.//h1)]"):
        navegacao.drop_tree()

    for ancora in raiz.xpath("//a[@href]"):
        href = ancora.get("href", "").strip()
        if href.startswith("mailto:"):
            inventario.emails.append(href[7:].split("?")[0])
        elif href:
            inventario.links.append((href, _texto(ancora)))

    # Rótulos "Rótulo: valor" em itens curtos (li, dt/dd, spans).
    for item in raiz.xpath("//li|//p|//dt|//span|//div[not(*)]"):
        texto = _texto(item)
        if 3 < len(texto) <= 160:
            correspondencia = _ROTULO_VALOR.match(texto)
            if correspondencia:
                inventario.rotulos.append(
                    (
                        correspondencia.group("rotulo").strip(),
                        correspondencia.group("valor").strip(),
                    )
                )
    for termo in raiz.xpath("//dt"):
        definicao = termo.getnext()
        if definicao is not None and definicao.tag == "dd":
            inventario.rotulos.append((_texto(termo).rstrip(":"), _texto(definicao)))

    from observatorio_vagas.extraction.normalizacao_vaga import _limpar_html_formatado

    corpo_html = lxml_html.tostring(raiz.find("body") if raiz.find("body") is not None else raiz)
    inventario.texto_corpo = _limpar_html_formatado(corpo_html.decode("utf-8", "replace")) or ""
    inventario.secoes = secoes_do_texto(inventario.texto_corpo)
    return inventario
