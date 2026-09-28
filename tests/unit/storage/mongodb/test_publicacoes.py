"""Testes do repositório idempotente de publicações no Empregos."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pymongo import DESCENDING, ReturnDocument

from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.storage.contracts import RepositorioPublicacoesEmpregos
from observatorio_vagas.storage.mongodb.publicacoes import (
    ConflitoPublicacaoMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    TransicaoPublicacaoMongoDBInvalida,
)
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_PUBLICACOES_EMPREGOS,
)

VAGA_ID = UUID("93000000-0000-4000-8000-000000000093")
ANUNCIO_ID = UUID("94000000-0000-4000-8000-000000000094")
DATA_INICIAL = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
DATA_ENVIO = datetime(2026, 8, 31, 10, 1, tzinfo=UTC)
DATA_FINAL = datetime(2026, 8, 31, 10, 2, tzinfo=UTC)


class CursorPublicacoesFalso:
    """Simula ordenação e limite de um cursor do MongoDB."""

    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        self.documentos = [dict(documento) for documento in documentos]

    def sort(self, campo: str, direcao: int) -> CursorPublicacoesFalso:
        assert campo == "criado_em"
        assert direcao == DESCENDING
        self.documentos.sort(key=lambda item: item[campo], reverse=True)
        return self

    def limit(self, quantidade: int) -> CursorPublicacoesFalso:
        self.documentos = self.documentos[:quantidade]
        return self

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.documentos)


class ColecaoPublicacoesFalsa:
    """Executa em memória as operações usadas pelo repositório."""

    def __init__(self) -> None:
        self.documentos: dict[UUID, dict[str, Any]] = {}

    def find_one_and_update(
        self,
        filtro: dict[str, object],
        atualizacao: dict[str, dict[str, Any]],
        *,
        upsert: bool,
        return_document: bool,
    ) -> dict[str, Any] | None:
        anterior = self.find_one(filtro)

        if anterior is None:
            if not upsert:
                return None

            documento = dict(atualizacao["$setOnInsert"])
            operacao_id = documento["_id"]
            assert isinstance(operacao_id, UUID)
            self.documentos[operacao_id] = documento
            return None

        operacao_id = anterior["_id"]
        assert isinstance(operacao_id, UUID)

        if "$set" in atualizacao:
            self.documentos[operacao_id].update(atualizacao["$set"])

        if return_document == ReturnDocument.AFTER:
            return dict(self.documentos[operacao_id])

        return anterior

    def find_one(self, filtro: dict[str, object]) -> dict[str, Any] | None:
        for documento in self.documentos.values():
            if all(documento.get(campo) == valor for campo, valor in filtro.items()):
                return dict(documento)
        return None

    def find(self, filtro: dict[str, object]) -> CursorPublicacoesFalso:
        documentos = [
            documento
            for documento in self.documentos.values()
            if all(documento.get(campo) == valor for campo, valor in filtro.items())
        ]
        return CursorPublicacoesFalso(documentos)


class BancoFalso:
    """Registra a coleção selecionada pelo repositório."""

    def __init__(self) -> None:
        self.colecao = ColecaoPublicacoesFalsa()
        self.nome_selecionado: str | None = None

    def __getitem__(self, nome: str) -> ColecaoPublicacoesFalsa:
        self.nome_selecionado = nome
        return self.colecao


def criar_operacao(
    *,
    operacao_id: UUID | None = None,
    payload_sha256: str = "a" * 64,
    criado_em: datetime = DATA_INICIAL,
) -> OperacaoPublicacaoEmpregos:
    """Cria uma preparação válida para os testes."""

    dados: dict[str, object] = {
        "vaga_id": VAGA_ID,
        "anuncio_id": ANUNCIO_ID,
        "external_job_posting_id": "VAGA-001",
        "operation_type": "CREATE",
        "payload_sha256": payload_sha256,
        "criado_em": criado_em,
        "atualizado_em": criado_em,
    }

    if operacao_id is not None:
        dados["id"] = operacao_id

    return OperacaoPublicacaoEmpregos.model_validate(dados)


def test_repositorio_atende_ao_contrato() -> None:
    """A implementação fornece todas as operações declaradas."""

    banco = BancoFalso()
    repositorio = RepositorioPublicacoesEmpregosMongoDB(banco)

    assert isinstance(repositorio, RepositorioPublicacoesEmpregos)
    assert banco.nome_selecionado == COLECAO_PUBLICACOES_EMPREGOS


def test_reserva_e_reutiliza_a_mesma_operacao() -> None:
    """Executar a reserva duas vezes não cria dois documentos."""

    banco = BancoFalso()
    repositorio = RepositorioPublicacoesEmpregosMongoDB(banco)
    operacao = criar_operacao()

    primeira = repositorio.reservar(operacao)
    segunda = repositorio.reservar(operacao)

    assert primeira.criada is True
    assert segunda.criada is False
    assert segunda.operacao.id == primeira.operacao.id
    assert len(banco.colecao.documentos) == 1


def test_mesma_chave_com_payload_diferente_vira_conflito() -> None:
    """Alterar o CREATE exige decisão explícita, não outra publicação."""

    repositorio = RepositorioPublicacoesEmpregosMongoDB(BancoFalso())
    repositorio.reservar(criar_operacao(payload_sha256="a" * 64))

    with pytest.raises(ConflitoPublicacaoMongoDB, match="conteúdo diferente"):
        repositorio.reservar(criar_operacao(payload_sha256="b" * 64))


def test_salva_transicoes_com_controle_de_estado() -> None:
    """O histórico passa por preparada, enviando e sucesso."""

    repositorio = RepositorioPublicacoesEmpregosMongoDB(BancoFalso())
    preparada = repositorio.reservar(criar_operacao()).operacao
    enviando = preparada.iniciar_envio(momento=DATA_ENVIO)
    enviando = repositorio.salvar_transicao(
        enviando,
        situacao_anterior=SituacaoPublicacaoEmpregos.PREPARADA,
    )
    sucesso = enviando.concluir(
        situacao=SituacaoPublicacaoEmpregos.SUCESSO,
        momento=DATA_FINAL,
        status_http=201,
        request_id="req-001",
    )
    sucesso = repositorio.salvar_transicao(
        sucesso,
        situacao_anterior=SituacaoPublicacaoEmpregos.ENVIANDO,
    )

    assert sucesso.situacao is SituacaoPublicacaoEmpregos.SUCESSO
    assert repositorio.buscar_por_id(sucesso.id) == sucesso
    assert repositorio.buscar_por_chave(str(sucesso.chave_idempotencia)) == sucesso


def test_rejeita_transicao_com_estado_anterior_desatualizado() -> None:
    """Dois processos não podem concluir a mesma etapa simultaneamente."""

    repositorio = RepositorioPublicacoesEmpregosMongoDB(BancoFalso())
    preparada = repositorio.reservar(criar_operacao()).operacao
    enviando = preparada.iniciar_envio(momento=DATA_ENVIO)
    repositorio.salvar_transicao(
        enviando,
        situacao_anterior=SituacaoPublicacaoEmpregos.PREPARADA,
    )

    with pytest.raises(TransicaoPublicacaoMongoDBInvalida, match="outro processo"):
        repositorio.salvar_transicao(
            enviando,
            situacao_anterior=SituacaoPublicacaoEmpregos.PREPARADA,
        )


def test_lista_historico_recente_da_vaga() -> None:
    """A consulta respeita vaga, ordem e limite."""

    repositorio = RepositorioPublicacoesEmpregosMongoDB(BancoFalso())
    repositorio.reservar(
        criar_operacao(
            operacao_id=UUID("95000000-0000-4000-8000-000000000095"),
            criado_em=DATA_INICIAL,
        )
    )

    segunda = criar_operacao(
        operacao_id=UUID("96000000-0000-4000-8000-000000000096"),
        criado_em=DATA_ENVIO,
    )
    dados_segunda = segunda.model_dump(mode="python")
    dados_segunda["external_job_posting_id"] = "VAGA-002"
    dados_segunda["chave_idempotencia"] = None
    repositorio.reservar(OperacaoPublicacaoEmpregos.model_validate(dados_segunda))

    resultado = repositorio.listar_por_vaga(VAGA_ID, limite=1)

    assert len(resultado) == 1
    assert resultado[0].external_job_posting_id == "VAGA-002"
