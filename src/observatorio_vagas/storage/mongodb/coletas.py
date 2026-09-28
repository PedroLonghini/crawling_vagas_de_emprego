"""Implementação MongoDB do repositório de execuções de coleta."""

from __future__ import annotations

from uuid import UUID

from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from observatorio_vagas.domain.coleta import ExecucaoColeta
from observatorio_vagas.domain.enums import Fonte
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)
from observatorio_vagas.storage.mongodb.schema import COLECAO_EXECUCOES_COLETA


class ErroRepositorioColetasMongoDB(RuntimeError):
    """Erro seguro durante uma operação com execuções de coleta."""


class ConflitoExecucaoColetaMongoDB(ErroRepositorioColetasMongoDB):
    """Indica conflito no identificador de uma execução."""


class RepositorioColetasMongoDB:
    """Salva e consulta execuções realizadas pelos conectores."""

    def __init__(self, banco: Database) -> None:
        """Seleciona a coleção sem executar consultas."""

        self._colecao: Collection = banco[COLECAO_EXECUCOES_COLETA]

    def salvar(self, execucao: ExecucaoColeta) -> ExecucaoColeta:
        """Insere uma execução ou atualiza seu andamento."""

        # Converte o modelo validado em documento BSON.
        documento = modelo_para_documento(execucao)

        # O UUID será utilizado como _id no MongoDB.
        execucao_id = documento.pop("_id")

        # O horário inicial não deve mudar durante a execução.
        iniciado_em = documento.pop("iniciado_em")

        try:
            documento_salvo = self._colecao.find_one_and_update(
                {"_id": execucao_id},
                {
                    # Atualiza status, métricas, checkpoint e finalização.
                    "$set": documento,
                    # Estes campos são definidos somente na inserção.
                    "$setOnInsert": {
                        "_id": execucao_id,
                        "iniciado_em": iniciado_em,
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )

        except DuplicateKeyError as erro_original:
            raise ConflitoExecucaoColetaMongoDB(
                "já existe uma execução com identificador conflitante"
            ) from erro_original

        except PyMongoError as erro_original:
            raise ErroRepositorioColetasMongoDB(
                "não foi possível salvar a execução de coleta"
            ) from erro_original

        if documento_salvo is None:
            raise ErroRepositorioColetasMongoDB("o MongoDB não devolveu a execução salva")

        return documento_para_modelo(
            documento_salvo,
            ExecucaoColeta,
        )

    def buscar_por_id(
        self,
        execucao_id: UUID,
    ) -> ExecucaoColeta | None:
        """Procura uma execução pelo UUID interno."""

        try:
            documento = self._colecao.find_one({"_id": execucao_id})

        except PyMongoError as erro_original:
            raise ErroRepositorioColetasMongoDB(
                "não foi possível consultar a execução de coleta"
            ) from erro_original

        if documento is None:
            return None

        return documento_para_modelo(
            documento,
            ExecucaoColeta,
        )

    def listar_recentes(
        self,
        fonte: Fonte | None = None,
        limite: int = 50,
    ) -> list[ExecucaoColeta]:
        """Lista as execuções mais recentes."""

        # Impede uma consulta sem limite prático.
        if limite < 1 or limite > 1000:
            raise ValueError("limite deve estar entre 1 e 1000")

        # Sem fonte, lista execuções de todos os conectores.
        filtro: dict[str, object] = {}

        # Com fonte, seleciona somente aquele conector.
        if fonte is not None:
            filtro["fonte"] = fonte.value

        try:
            cursor = (
                self._colecao.find(filtro)
                .sort(
                    "iniciado_em",
                    DESCENDING,
                )
                .limit(limite)
            )

            return [
                documento_para_modelo(
                    documento,
                    ExecucaoColeta,
                )
                for documento in cursor
            ]

        except PyMongoError as erro_original:
            raise ErroRepositorioColetasMongoDB(
                "não foi possível listar as execuções de coleta"
            ) from erro_original
