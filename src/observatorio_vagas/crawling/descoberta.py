"""Descoberta conservadora de links candidatos a páginas de vagas."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from unicodedata import combining, normalize
from urllib.parse import urldefrag, urljoin, urlsplit

from scrapy.http import Response, TextResponse

# Palavras que indicam páginas relacionadas a vagas.
#
# Começamos com português e inglês.
PALAVRAS_VAGA = (
    "carreira",
    "carreiras",
    "vaga",
    "vagas",
    "job",
    "jobs",
    "career",
    "careers",
    "oportunidade",
    "oportunidades",
    "position",
    "positions",
    "opening",
    "openings",
    "contratacao",
    "entrevista",
    "recrutamento",
    "selecao",
    "trabalhe conosco",
    "trabalhe-conosco",
)


# Mesmo que uma página use a palavra "vaga", não queremos
# coletar páginas administrativas ou institucionais óbvias.
PALAVRAS_EXCLUIDAS = (
    "login",
    "signin",
    "cadastro",
    "privacy",
    "privacidade",
    "termos",
    "terms",
    "contato",
    "contact",
    "cookie",
    "formulario-curriculo",
    "sharearticle",
    "sharer",
    "/intent/tweet",
)


# Arquivos não são páginas HTML de vagas.
EXTENSOES_IGNORADAS = frozenset(
    {
        ".7z",
        ".csv",
        ".doc",
        ".docx",
        ".gif",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".rar",
        ".svg",
        ".xls",
        ".xlsx",
        ".zip",
    }
)


@dataclass(frozen=True, slots=True)
class LinkCandidatoVaga:
    """Link que possui evidências de apontar para vagas."""

    # Endereço absoluto sem fragmento.
    url: str

    # Texto visível do link.
    texto: str

    # Explica por que o link foi selecionado.
    evidencias: tuple[str, ...]


def descobrir_links_candidatos(
    resposta: Response,
) -> tuple[LinkCandidatoVaga, ...]:
    """Encontra links candidatos sem realizar novas requisições."""

    if not isinstance(resposta, Response):
        raise TypeError("resposta precisa ser uma Response do Scrapy")

    # Response binária não oferece seletores HTML.
    if not isinstance(resposta, TextResponse):
        return ()

    tipo_conteudo = resposta.headers.get(b"Content-Type")

    # Se o servidor informar explicitamente que o conteúdo
    # não é HTML, não tentamos procurar links.
    if tipo_conteudo is not None:
        tipo_texto = tipo_conteudo.decode(
            "latin-1",
            errors="replace",
        ).casefold()

        if "html" not in tipo_texto:
            return ()

    # Remove fragmentos da página atual.
    #
    # Isso permite identificar links que apenas voltam
    # para outra parte da mesma página.
    url_atual, _ = urldefrag(resposta.url)

    encontrados: list[LinkCandidatoVaga] = []
    urls_encontradas: set[str] = set()

    # Procura todas as tags <a> que possuem href.
    for elemento in resposta.css("a[href]"):
        href = (elemento.attrib.get("href") or "").strip()

        if not href:
            continue

        # string(.) também captura textos dentro de tags filhas.
        #
        # Exemplo:
        # <a><span>Desenvolvedor</span></a>
        texto_original = elemento.xpath("string(.)").get() or ""

        # Remove quebras de linha e espaços duplicados.
        texto = " ".join(texto_original.split())

        # Transforma /vagas/123 em uma URL completa.
        url_absoluta = urljoin(
            resposta.url,
            href,
        )

        # /vagas/123 e /vagas/123#descricao representam
        # a mesma página.
        url_limpa, _ = urldefrag(url_absoluta)

        endereco = urlsplit(url_limpa)

        # Elimina mailto:, javascript:, tel: e outros esquemas.
        if endereco.scheme not in {
            "http",
            "https",
        }:
            continue

        if not endereco.netloc:
            continue

        # Não solicita novamente a página atual.
        if url_limpa == url_atual:
            continue

        # Não considera documentos e imagens como vagas.
        if _possui_extensao_ignorada(endereco.path):
            continue

        # Uma vaga citada na query de compartilhamento não é o destino.
        if (endereco.hostname or "").casefold() in {
            "wa.me",
            "api.whatsapp.com",
            "www.facebook.com",
            "www.linkedin.com",
            "x.com",
        }:
            continue
        url_normalizada = _normalizar_texto(url_limpa)

        texto_normalizado = _normalizar_texto(texto)

        # Descarta páginas administrativas ou institucionais.
        if _possui_palavra_excluida(
            url_normalizada,
            texto_normalizado,
        ):
            continue

        evidencias: list[str] = []

        # Exemplo: /vagas/desenvolvedor-python
        if _possui_palavra_vaga(_normalizar_texto(f"{endereco.path} {endereco.query}")):
            evidencias.append("palavra_na_url")

        # Exemplo: <a href="/123">Ver oportunidade</a>
        if _possui_palavra_vaga(texto_normalizado):
            evidencias.append("palavra_no_texto")

        # Sem evidência, não consideramos o link candidato.
        if not evidencias:
            continue

        # Evita guardar o mesmo endereço duas vezes.
        if url_limpa in urls_encontradas:
            continue

        urls_encontradas.add(url_limpa)

        encontrados.append(
            LinkCandidatoVaga(
                url=url_limpa,
                texto=texto,
                evidencias=tuple(evidencias),
            )
        )

    return tuple(encontrados)


def _normalizar_texto(
    valor: str,
) -> str:
    """Remove acentos, diferenças de letras e espaços duplicados."""

    # Exemplo:
    # Oportunidades → oportunidades
    sem_acentos = "".join(
        caractere
        for caractere in normalize(
            "NFKD",
            valor,
        )
        if not combining(caractere)
    )

    return " ".join(sem_acentos.casefold().split())


def _possui_palavra_vaga(
    valor: str,
) -> bool:
    """Verifica se o texto possui algum sinal de vaga."""

    return any(palavra in valor for palavra in PALAVRAS_VAGA)


def _possui_palavra_excluida(
    url: str,
    texto: str,
) -> bool:
    """Elimina páginas administrativas e institucionais óbvias."""

    # O nome do domínio não descreve o destino do link. Por exemplo,
    # ``dataprivacybr.org/vaga`` é uma vaga legítima e contém "privacy"
    # apenas na marca da organização. Avaliamos caminho, consulta e texto.
    endereco = urlsplit(url)
    segmentos = [p for p in endereco.path.split("/") if p]
    detalhe = any(
        p in {"vaga", "vagas", "job", "jobs", "position", "positions", "oportunidade"}
        and i + 1 < len(segmentos)
        for i, p in enumerate(segmentos)
    )
    if detalhe:
        # "Analista de privacidade" e "contato com clientes" podem ser cargos
        # legítimos. Em uma rota explícita de detalhe, examine os segmentos
        # administrativos, não palavras soltas no título ou no slug do cargo.
        return any(p in PALAVRAS_EXCLUIDAS for p in segmentos)
    combinado = f"{endereco.path} {endereco.query} {texto}"

    return any(palavra in combinado for palavra in PALAVRAS_EXCLUIDAS)


def _possui_extensao_ignorada(
    caminho: str,
) -> bool:
    """Evita tratar arquivos para download como páginas de vaga."""

    extensao = PurePosixPath(caminho.casefold()).suffix

    return extensao in EXTENSOES_IGNORADAS
