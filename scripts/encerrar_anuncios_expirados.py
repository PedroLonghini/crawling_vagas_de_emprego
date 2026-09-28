"""Marca como encerrados os anúncios cuja data de expiração já passou.

O comando preserva o histórico: nenhum anúncio é removido do MongoDB. Sem
``--confirmar`` ele apenas mostra o que seria alterado.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from observatorio_vagas.config import get_settings
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.enums import StatusAnuncio
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioAnunciosMongoDB,
    RepositorioAnunciosMongoDB,
)


@dataclass(frozen=True)
class ResultadoEncerramento:
    """Totais da atualização de expirações."""

    avaliados: int
    expirados: int
    atualizados: int
    ja_encerrados: int


def criar_parser() -> argparse.ArgumentParser:
    """Cria os argumentos aceitos pelo comando."""

    parser = argparse.ArgumentParser(
        description=(
            "Marca anúncios expirados como encerrados, sem apagar o histórico. "
            "Sem --confirmar, nenhuma informação é alterada."
        )
    )
    parser.add_argument(
        "--alvo-id",
        help="processa somente anúncios pertencentes ao alvo informado no catálogo",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=10000,
        help="quantidade máxima de anúncios analisados (padrão: 10000)",
    )
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="autoriza atualizar o status dos anúncios no MongoDB",
    )
    return parser


def esta_expirado(
    expira_em: date | datetime | None,
    *,
    momento_referencia: datetime,
) -> bool:
    """Informa se a expiração já passou, preservando o dia de validade."""

    if expira_em is None:
        return False

    if isinstance(expira_em, datetime):
        data_hora = expira_em
        if data_hora.tzinfo is None:
            data_hora = data_hora.replace(tzinfo=UTC)
        return data_hora <= momento_referencia

    return expira_em < momento_referencia.date()


def carregar_anuncios(
    repositorio: RepositorioAnunciosMongoDB,
    *,
    alvo_id: str | None,
    limite: int,
) -> list[AnuncioVaga]:
    """Carrega anúncios pela seleção solicitada."""

    if limite < 1 or limite > 10000:
        raise ValueError("limite deve estar entre 1 e 10000")

    if alvo_id is None:
        return repositorio.listar_recentes(limite=limite)

    alvo_normalizado = alvo_id.strip()
    if not alvo_normalizado:
        raise ValueError("alvo-id não pode ser vazio")

    return repositorio.listar_por_alvo(alvo_normalizado, limite=limite)


def encerrar_expirados(
    anuncios: Sequence[AnuncioVaga],
    *,
    repositorio: RepositorioAnunciosMongoDB,
    confirmar: bool,
    momento_referencia: datetime,
) -> ResultadoEncerramento:
    """Atualiza somente anúncios com expiração definitivamente vencida."""

    expirados = 0
    atualizados = 0
    ja_encerrados = 0

    for anuncio in anuncios:
        if not esta_expirado(
            anuncio.expira_em,
            momento_referencia=momento_referencia,
        ):
            continue

        expirados += 1
        if anuncio.status is StatusAnuncio.ENCERRADO:
            ja_encerrados += 1
            continue

        if confirmar:
            repositorio.salvar(
                anuncio.model_copy(update={"status": StatusAnuncio.ENCERRADO})
            )
        atualizados += 1

    return ResultadoEncerramento(
        avaliados=len(anuncios),
        expirados=expirados,
        atualizados=atualizados,
        ja_encerrados=ja_encerrados,
    )


def executar(argumentos: list[str] | None = None) -> int:
    """Abre o MongoDB e executa a atualização solicitada."""

    opcoes = criar_parser().parse_args(argumentos)

    try:
        configuracoes = get_settings()
        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio = RepositorioAnunciosMongoDB(conexao.banco)
            anuncios = carregar_anuncios(
                repositorio,
                alvo_id=opcoes.alvo_id,
                limite=opcoes.limite,
            )
            resultado = encerrar_expirados(
                anuncios,
                repositorio=repositorio,
                confirmar=opcoes.confirmar,
                momento_referencia=agora_utc(),
            )

    except (ErroConexaoMongoDB, ErroRepositorioAnunciosMongoDB, ValueError) as erro:
        print(f"ERRO: {erro}")
        return 1

    modo = "GRAVAÇÃO CONFIRMADA" if opcoes.confirmar else "SOMENTE PRÉVIA"
    print()
    print("# ENCERRAMENTO DE ANÚNCIOS EXPIRADOS")
    print()
    print(f"Modo: {modo}")
    print(f"Anúncios avaliados: {resultado.avaliados}")
    print(f"Expirados identificados: {resultado.expirados}")
    print(f"Já encerrados: {resultado.ja_encerrados}")
    print(f"Marcados como encerrados: {resultado.atualizados}")

    if not opcoes.confirmar and resultado.atualizados:
        print()
        print("Para aplicar, execute novamente acrescentando --confirmar.")

    return 0


if __name__ == "__main__":
    raise SystemExit(executar())
