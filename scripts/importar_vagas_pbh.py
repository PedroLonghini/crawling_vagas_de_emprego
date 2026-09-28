"""Importa o CSV oficial de vagas da PBH para o MongoDB.

O comando é somente uma prévia até receber ``--confirmar``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from pymongo.errors import PyMongoError

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.enums import StatusAnuncio
from observatorio_vagas.importacao import (
    ErroArquivoVagasPBH,
    importar_vagas_pbh_csv,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    RepositorioAnunciosMongoDB,
    preparar_banco,
)


def criar_parser() -> argparse.ArgumentParser:
    """Define uma interface pequena e segura para a importação."""

    parser = argparse.ArgumentParser(
        description="Importa o CSV oficial VAGAS OFERTADAS PBH.",
    )
    parser.add_argument(
        "--arquivo",
        type=Path,
        required=True,
        help="caminho do CSV baixado no portal de dados abertos da PBH",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=5000,
        help="máximo de anúncios processados (padrão: 5000)",
    )
    parser.add_argument(
        "--validade-dias",
        type=int,
        default=30,
        help="prazo operacional conservador quando a fonte não informa expiração",
    )
    parser.add_argument(
        "--somente-vigentes",
        action="store_true",
        help="ignora registros encerrados pela validade operacional",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="confirma a gravação dos anúncios válidos no MongoDB",
    )
    return parser


def executar(argumentos: Sequence[str] | None = None) -> int:
    """Converte o arquivo e grava somente após confirmação explícita."""

    opcoes = criar_parser().parse_args(argumentos)

    if not 1 <= opcoes.limite <= 10000:
        print("ERRO: limite deve estar entre 1 e 10000.")
        return 2

    if not 1 <= opcoes.validade_dias <= 365:
        print("ERRO: validade-dias deve estar entre 1 e 365.")
        return 2

    try:
        conteudo = opcoes.arquivo.read_bytes()
        resultado = importar_vagas_pbh_csv(
            conteudo,
            referencia_bruta=opcoes.arquivo.as_posix(),
            validade_dias=opcoes.validade_dias,
        )
    except (ErroArquivoVagasPBH, OSError, ValueError) as erro:
        print(f"ERRO: não foi possível importar o CSV: {erro}")
        return 1

    anuncios = list(resultado.anuncios)
    if opcoes.somente_vigentes:
        anuncios = [
            anuncio for anuncio in anuncios if anuncio.status is not StatusAnuncio.ENCERRADO
        ]
    anuncios = anuncios[: opcoes.limite]

    print()
    print("RESUMO DA IMPORTAÇÃO PBH")
    print(f"Linhas lidas: {resultado.linhas_lidas}")
    print(f"Anúncios válidos: {len(resultado.anuncios)}")
    print(f"Anúncios encerrados pela trava temporal: {resultado.encerrados}")
    print(f"Duplicados ignorados: {resultado.duplicados}")
    print(f"Linhas inválidas ignoradas: {len(resultado.falhas)}")
    print(f"Selecionados para esta execução: {len(anuncios)}")

    if resultado.falhas:
        print()
        print("PRIMEIRAS FALHAS ISOLADAS")
        for falha in resultado.falhas[:20]:
            print(f"- linha {falha.numero_linha} | {falha.identificacao} | {falha.mensagem}")

    if anuncios:
        print()
        print("PRIMEIROS ANÚNCIOS")
        for anuncio in anuncios[:20]:
            print(f"- {anuncio.id_externo} | {anuncio.titulo_original} | {anuncio.status.value}")

    if not anuncios:
        print()
        print("Nenhum anúncio atende aos filtros. Nada será gravado.")
        return 0

    if not opcoes.confirmar:
        print()
        print("SIMULAÇÃO CONCLUÍDA: nenhuma gravação foi realizada.")
        print("Revise o resultado e acrescente --confirmar para gravar.")
        return 0

    configuracoes = get_settings()
    if configuracoes.is_production:
        print("ERRO: a gravação está bloqueada no ambiente de produção.")
        return 3

    try:
        with ConexaoMongoDB(configuracoes) as conexao:
            preparar_banco(conexao.banco)
            repositorio = RepositorioAnunciosMongoDB(conexao.banco)
            gravacao = repositorio.salvar_lote(anuncios)
    except (
        ErroConexaoMongoDB,
        ErroRepositorioAnunciosMongoDB,
        PyMongoError,
    ) as erro:
        print(f"ERRO: não foi possível gravar no MongoDB: {erro}")
        return 4

    print()
    print("GRAVAÇÃO CONCLUÍDA")
    print(f"Recebidos: {gravacao.recebidos}")
    print(f"Inseridos: {gravacao.inseridos}")
    print(f"Atualizados: {gravacao.atualizados}")
    print(f"Inalterados: {gravacao.inalterados}")
    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
