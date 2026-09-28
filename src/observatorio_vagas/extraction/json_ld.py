"""Extração de vagas estruturadas em JSON-LD.

Muitos sites publicam informações de vagas dentro de uma tag como:

<script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Pessoa Desenvolvedora"
    }
</script>

Algumas plataformas, como a Gupy, codificam caracteres JSON usando
entidades HTML. O extrator aceita os dois formatos sem alterar um
JSON que já esteja válido.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

# Valor esperado no atributo "type" da tag <script>.
TIPO_CONTEUDO_JSON_LD = "application/ld+json"

# Tipo utilizado pelo Schema.org para representar uma vaga.
TIPO_VAGA_SCHEMA = "jobposting"


@dataclass(frozen=True, slots=True)
class ResultadoExtracaoJsonLd:
    """Resultado da leitura dos blocos JSON-LD de uma página."""

    # Cada item desta tupla representa uma vaga estruturada.
    vagas: tuple[dict[str, Any], ...]

    # Quantidade total de blocos JSON-LD encontrados no HTML.
    blocos_encontrados: int

    # Quantidade de blocos encontrados que possuíam JSON inválido.
    #
    # Um bloco inválido não impede que os demais sejam processados.
    blocos_invalidos: int


class _ColetorBlocosJsonLd(HTMLParser):
    """Localiza o texto existente dentro das tags JSON-LD."""

    def __init__(self) -> None:
        """Prepara o estado interno do leitor de HTML."""

        super().__init__(
            convert_charrefs=True,
        )

        # Indica se o parser está dentro de uma tag JSON-LD.
        self._capturando = False

        # Uma tag pode entregar o conteúdo em várias partes.
        self._partes_atuais: list[str] = []

        # Guarda cada bloco completo encontrado.
        self.blocos: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """Verifica se uma tag aberta inicia um bloco JSON-LD."""

        if tag.casefold() != "script":
            return

        atributos = {nome.casefold(): valor for nome, valor in attrs}

        tipo = atributos.get("type") or ""

        # Também aceita:
        # application/ld+json; charset=utf-8
        tipo_normalizado = (
            tipo.split(
                ";",
                maxsplit=1,
            )[0]
            .strip()
            .casefold()
        )

        if tipo_normalizado != TIPO_CONTEUDO_JSON_LD:
            return

        self._capturando = True
        self._partes_atuais = []

    def handle_data(
        self,
        data: str,
    ) -> None:
        """Guarda o conteúdo encontrado dentro da tag atual."""

        if self._capturando:
            self._partes_atuais.append(data)

    def handle_endtag(
        self,
        tag: str,
    ) -> None:
        """Finaliza o bloco ao encontrar o fechamento de script."""

        if tag.casefold() != "script" or not self._capturando:
            return

        bloco_completo = "".join(
            self._partes_atuais,
        ).strip()

        if bloco_completo:
            self.blocos.append(
                bloco_completo,
            )

        self._capturando = False
        self._partes_atuais = []


def _normalizar_nome_tipo(
    valor: str,
) -> str:
    """Normaliza diferentes representações do tipo JobPosting."""

    normalizado = valor.strip().casefold()

    # Aceita:
    # JobPosting
    # https://schema.org/JobPosting
    # schema:JobPosting
    for separador in (
        "/",
        "#",
        ":",
    ):
        normalizado = normalizado.rsplit(
            separador,
            maxsplit=1,
        )[-1]

    return normalizado


def _eh_job_posting(
    documento: dict[str, Any],
) -> bool:
    """Informa se um documento JSON-LD representa uma vaga."""

    tipos = documento.get("@type")

    if isinstance(tipos, str):
        tipos = [
            tipos,
        ]

    if not isinstance(tipos, list):
        return False

    return any(
        isinstance(tipo, str) and _normalizar_nome_tipo(tipo) == TIPO_VAGA_SCHEMA for tipo in tipos
    )


def _iterar_documentos(
    valor: object,
) -> Iterator[dict[str, Any]]:
    """Percorre formatos válidos de documentos JSON-LD."""

    # Um bloco pode conter uma lista de documentos.
    if isinstance(valor, list):
        for item in valor:
            yield from _iterar_documentos(
                item,
            )

        return

    if not isinstance(valor, dict):
        return

    yield valor

    # Listagens também usam ItemList -> ListItem -> item. Somente os nós
    # explicitamente JobPosting serão extraídos; BreadcrumbList não vira vaga.
    for chave in ("@graph", "itemListElement", "item", "mainEntity"):
        filho = valor.get(chave)
        if isinstance(filho, (dict, list)):
            yield from _iterar_documentos(filho)


def _carregar_documento_json_ld(
    bloco: str,
) -> object:
    """Carrega JSON-LD normal ou codificado com entidades HTML."""

    try:
        # Este é o formato correto e será sempre tentado primeiro.
        return json.loads(
            bloco,
        )

    except json.JSONDecodeError:
        # Dentro de tags script, algumas plataformas deixam textos como:
        #
        # {&quot;@type&quot;:&quot;JobPosting&quot;}
        #
        # HTMLParser não converte essas entidades automaticamente
        # dentro de conteúdo de script.
        bloco_decodificado = unescape(
            bloco,
        )

        # Se nada mudou, repetiria exatamente o mesmo erro.
        if bloco_decodificado == bloco:
            raise

        return json.loads(
            bloco_decodificado,
        )


def extrair_job_postings_json_ld(
    conteudo: str | bytes,
    *,
    codificacao: str = "utf-8",
) -> ResultadoExtracaoJsonLd:
    """Extrai todos os objetos JobPosting existentes em um HTML."""

    if isinstance(conteudo, bytes):
        if not codificacao.strip():
            raise ValueError(
                "codificacao não pode ser vazia",
            )

        html = conteudo.decode(
            codificacao,
            errors="replace",
        )

    elif isinstance(conteudo, str):
        html = conteudo

    else:
        raise TypeError(
            "conteudo precisa ser texto ou bytes",
        )

    coletor = _ColetorBlocosJsonLd()

    coletor.feed(
        html,
    )

    coletor.close()

    vagas: list[dict[str, Any]] = []
    blocos_invalidos = 0

    for bloco in coletor.blocos:
        try:
            documento_raiz = _carregar_documento_json_ld(
                bloco,
            )

        except json.JSONDecodeError:
            # Um bloco inválido não impede o processamento dos demais.
            blocos_invalidos += 1
            continue

        for documento in _iterar_documentos(
            documento_raiz,
        ):
            if _eh_job_posting(
                documento,
            ):
                vagas.append(
                    documento,
                )

    return ResultadoExtracaoJsonLd(
        vagas=tuple(
            vagas,
        ),
        blocos_encontrados=len(
            coletor.blocos,
        ),
        blocos_invalidos=blocos_invalidos,
    )
