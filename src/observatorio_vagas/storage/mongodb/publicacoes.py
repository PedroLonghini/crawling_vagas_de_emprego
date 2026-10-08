"""Repositório MongoDB do histórico idempotente de publicações."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

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

COLECAO_TRAVAS = "travas"
NOME_TRAVA_PUBLICACAO = "publicacao_empregos"
VALIDADE_TRAVA_PUBLICACAO = timedelta(minutes=2)
ESPERA_MAXIMA_TRAVA_S = 60.0
INTERVALO_TENTATIVA_TRAVA_S = 0.5

SITUACOES_NO_AR = (
    SituacaoPublicacaoEmpregos.PREPARADA,
    SituacaoPublicacaoEmpregos.ENVIANDO,
    SituacaoPublicacaoEmpregos.SUCESSO,
    SituacaoPublicacaoEmpregos.INDETERMINADA,
)


class ErroRepositorioPublicacoesMongoDB(RuntimeError):
    """Erro seguro durante o acesso ao histórico de publicações."""


class ConflitoPublicacaoMongoDB(ErroRepositorioPublicacoesMongoDB):
    """A identidade da operação já existe com outro conteúdo."""


class TransicaoPublicacaoMongoDBInvalida(ErroRepositorioPublicacoesMongoDB):
    """Outro processo alterou o estado antes desta transição."""


class TravaPublicacaoOcupada(ErroRepositorioPublicacoesMongoDB):
    """Outro processo está conferindo e reservando uma publicação agora."""


class RepositorioPublicacoesEmpregosMongoDB:
    """Reserva e atualiza publicações sem duplicar uma operação lógica."""

    def __init__(self, banco: Database) -> None:
        """Seleciona a coleção sem executar consultas."""

        self._banco = banco
        self._colecao: Collection = banco[COLECAO_PUBLICACOES_EMPREGOS]

    @contextmanager
    def trava_publicacao(self) -> Iterator[None]:
        """Só um processo por vez confere duplicadas e reserva uma publicação.

        Sem isto, duas execuções simultâneas (rotina e manual, duas janelas)
        podiam conferir a mesma vaga ao mesmo tempo, não achar a outra e
        publicar as duas cópias. A trava vence sozinha depois de
        ``VALIDADE_TRAVA_PUBLICACAO``, para não ficar presa se o processo cair.
        """

        # A coleção da trava só é selecionada aqui: quem só consulta não a usa.
        travas: Collection = self._banco[COLECAO_TRAVAS]
        dono = uuid4().hex
        prazo = time.monotonic() + ESPERA_MAXIMA_TRAVA_S
        while True:
            agora = datetime.now(UTC)
            try:
                # Com o documento existindo e ocupado, o filtro não casa e o
                # upsert tenta inserir o mesmo _id: DuplicateKeyError = ocupada.
                travas.update_one(
                    {
                        "_id": NOME_TRAVA_PUBLICACAO,
                        "$or": [{"dono": None}, {"expira_em": {"$lt": agora}}],
                    },
                    {"$set": {"dono": dono, "expira_em": agora + VALIDADE_TRAVA_PUBLICACAO}},
                    upsert=True,
                )
                break
            except DuplicateKeyError:
                if time.monotonic() >= prazo:
                    raise TravaPublicacaoOcupada(
                        "outra publicação está em andamento; tente de novo em instantes"
                    ) from None
                time.sleep(INTERVALO_TENTATIVA_TRAVA_S)
            except PyMongoError as erro_original:
                raise ErroRepositorioPublicacoesMongoDB(
                    "não foi possível obter a trava de publicação"
                ) from erro_original
        try:
            yield
        finally:
            # Se a liberação falhar, a trava vence sozinha; não esconder o erro original.
            with suppress(PyMongoError):
                travas.update_one(
                    {"_id": NOME_TRAVA_PUBLICACAO, "dono": dono},
                    {"$set": {"dono": None, "expira_em": None}},
                )

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

    def listar_ativas_por_assinatura(
        self,
        assinatura_conteudo: str,
    ) -> list[OperacaoPublicacaoEmpregos]:
        """Publicações ainda no ar com a mesma assinatura (a mais antiga primeiro).

        Rejeitada pela API não conta. Enquanto não existir a remoção pela API,
        toda operação não rejeitada é tratada como no ar (inclusive preparada,
        em envio ou indeterminada: na dúvida, não publicar de novo). Quem
        chama decide, com ``encontrar_publicacao_da_mesma_vaga``, se é a mesma
        vaga (mesmo site exige a descrição inteira igual).
        """

        assinatura = assinatura_conteudo.strip().casefold()

        if re.fullmatch(r"[0-9a-f]{64}", assinatura) is None:
            raise ValueError("assinatura_conteudo deve ser um SHA-256 hexadecimal")

        try:
            cursor = self._colecao.find(
                {
                    "assinatura_conteudo": assinatura,
                    "situacao": {"$in": [s.value for s in SITUACOES_NO_AR]},
                }
            ).sort("criado_em", 1)
            return [
                documento_para_modelo(documento, OperacaoPublicacaoEmpregos) for documento in cursor
            ]
        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível consultar a publicação"
            ) from erro_original

    def listar_no_ar(
        self,
        situacoes: tuple[SituacaoPublicacaoEmpregos, ...] = SITUACOES_NO_AR,
    ) -> list[OperacaoPublicacaoEmpregos]:
        """Todas as publicações que ainda contam como no ar (varredura semanal)."""

        try:
            cursor = self._colecao.find({"situacao": {"$in": [s.value for s in situacoes]}}).sort(
                "criado_em", 1
            )
            return [
                documento_para_modelo(documento, OperacaoPublicacaoEmpregos) for documento in cursor
            ]
        except PyMongoError as erro_original:
            raise ErroRepositorioPublicacoesMongoDB(
                "não foi possível listar as publicações no ar"
            ) from erro_original

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
