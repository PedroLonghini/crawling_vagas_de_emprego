"""Extração auditável de CNPJs encontrados em textos e PDFs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

PADRAO_CNPJ = re.compile(
    r"(?<!\d)"
    r"\d{2}[.\s]?\d{3}[.\s]?\d{3}"
    r"[/\s]?\d{4}-?\d{2}"
    r"(?!\d)"
)


class ErroExtracaoCnpjDocumento(ValueError):
    """Indica que o documento não pôde ser analisado com segurança."""


@dataclass(frozen=True, slots=True)
class CnpjDocumento:
    """CNPJ validado junto da página e do trecho que o comprova."""

    cnpj: str
    pagina: int
    trecho_evidencia: str

    def __post_init__(self) -> None:
        """Impede a criação de uma evidência inconsistente."""

        if not validar_cnpj(self.cnpj):
            raise ValueError("cnpj precisa possuir dígitos verificadores válidos")

        if isinstance(self.pagina, bool) or self.pagina < 1:
            raise ValueError("pagina precisa ser um inteiro positivo")

        if not self.trecho_evidencia.strip():
            raise ValueError("trecho_evidencia não pode ser vazio")

    @property
    def formatado(self) -> str:
        """Retorna o CNPJ com a pontuação convencional."""

        return formatar_cnpj(self.cnpj)


def normalizar_cnpj(valor: str) -> str:
    """Remove todos os caracteres que não são números."""

    if not isinstance(valor, str):
        raise TypeError("valor precisa ser texto")

    return re.sub(
        r"\D",
        "",
        valor,
    )


def formatar_cnpj(valor: str) -> str:
    """Aplica a pontuação convencional a um CNPJ válido."""

    normalizado = normalizar_cnpj(valor)

    if not validar_cnpj(normalizado):
        raise ValueError("não é possível formatar um CNPJ inválido")

    return (
        f"{normalizado[:2]}."
        f"{normalizado[2:5]}."
        f"{normalizado[5:8]}/"
        f"{normalizado[8:12]}-"
        f"{normalizado[12:]}"
    )


def validar_cnpj(valor: str) -> bool:
    """Valida tamanho, repetições e dígitos verificadores."""

    if not isinstance(valor, str):
        return False

    normalizado = normalizar_cnpj(valor)

    if len(normalizado) != 14:
        return False

    if len(set(normalizado)) == 1:
        return False

    pesos_primeiro = (
        5,
        4,
        3,
        2,
        9,
        8,
        7,
        6,
        5,
        4,
        3,
        2,
    )

    pesos_segundo = (
        6,
        5,
        4,
        3,
        2,
        9,
        8,
        7,
        6,
        5,
        4,
        3,
        2,
    )

    primeiro = _calcular_digito(
        normalizado[:12],
        pesos_primeiro,
    )

    if normalizado[12] != primeiro:
        return False

    segundo = _calcular_digito(
        normalizado[:13],
        pesos_segundo,
    )

    return normalizado[13] == segundo


def extrair_cnpjs_texto(
    texto: str,
    *,
    pagina: int = 1,
) -> tuple[CnpjDocumento, ...]:
    """Localiza somente CNPJs numericamente válidos em um texto."""

    if not isinstance(texto, str):
        raise TypeError("texto precisa ser uma string")

    if isinstance(pagina, bool) or not isinstance(pagina, int):
        raise TypeError("pagina precisa ser um inteiro")

    if pagina < 1:
        raise ValueError("pagina precisa ser positiva")

    resultados: list[CnpjDocumento] = []
    encontrados: set[str] = set()

    for correspondencia in PADRAO_CNPJ.finditer(texto):
        cnpj = normalizar_cnpj(correspondencia.group(0))

        if not validar_cnpj(cnpj):
            continue

        if cnpj in encontrados:
            continue

        encontrados.add(cnpj)

        trecho = _extrair_trecho(
            texto=texto,
            inicio=correspondencia.start(),
            fim=correspondencia.end(),
        )

        resultados.append(
            CnpjDocumento(
                cnpj=cnpj,
                pagina=pagina,
                trecho_evidencia=trecho,
            )
        )

    return tuple(resultados)


def extrair_cnpjs_pdf(
    conteudo: bytes,
) -> tuple[CnpjDocumento, ...]:
    """Extrai texto do PDF e retorna CNPJs válidos e únicos."""

    if not isinstance(conteudo, bytes):
        raise TypeError("conteudo precisa ser bytes")

    if not conteudo:
        raise ErroExtracaoCnpjDocumento("o documento PDF está vazio")

    try:
        leitor = PdfReader(
            BytesIO(conteudo),
            strict=False,
        )

    except (
        PdfReadError,
        OSError,
        ValueError,
    ) as erro:
        raise ErroExtracaoCnpjDocumento("não foi possível interpretar o documento PDF") from erro

    resultados: list[CnpjDocumento] = []
    encontrados: set[str] = set()

    for numero_pagina, pagina_pdf in enumerate(
        leitor.pages,
        start=1,
    ):
        try:
            texto = pagina_pdf.extract_text() or ""

        except Exception as erro:
            raise ErroExtracaoCnpjDocumento(
                f"não foi possível extrair o texto da página {numero_pagina}"
            ) from erro

        for resultado in extrair_cnpjs_texto(
            texto,
            pagina=numero_pagina,
        ):
            if resultado.cnpj in encontrados:
                continue

            encontrados.add(resultado.cnpj)
            resultados.append(resultado)

    return tuple(resultados)


def _calcular_digito(
    base: str,
    pesos: tuple[int, ...],
) -> str:
    """Calcula um dígito verificador do CNPJ."""

    soma = sum(
        int(digito) * peso
        for digito, peso in zip(
            base,
            pesos,
            strict=True,
        )
    )

    resto = soma % 11

    if resto < 2:
        return "0"

    return str(11 - resto)


def _extrair_trecho(
    *,
    texto: str,
    inicio: int,
    fim: int,
    margem: int = 140,
) -> str:
    """Preserva um pequeno contexto ao redor do CNPJ."""

    inicio_trecho = max(
        inicio - margem,
        0,
    )

    fim_trecho = min(
        fim + margem,
        len(texto),
    )

    trecho = texto[inicio_trecho:fim_trecho]

    return " ".join(trecho.split())
