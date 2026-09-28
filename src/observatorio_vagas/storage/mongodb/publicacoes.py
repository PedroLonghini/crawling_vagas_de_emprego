"""Repositório MongoDB do histórico idempotente de publicações."""

from __future__ import annotations

import re
from uuid import UUID

from pymongo import DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.storage.contracts import ResultadoReservaPublicacao
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_PUBLICACOES_EMPREGOS,
)


class ErroRepositorioPublicacoesMongoDB(RuntimeError):
    """Erro seguro durante o acesso ao histórico de publicações."""


class ConflitoPublicacaoMongoDB(ErroRepositorioPublicacoesMongoDB):
    """A identidade da operação já existe com outro conteúdo."""


class TransicaoPublicacaoMongoDBInvalida(ErroRepositorioPublicacoesMongoDB):
    """Outro processo alterou o estado antes desta transição."""


class RepositorioPublicacoesEmpregosMongoDB:
    """Reserva e atualiza publicações sem duplicar uma operação lógica."""

    def __init__(self, banco: Database) -> None:
        """Seleciona a coleção sem executar consultas."""

        self._colecao: Collection = banco[COLECAO_PUBLICACOES_EMPREGOS]

    def reservar(
        self,
        operacao: OperacaoPublicacaoEmpregos,
    ) -> ResultadoReservaPublicacao:
        """Insere uma preparação uma única vez, mesmo sob concorrência."""

        if operacao.situacao is not SituacaoPublicacaoEmpregos.PREPARADA:
            raise ValueError("somente uma operação preparada pode ser reservada")

        documento = modelo_para_documento(operacao)
        chave = operacao.chave_idempotencia

        if chave is None:
            raise ValueError("a operação não possui chave de idempotência")

        try:
            anterior = self._colecao.find_one_and_update(
                {"chave_idempotencia": chave},
                {"$setOnInsert": documento},
                upsert=True,
                return_document=ReturnDocument.BEFORE,
            )

        except DuplicateKeyError:
            # Outra execução pode ter vencido a corrida entre o filtro e o
            # índice único. Nesse caso, recuperamos o registro vencedor.
            anterior = self._buscar_documento_por_chave(chave)

        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível reservar a publicação"
            ) from erro_original

        if anterior is None:
            salvo = self._buscar_documento_por_chave(chave)

            if salvo is None:
                raise ErroRepositorioPublicacoesMongoDB(
                    "o MongoDB não devolveu a publicação reservada"
                )

            return ResultadoReservaPublicacao(
                operacao=documento_para_modelo(salvo, OperacaoPublicacaoEmpregos),
                criada=True,
            )

        existente = documento_para_modelo(anterior, OperacaoPublicacaoEmpregos)
        self._validar_mesma_operacao(existente, operacao)
        return ResultadoReservaPublicacao(operacao=existente, criada=False)

    def salvar_transicao(
        self,
        operacao: OperacaoPublicacaoEmpregos,
        *,
        situacao_anterior: SituacaoPublicacaoEmpregos,
    ) -> OperacaoPublicacaoEmpregos:
        """Atualiza o estado usando controle otimista de concorrência."""

        campos_mutaveis = {
            "situacao": operacao.situacao.value,
            "tentativas": operacao.tentativas,
            "status_http": operacao.status_http,
            "request_id": operacao.request_id,
            "erro_resumido": operacao.erro_resumido,
            "atualizado_em": operacao.atualizado_em,
            "enviado_em": operacao.enviado_em,
            "finalizado_em": operacao.finalizado_em,
        }

        try:
            documento = self._colecao.find_one_and_update(
                {
                    "_id": operacao.id,
                    "situacao": situacao_anterior.value,
                },
                {"$set": campos_mutaveis},
                upsert=False,
                return_document=ReturnDocument.AFTER,
            )

        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível atualizar a publicação"
            ) from erro_original

        if documento is None:
            existente = self.buscar_por_id(operacao.id)

            if existente is None:
                raise ErroRepositorioPublicacoesMongoDB("publicação não encontrada")

            raise TransicaoPublicacaoMongoDBInvalida("a publicação foi alterada por outro processo")

        return documento_para_modelo(documento, OperacaoPublicacaoEmpregos)

    def buscar_por_id(
        self,
        operacao_id: UUID,
    ) -> OperacaoPublicacaoEmpregos | None:
        """Procura uma operação pelo UUID interno."""

        try:
            documento = self._colecao.find_one({"_id": operacao_id})
        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível consultar a publicação"
            ) from erro_original

        if documento is None:
            return None

        return documento_para_modelo(documento, OperacaoPublicacaoEmpregos)

    def buscar_por_chave(
        self,
        chave_idempotencia: str,
    ) -> OperacaoPublicacaoEmpregos | None:
        """Procura uma operação por sua identidade lógica."""

        chave = chave_idempotencia.strip().casefold()

        if re.fullmatch(r"[0-9a-f]{64}", chave) is None:
            raise ValueError("chave_idempotencia deve ser um SHA-256 hexadecimal")

        documento = self._buscar_documento_por_chave(chave)

        if documento is None:
            return None

        return documento_para_modelo(documento, OperacaoPublicacaoEmpregos)

    def listar_por_vaga(
        self,
        vaga_id: UUID,
        limite: int = 100,
    ) -> list[OperacaoPublicacaoEmpregos]:
        """Lista o histórico recente de uma vaga."""

        if limite < 1 or limite > 1000:
            raise ValueError("limite deve estar entre 1 e 1000")

        try:
            cursor = (
                self._colecao.find({"vaga_id": vaga_id}).sort("criado_em", DESCENDING).limit(limite)
            )
            return [
                documento_para_modelo(documento, OperacaoPublicacaoEmpregos) for documento in cursor
            ]
        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível listar as publicações da vaga"
            ) from erro_original

    def _buscar_documento_por_chave(self, chave: str) -> dict[str, object] | None:
        """Consulta interna que converte falhas técnicas em erro seguro."""

        try:
            return self._colecao.find_one({"chave_idempotencia": chave})
        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível consultar a publicação"
            ) from erro_original

    @staticmethod
    def _validar_mesma_operacao(
        existente: OperacaoPublicacaoEmpregos,
        recebida: OperacaoPublicacaoEmpregos,
    ) -> None:
        """Bloqueia a reutilização da chave com payload ou vínculos diferentes."""

        identidade_existente = (
            existente.vaga_id,
            existente.anuncio_id,
            existente.payload_sha256,
        )
        identidade_recebida = (
            recebida.vaga_id,
            recebida.anuncio_id,
            recebida.payload_sha256,
        )

        if identidade_existente != identidade_recebida:
            raise ConflitoPublicacaoMongoDB(
                "a operação idempotente já existe com conteúdo diferente"
            )
