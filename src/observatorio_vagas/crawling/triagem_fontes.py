"""Triagem de URLs do catálogo: separa o que inequivocamente não é página de vagas.

Só olhar a URL não prevê quais fontes rendem vaga (no teste de 10 mil fontes, 15,9%
das URLs "de carreira" renderam contra 11,5% das de blog). Por isso a triagem é
conservadora: exclui apenas o que **nunca** é uma página de vagas (arquivo PDF ou
imagem, categoria/tag de blog, artigo com data e sem palavra de vaga, lista do tipo
"10 carreiras para...") e deixa o resto para o agendamento por resultado.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

EXTENSOES_QUE_NAO_SAO_PAGINA = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4", ".mp3",
)  # fmt: skip

_PALAVRA_DE_VAGA = re.compile(
    r"vaga|emprego|oportunidade|trabalhe|carreira|career|job|estagi|recrut|talent|selecao|"
    r"seleção|processo-seletivo|candidat|contrat|selecion|abre-inscri|admite|admissao|admissão|trainee|trabalhar|aprendiz|jovem-talento",
    re.IGNORECASE,
)
_DATA_NO_CAMINHO = re.compile(r"/(19|20)\d{2}/(0?[1-9]|1[0-2])(/|$)")
_LISTA_EDITORIAL = re.compile(
    r"/\d{1,3}-(carreiras|profissoes|profissões|dicas|motivos|razoes|razões|formas|passos|"
    r"ideias|jeitos|cursos|erros|coisas|habilidades|curiosidades|vantagens|beneficios|"
    r"benefícios|areas|áreas|tendencias|tendências)\b",
    re.IGNORECASE,
)
_SECAO_EDITORIAL = re.compile(
    r"/(category|categoria|categorias|tag|tags|author|autor|feed|wp-json|wp-content|"
    r"wp-admin|page/\d+)(/|$)",
    re.IGNORECASE,
)
_CARREIRA = re.compile(
    r"trabalhe[-_ ]?conosco|trabalhe[-_ ]?na|carreira|career|vaga|oportunidade|jobs?\b|"
    r"talent|recrut|seja[-_ ]?(nosso|parte)|fa[cç]a[-_ ]?parte|work[-_ ]?with|junte[-_ ]?se|"
    r"emprego|estagi|candidat|banco[-_ ]de[-_ ]talentos|trabalhe",
    re.IGNORECASE,
)
_EDITORIAL_OU_PRODUTO = re.compile(
    r"/(blog|noticias?|not[ií]cias?|artigos?|guias?|dicas|cursos?|produtos?|loja|shop|"
    r"sobre|imprensa|institucional|contato|servicos?|eventos?|concursos?|editais?|licitacoes?)"
    r"(/|$)",
    re.IGNORECASE,
)
# Loja, produto ou curso nunca é página de vagas (etiquetasamericana.com.br/produto/...,
# profec.com.br/curso/curso-de-operador-de-colheitadeira).
_LOJA_OU_CURSO = re.compile(
    r"/(produtos?|products?|loja|shop|cursos?|pos-?graduacao|posgraduacao|graduacao|"
    r"especializacao)(/|$)|[?&]product_cat=",
    re.IGNORECASE,
)
# Sites de vagas do exterior: na rodada de 06/10/2026, 9.591 das 9.773 vagas do
# emploive.com foram barradas como fora do Brasil; drjobpro e disneycareers idem.
DOMINIOS_FORA_DO_BRASIL = ("emploive.com", "drjobpro.com", "disneycareers.com")
# Agregadores que a coleta lê inteiros a partir de qualquer entrada: com 63 entradas
# do empregandobrasil, 5 leram ~10 mil anúncios cada, quase sempre os mesmos. Basta uma.
# (jobijoba, BNE e trabalhabrasil NÃO entram: cada entrada traz vagas diferentes.)
# O empregandobrasil saiu: em 06/10/2026 foi bloqueado como fonte não autorizada.
DOMINIOS_LIDOS_INTEIROS: tuple[str, ...] = ()
MOTIVO_ENTRADA_REPETIDA = "entrada repetida de agregador lido inteiro (fica uma só)"
# Página que descreve uma ocupação (salário médio, atribuições), sem vaga.
_DESCRICAO_DE_CARGO = re.compile(r"^(www\.)?cargos\.com\.br/cargo/", re.IGNORECASE)


def motivo_de_exclusao(url: str) -> str | None:
    """Motivo pelo qual a URL jamais seria uma página de vagas, ou ``None``."""

    partes = urlsplit(url)
    caminho = partes.path

    if caminho.casefold().endswith(EXTENSOES_QUE_NAO_SAO_PAGINA):
        return "arquivo (PDF, documento ou imagem), não é página"
    if _SECAO_EDITORIAL.search(caminho) and not _PALAVRA_DE_VAGA.search(caminho):
        return "categoria, tag, autor ou feed de blog"
    if _LISTA_EDITORIAL.search(caminho):
        return "artigo de lista editorial (ex.: '10 carreiras para...')"
    if _DATA_NO_CAMINHO.search(caminho) and not _PALAVRA_DE_VAGA.search(caminho):
        return "artigo datado sem nenhuma palavra de vaga na URL"
    if _LOJA_OU_CURSO.search(caminho + ("?" + partes.query if partes.query else "")) and not (
        _PALAVRA_DE_VAGA.search(caminho)
    ):
        return "produto, loja ou curso, não é página de vagas"
    host = (partes.hostname or "").casefold().removeprefix("www.")
    if any(host == d or host.endswith("." + d) for d in DOMINIOS_FORA_DO_BRASIL):
        return "site de vagas do exterior (vagas barradas como fora do Brasil)"
    if _DESCRICAO_DE_CARGO.search((partes.hostname or "") + caminho):
        return "descrição de cargo (salário médio, atribuições), sem vaga"
    return None


def dominio_lido_inteiro(url: str) -> str | None:
    """Domínio do agregador lido inteiro a partir de qualquer entrada, ou None."""

    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    return next((d for d in DOMINIOS_LIDOS_INTEIROS if host == d or host.endswith("." + d)), None)


def tem_palavra_de_vaga(url: str) -> bool:
    """O caminho fala de vaga, seleção, aprendiz etc. ("senac-abre-processo-seletivo")."""

    return bool(_PALAVRA_DE_VAGA.search(urlsplit(url).path))


MOTIVO_EDITORIAL_SEM_ANUNCIO = "notícia, curso, produto ou institucional que nunca rendeu vaga"


def classificar(url: str) -> str:
    """Classe grosseira da URL, só para relatório: carreira, editorial, home ou outra."""

    partes = urlsplit(url)
    caminho = partes.path

    if _EDITORIAL_OU_PRODUTO.search(caminho) and not _CARREIRA.search(caminho):
        return "editorial_ou_produto"
    if _CARREIRA.search(caminho) or _CARREIRA.search(partes.hostname or ""):
        return "carreira"
    if caminho.strip("/") == "":
        return "home"
    return "outra"
