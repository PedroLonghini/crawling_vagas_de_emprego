"""Extrai anúncios das páginas brutas e os salva no MongoDB.

Sem --confirmar, este comando funciona somente como simulação.
Ele não publica vagas no Empregos e não acessa sua API.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from pymongo.errors import PyMongoError

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.inventario import (
    ErroInventarioBruto,
    RegistroInventarioBruto,
)
from observatorio_vagas.extraction import (
    ResultadoProcessamentoExtracao,
    processar_respostas_brutas,
)
from observatorio_vagas.extraction.data_publicacao import ontem_brasilia
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    RepositorioAnunciosMongoDB,
    preparar_banco,
)

# Código reservado para o orquestrador distinguir uma execução válida
# que não encontrou anúncios de uma falha técnica.
CODIGO_SEM_ANUNCIOS = 10


def _converter_data_hora(
    valor: str,
) -> datetime:
    """Converte uma data ISO 8601 recebida pelo terminal."""

    try:
        resultado = datetime.fromisoformat(valor)

    except ValueError as erro:
        raise argparse.ArgumentTypeError("coletado-desde precisa usar uma data ISO 8601") from erro

    if resultado.tzinfo is None or resultado.utcoffset() is None:
        raise argparse.ArgumentTypeError("coletado-desde precisa informar o fuso horário")

    return resultado


def criar_parser() -> argparse.ArgumentParser:
    """Define os argumentos aceitos pelo comando."""

    parser = argparse.ArgumentParser(
        description=(
            "Extrai anúncios de um alvo do catálogo e, quando confirmado, salva no MongoDB."
        )
    )

    parser.add_argument(
        "--alvo-id",
        required=True,
        help=("identificador exato do alvo existente no catálogo"),
    )

    parser.add_argument(
        "--diretorio-raw",
        type=Path,
        default=Path("data/raw"),
        help=("diretório das respostas brutas (padrão: data/raw)"),
    )

    parser.add_argument(
        "--coletado-desde",
        type=_converter_data_hora,
        help=("processa somente respostas coletadas a partir desta data ISO 8601"),
    )

    parser.add_argument(
        "--confirmar",
        action="store_true",
        help=("confirma que os anúncios podem ser gravados no MongoDB"),
    )

    parser.add_argument(
        "--sinalizar-sem-anuncios",
        action="store_true",
        help=(
            "retorna um código específico quando não há anúncios; "
            "uso interno do processamento em lote"
        ),
    )

    datas = parser.add_mutually_exclusive_group()
    datas.add_argument(
        "--publicados-ontem", action="store_true", help="publicados ontem em Brasília"
    )
    datas.add_argument(
        "--publicados-em", type=date.fromisoformat, help="data de publicação YYYY-MM-DD"
    )
    return parser


def mostrar_resultado_extracao(
    resultado: ResultadoProcessamentoExtracao,
) -> None:
    """Mostra um resumo seguro antes de qualquer gravação."""

    print()
    print("Resumo da extração")
    print("------------------")
    print(
        "Anúncios únicos:",
        len(resultado.anuncios),
    )
    print(
        "Páginas analisadas:",
        resultado.paginas_analisadas,
    )
    print(
        "Páginas ignoradas:",
        resultado.paginas_ignoradas,
    )
    print("  De outros alvos/períodos:", resultado.paginas_fora_filtro)
    print("  HTTP/tipo de página/conteúdo incompatível:", resultado.paginas_incompativeis)
    print("Anúncios de outras datas:", resultado.anuncios_fora_data_publicacao)
    print("Anúncios sem data de publicação:", resultado.anuncios_sem_data_publicacao)
    print(
        "Páginas sem vagas reconhecidas pelos extratores:",
        resultado.paginas_sem_json_ld,
    )
    print(
        "Documentos encontrados:",
        resultado.documentos_encontrados,
    )
    print(
        "Anúncios duplicados:",
        resultado.anuncios_duplicados,
    )
    print(
        "Blocos JSON-LD inválidos:",
        resultado.blocos_invalidos,
    )
    print(
        "Falhas:",
        len(resultado.falhas),
    )
    if resultado.paginas_do_cache:
        print("Páginas reaproveitadas do cache:", resultado.paginas_do_cache)

    print()
    print("Anúncios preparados")
    print("-------------------")

    for anuncio in resultado.anuncios:
        print(
            "-",
            anuncio.titulo_original,
            "|",
            (anuncio.empresa_original or "Empresa não informada"),
            "|",
            anuncio.id_externo,
        )

    if resultado.falhas:
        print()
        print("Falhas encontradas")
        print("------------------")

        for falha in resultado.falhas:
            print(
                "-",
                falha.referencia,
                "|",
                falha.mensagem,
            )


def executar(
    *,
    alvo_id: str,
    diretorio_raw: Path,
    coletado_desde: datetime | None,
    confirmar: bool,
    sinalizar_sem_anuncios: bool = False,
    registros: tuple[RegistroInventarioBruto, ...] | None = None,
    publicado_em: date | None = None,
    preparar: bool = True,
    cache_paginas: Path | None = None,
) -> int:
    """Executa a extração e a persistência autorizada.

    ``preparar=False`` pula a criação de índices, para quem já a fez.
    """

    if publicado_em is not None:
        print("Data de publicação selecionada (Brasília):", publicado_em.isoformat())
    if coletado_desde is not None:
        print()
        print(
            "Processando respostas coletadas desde:",
            coletado_desde.isoformat(),
        )

    try:
        resultado_extracao = processar_respostas_brutas(
            diretorio_raw,
            alvo_id=alvo_id,
            coletado_desde=coletado_desde,
            registros=registros,
            publicado_em=publicado_em,
            cache_paginas=cache_paginas,
        )

    except (
        ErroInventarioBruto,
        OSError,
        TypeError,
        ValueError,
    ) as erro:
        print("Não foi possível processar as respostas brutas:")
        print(erro)
        return 1

    return concluir_extracao(
        resultado_extracao,
        confirmar=confirmar,
        sinalizar_sem_anuncios=sinalizar_sem_anuncios,
        preparar=preparar,
    )


def concluir_extracao(
    resultado_extracao: ResultadoProcessamentoExtracao,
    *,
    confirmar: bool,
    sinalizar_sem_anuncios: bool = False,
    preparar: bool = True,
) -> int:
    """Mostra o resultado e grava os anúncios quando autorizado.

    Separada de ``executar`` para que o lote possa juntar pedaços de um alvo
    extraídos em processos diferentes e concluir uma única vez.
    """

    mostrar_resultado_extracao(resultado_extracao)

    # Falhas precisam ser analisadas antes da gravação.
    if resultado_extracao.falhas:
        print()
        print("GRAVAÇÃO BLOQUEADA: existem falhas de extração.")
        return 2

    if not resultado_extracao.anuncios:
        print()
        print("Nenhum anúncio foi encontrado. Nada será gravado.")
        return CODIGO_SEM_ANUNCIOS if sinalizar_sem_anuncios else 0

    # Sem confirmação, termina antes de abrir o MongoDB.
    if not confirmar:
        print()
        print("SIMULAÇÃO CONCLUÍDA: nenhuma gravação foi realizada.")
        print("Use --confirmar somente depois de revisar este resultado.")
        return 0

    configuracoes = get_settings()

    if configuracoes.is_production:
        print()
        print("GRAVAÇÃO BLOQUEADA: o ambiente está configurado como produção.")
        return 3

    try:
        with ConexaoMongoDB(configuracoes) as conexao:
            if preparar:
                preparar_banco(conexao.banco)

            repositorio = RepositorioAnunciosMongoDB(conexao.banco)

            resultado_gravacao = repositorio.salvar_lote(resultado_extracao.anuncios)

    except (
        ErroConexaoMongoDB,
        ErroRepositorioAnunciosMongoDB,
    ) as erro:
        print()
        print("Não foi possível salvar os anúncios:")
        print(erro)
        return 4

    except PyMongoError:
        print()
        print("O MongoDB recusou a operação de gravação.")
        return 4

    print()
    print("Gravação concluída")
    print("------------------")
    print(
        "Recebidos:",
        resultado_gravacao.recebidos,
    )
    print(
        "Inseridos:",
        resultado_gravacao.inseridos,
    )
    print(
        "Atualizados:",
        resultado_gravacao.atualizados,
    )
    print(
        "Inalterados:",
        resultado_gravacao.inalterados,
    )

    return 0


def main(
    argumentos: Sequence[str] | None = None,
) -> int:
    """Lê o terminal e inicia a operação."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)

    return executar(
        alvo_id=opcoes.alvo_id,
        diretorio_raw=opcoes.diretorio_raw,
        coletado_desde=opcoes.coletado_desde,
        confirmar=opcoes.confirmar,
        sinalizar_sem_anuncios=opcoes.sinalizar_sem_anuncios,
        publicado_em=ontem_brasilia() if opcoes.publicados_ontem else opcoes.publicados_em,
    )


if __name__ == "__main__":
    # No Windows, a saída redirecionada usa cp1252; um caractere fora dele
    # descartava o alvo inteiro. Caracteres impossíveis viram escapes.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
