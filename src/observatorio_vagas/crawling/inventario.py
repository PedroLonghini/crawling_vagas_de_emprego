"""Leitura dos metadados produzidos pelo armazenamento bruto."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from observatorio_vagas.domain.enums import TipoPaginaColeta


class ErroInventarioBruto(ValueError):
    """Erro encontrado ao montar o inventário das coletas."""


@dataclass(frozen=True, slots=True)
class RegistroInventarioBruto:
    """Resumo de uma resposta bruta armazenada."""

    # Caminho do JSON dentro da pasta data/raw.
    referencia: str

    # Versão da estrutura utilizada pelo JSON.
    versao_schema: int

    # Identificação recebida do catálogo.
    #
    # São opcionais porque arquivos antigos da versão 1
    # ainda não possuíam essas informações.
    alvo_id: str | None
    empresa_nome: str | None

    # Papel da página dentro da coleta.
    #
    # Para schemas antigos usamos:
    # numero_pagina = 1;
    # tipo_pagina = avulsa.
    numero_pagina: int
    tipo_pagina: str

    # Origem e endereços da coleta.
    fonte: str
    url_solicitada: str
    url_final: str

    status_http: int
    tamanho_bytes: int

    # Informações necessárias para interpretar o corpo armazenado.
    tipo_conteudo: str | None
    codificacao: str | None

    # SHA-256 usado para verificar e identificar o conteúdo original.
    hash_conteudo: str

    # Momento exato em que a página foi coletada.
    coletado_em: datetime

    # Local do HTML ou conteúdo original.
    caminho_corpo: str


def carregar_inventario_bruto(
    diretorio_base: str | Path,
) -> tuple[RegistroInventarioBruto, ...]:
    """Lê os JSONs de coleta sem carregar os corpos binários."""

    # Transformamos o caminho recebido em caminho absoluto.
    base = Path(diretorio_base).resolve()

    # Os arquivos JSON ficam dentro de data/raw/respostas.
    diretorio_respostas = base / "respostas"

    # Antes da primeira coleta essa pasta pode não existir.
    if not diretorio_respostas.exists():
        return ()

    if not diretorio_respostas.is_dir():
        raise ErroInventarioBruto(f"o caminho de respostas não é uma pasta: {diretorio_respostas}")

    # rglob procura arquivos JSON também dentro das subpastas
    # de fonte, ano, mês e dia.
    registros = [
        _carregar_registro(
            caminho=caminho,
            diretorio_base=base,
        )
        for caminho in diretorio_respostas.rglob("*.json")
    ]

    # Mostramos primeiro as coletas mais recentes.
    return tuple(
        sorted(
            registros,
            key=lambda registro: registro.coletado_em,
            reverse=True,
        )
    )


def carregar_inventario_bruto_desde(
    diretorio_base: str | Path,
    *,
    desde: datetime,
) -> tuple[RegistroInventarioBruto, ...]:
    """Lê somente os metadados dos dias alcançados por ``desde``.

    O armazenamento já separa respostas por fonte, ano, mês e dia. Usar essa
    estrutura evita percorrer todo o histórico quando um lote recém-coletado
    precisa ser reprocessado ou exportado.
    """

    if desde.tzinfo is None or desde.utcoffset() is None:
        raise ValueError("desde precisa incluir fuso horário")

    base = Path(diretorio_base).resolve()
    diretorio_respostas = base / "respostas"
    if not diretorio_respostas.is_dir():
        return ()

    inicio = desde.astimezone(UTC).date()
    fim = datetime.now(UTC).date()
    if inicio > fim:
        return ()

    caminhos: list[Path] = []
    dia = inicio
    while dia <= fim:
        trecho_data = Path(f"{dia.year:04d}") / f"{dia.month:02d}" / f"{dia.day:02d}"
        for diretorio_fonte in diretorio_respostas.iterdir():
            pasta = diretorio_fonte / trecho_data
            if diretorio_fonte.is_dir() and pasta.is_dir():
                caminhos.extend(pasta.glob("*.json"))
        dia += timedelta(days=1)

    registros = [
        _carregar_registro(caminho=caminho, diretorio_base=base)
        for caminho in caminhos
    ]
    return tuple(
        sorted(
            (registro for registro in registros if registro.coletado_em >= desde),
            key=lambda registro: registro.coletado_em,
            reverse=True,
        )
    )


def carregar_inventario_bruto_de_cadernos(
    cadernos: Iterable[Path],
) -> tuple[RegistroInventarioBruto, ...]:
    """Lê os cadernos JSONL gravados durante a coleta.

    Cada linha traz a referência e os mesmos metadados do JSON individual.
    Ler alguns cadernos evita abrir centenas de milhares de arquivos pequenos.
    Uma última linha cortada (processo interrompido no meio da escrita) é
    descartada; uma linha inválida no meio do arquivo é tratada como erro.
    """

    por_referencia: dict[str, RegistroInventarioBruto] = {}

    for caderno in cadernos:
        try:
            linhas = caderno.read_bytes().split(b"\n")
        except OSError as erro:
            raise ErroInventarioBruto(f"não foi possível ler o caderno: {caderno}") from erro

        # O último elemento é vazio quando o arquivo termina com quebra de linha.
        ultima = len(linhas) - 1

        for numero, linha in enumerate(linhas):
            if not linha.strip():
                continue

            try:
                entrada = json.loads(linha.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as erro:
                if numero == ultima:
                    break
                raise ErroInventarioBruto(
                    f"linha {numero + 1} inválida no caderno: {caderno}"
                ) from erro

            if not isinstance(entrada, dict) or not isinstance(entrada.get("referencia"), str):
                raise ErroInventarioBruto(f"linha {numero + 1} inválida no caderno: {caderno}")

            referencia = entrada["referencia"]
            por_referencia[referencia] = _registro_de_dados(
                entrada.get("metadados"),
                referencia,
            )

    return tuple(
        sorted(
            por_referencia.values(),
            key=lambda registro: registro.coletado_em,
            reverse=True,
        )
    )


def _carregar_registro(
    *,
    caminho: Path,
    diretorio_base: Path,
) -> RegistroInventarioBruto:
    """Converte um arquivo JSON em um registro do inventário."""

    referencia = caminho.relative_to(diretorio_base).as_posix()

    try:
        # Lemos somente os metadados.
        #
        # O HTML ou JSON bruto permanece no arquivo .bin.
        conteudo = json.loads(
            caminho.read_text(
                encoding="utf-8",
            )
        )

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as erro:
        raise ErroInventarioBruto(f"não foi possível ler os metadados: {referencia}") from erro

    return _registro_de_dados(conteudo, referencia)


def _registro_de_dados(
    conteudo: object,
    referencia: str,
) -> RegistroInventarioBruto:
    """Valida os metadados de uma resposta e monta o registro."""

    if not isinstance(conteudo, dict):
        raise ErroInventarioBruto(f"os metadados precisam ser um objeto JSON: {referencia}")

    dados: dict[str, Any] = conteudo

    # Primeiro descobrimos a versão.
    #
    # Ela determina quais campos devem existir no documento.
    versao_schema = _ler_inteiro(
        dados,
        "versao_schema",
        referencia,
    )

    numero_pagina, tipo_pagina = _ler_contexto_pagina(
        dados=dados,
        versao_schema=versao_schema,
        referencia=referencia,
    )

    return RegistroInventarioBruto(
        referencia=referencia,
        versao_schema=versao_schema,
        alvo_id=_ler_texto_opcional(
            dados,
            "alvo_id",
            referencia,
        ),
        empresa_nome=_ler_texto_opcional(
            dados,
            "empresa_nome",
            referencia,
        ),
        numero_pagina=numero_pagina,
        tipo_pagina=tipo_pagina,
        fonte=_ler_texto(
            dados,
            "fonte",
            referencia,
        ),
        url_solicitada=_ler_texto(
            dados,
            "url_solicitada",
            referencia,
        ),
        url_final=_ler_texto(
            dados,
            "url_final",
            referencia,
        ),
        status_http=_ler_inteiro(
            dados,
            "status_http",
            referencia,
        ),
        tamanho_bytes=_ler_inteiro(
            dados,
            "tamanho_bytes",
            referencia,
        ),
        tipo_conteudo=_ler_texto_opcional(
            dados,
            "tipo_conteudo",
            referencia,
        ),
        codificacao=_ler_texto_opcional(
            dados,
            "codificacao",
            referencia,
        ),
        hash_conteudo=_ler_texto(
            dados,
            "hash_conteudo",
            referencia,
        ),
        coletado_em=_ler_data(
            dados,
            "coletado_em",
            referencia,
        ),
        caminho_corpo=_ler_texto(
            dados,
            "caminho_corpo",
            referencia,
        ),
    )


def _ler_contexto_pagina(
    *,
    dados: dict[str, Any],
    versao_schema: int,
    referencia: str,
) -> tuple[int, str]:
    """Lê o papel da página mantendo compatibilidade histórica."""

    # As versões 1 e 2 não possuíam esses campos.
    #
    # Em vez de quebrar o dashboard, apresentamos essas páginas
    # antigas como coletas avulsas.
    if versao_schema < 3:
        return (
            1,
            TipoPaginaColeta.AVULSA.value,
        )

    # A partir da versão 3, os dois campos são obrigatórios.
    numero_pagina = _ler_inteiro(
        dados,
        "numero_pagina",
        referencia,
    )

    if numero_pagina < 1:
        raise ErroInventarioBruto(f"campo 'numero_pagina' inválido em {referencia}")

    tipo_original = _ler_texto(
        dados,
        "tipo_pagina",
        referencia,
    )

    try:
        tipo_pagina = TipoPaginaColeta(tipo_original)

    except ValueError as erro:
        raise ErroInventarioBruto(f"campo 'tipo_pagina' inválido em {referencia}") from erro

    return (
        numero_pagina,
        tipo_pagina.value,
    )


def _ler_texto(
    dados: dict[str, Any],
    campo: str,
    referencia: str,
) -> str:
    """Lê um texto obrigatório dos metadados."""

    valor = dados.get(campo)

    if not isinstance(valor, str) or not valor.strip():
        raise ErroInventarioBruto(f"campo {campo!r} inválido em {referencia}")

    return valor.strip()


def _ler_texto_opcional(
    dados: dict[str, Any],
    campo: str,
    referencia: str,
) -> str | None:
    """Lê um texto que pode não existir em coletas antigas."""

    valor = dados.get(campo)

    if valor is None:
        return None

    if not isinstance(valor, str) or not valor.strip():
        raise ErroInventarioBruto(f"campo {campo!r} inválido em {referencia}")

    return valor.strip()


def _ler_inteiro(
    dados: dict[str, Any],
    campo: str,
    referencia: str,
) -> int:
    """Lê um número inteiro sem aceitar True ou False."""

    valor = dados.get(campo)

    # Python considera True e False como números.
    if isinstance(valor, bool) or not isinstance(
        valor,
        int,
    ):
        raise ErroInventarioBruto(f"campo {campo!r} inválido em {referencia}")

    return valor


def _ler_data(
    dados: dict[str, Any],
    campo: str,
    referencia: str,
) -> datetime:
    """Converte uma data ISO 8601 em datetime com fuso horário."""

    texto = _ler_texto(
        dados,
        campo,
        referencia,
    )

    try:
        resultado = datetime.fromisoformat(texto)

    except ValueError as erro:
        raise ErroInventarioBruto(f"campo {campo!r} inválido em {referencia}") from erro

    if resultado.tzinfo is None:
        raise ErroInventarioBruto(f"campo {campo!r} precisa informar o fuso em {referencia}")

    return resultado
