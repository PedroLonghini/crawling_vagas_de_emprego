"""Implementação MongoDB do repositório de vagas canônicas."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pymongo import DESCENDING, ReturnDocument, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import BulkWriteError, DuplicateKeyError, PyMongoError

from observatorio_vagas.domain.vaga import VagaCanonica
from observatorio_vagas.storage.contracts import ResultadoEscrita
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)
from observatorio_vagas.storage.mongodb.schema import COLECAO_VAGAS_CANONICAS


class ErroRepositorioVagasMongoDB(RuntimeError):
    """Erro seguro durante uma operação com vagas canônicas."""


class ConflitoVagaMongoDB(ErroRepositorioVagasMongoDB):
    """Indica um conflito de identidade durante a gravação."""


class RepositorioVagasMongoDB:
    """Salva e consulta vagas canônicas no MongoDB."""

    def __init__(self, banco: Database) -> None:
        """Seleciona a coleção sem executar nenhuma consulta."""

        # O banco já chega conectado.
        # Esta classe precisa conhecer somente sua coleção.
        self._colecao: Collection = banco[COLECAO_VAGAS_CANONICAS]

    def salvar(self, vaga: VagaCanonica) -> VagaCanonica:
        """Insere uma vaga nova ou atualiza uma vaga existente."""

        # Converte UUIDs, enumerações, datas e salários para BSON.
        documento = modelo_para_documento(vaga)

        # O UUID será usado como identificador do MongoDB.
        vaga_id = documento.pop("_id")

        # A data de criação só pode ser definida na primeira inserção.
        criado_em = documento.pop("criado_em")

        try:
            documento_salvo = self._colecao.find_one_and_update(
                {"_id": vaga_id},
                {
                    # Atualiza as informações atuais da vaga.
                    "$set": documento,
                    # Estes campos são aplicados somente na inserção.
                    "$setOnInsert": {
                        "_id": vaga_id,
                        "criado_em": criado_em,
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )

        except DuplicateKeyError as erro_original:
            raise ConflitoVagaMongoDB(
                "já existe uma vaga canônica com identificador conflitante"
            ) from erro_original

        except PyMongoError as erro_original:
            raise ErroRepositorioVagasMongoDB(
                "não foi possível salvar a vaga canônica no MongoDB"
            ) from erro_original

        if documento_salvo is None:
            raise ErroRepositorioVagasMongoDB("o MongoDB não devolveu a vaga canônica salva")

        # Reconstrói e valida novamente o modelo.
        return documento_para_modelo(
            documento_salvo,
            VagaCanonica,
        )

    def salvar_lote(
        self,
        vagas: Sequence[VagaCanonica],
    ) -> ResultadoEscrita:
        """Salva várias vagas em uma única chamada ao MongoDB."""

        # Um lote vazio não precisa acessar o banco.
        if not vagas:
            return ResultadoEscrita(
                recebidos=0,
                inseridos=0,
                atualizados=0,
                inalterados=0,
            )

        operacoes: list[UpdateOne] = []

        for vaga in vagas:
            documento = modelo_para_documento(vaga)
            vaga_id = documento.pop("_id")
            criado_em = documento.pop("criado_em")

            operacoes.append(
                UpdateOne(
                    {"_id": vaga_id},
                    {
                        "$set": documento,
                        "$setOnInsert": {
                            "_id": vaga_id,
                            "criado_em": criado_em,
                        },
                    },
                    upsert=True,
                )
            )

        try:
            resultado = self._colecao.bulk_write(
                operacoes,
                ordered=False,
            )

        except BulkWriteError as erro_original:
            raise ConflitoVagaMongoDB(
                "o lote possui vagas canônicas conflitantes"
            ) from erro_original

        except PyMongoError as erro_original:
            raise ErroRepositorioVagasMongoDB(
                "não foi possível salvar o lote de vagas canônicas"
            ) from erro_original

        # Documentos criados pelo upsert.
        inseridos = resultado.upserted_count

        # Documentos existentes cujo conteúdo mudou.
        atualizados = resultado.modified_count

        # Documentos que já possuíam o mesmo conteúdo.
        inalterados = len(vagas) - inseridos - atualizados

        return ResultadoEscrita(
            recebidos=len(vagas),
            inseridos=inseridos,
            atualizados=atualizados,
            inalterados=inalterados,
        )

    def buscar_por_id(
        self,
        vaga_id: UUID,
    ) -> VagaCanonica | None:
        """Procura uma vaga canônica pelo UUID."""

        try:
            documento = self._colecao.find_one({"_id": vaga_id})

        except PyMongoError as erro_original:
            raise ErroRepositorioVagasMongoDB(
                "não foi possível consultar a vaga canônica"
            ) from erro_original

        if documento is None:
            return None

        return documento_para_modelo(
            documento,
            VagaCanonica,
        )

    def listar_por_empresa(
        self,
        empresa_id: UUID,
        limite: int = 100,
    ) -> list[VagaCanonica]:
        """Lista as vagas normalizadas de uma empresa."""

        # Evita carregar uma quantidade ilimitada na memória.
        if limite < 1 or limite > 1000:
            raise ValueError("limite deve estar entre 1 e 1000")

        try:
            cursor = (
                self._colecao.find({"empresa_id": empresa_id})
                .sort(
                    "atualizado_em",
                    DESCENDING,
                )
                .limit(limite)
            )

            return [
                documento_para_modelo(
                    documento,
                    VagaCanonica,
                )
                for documento in cursor
            ]

        except PyMongoError as erro_original:
            raise ErroRepositorioVagasMongoDB(
                "não foi possível listar as vagas da empresa"
            ) from erro_original
