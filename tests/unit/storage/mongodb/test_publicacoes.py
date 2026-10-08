"""Testes do repositório idempotente de publicações no Empregos."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.storage.contracts import RepositorioPublicacoesEmpregos
from observatorio_vagas.storage.mongodb import publicacoes as modulo_publicacoes
from observatorio_vagas.storage.mongodb.publicacoes import (
    COLECAO_TRAVAS,
    NOME_TRAVA_PUBLICACAO,
    ConflitoPublicacaoMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    TransicaoPublicacaoMongoDBInvalida,
    TravaPublicacaoOcupada,
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
        self.documentos.sort(key=lambda item: item[campo], reverse=direcao == DESCENDING)
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
        def casa(atual: object, esperado: object) -> bool:
            if isinstance(esperado, dict) and "$in" in esperado:
                return atual in esperado["$in"]
            return atual == esperado

        documentos = [
            documento
            for documento in self.documentos.values()
            if all(casa(documento.get(campo), valor) for campo, valor in filtro.items())
        ]
        return CursorPublicacoesFalso(documentos)


class ColecaoTravasFalsa:
    """Simula o upsert condicional usado pela trava de publicação."""

    def __init__(self) -> None:
        self.documentos: dict[str, dict[str, Any]] = {}

    def update_one(
        self,
        filtro: dict[str, Any],
        atualizacao: dict[str, dict[str, Any]],
        *,
        upsert: bool = False,
    ) -> None:
        atual = self.documentos.get(filtro["_id"])
        if atual is None:
            if upsert:
                self.documentos[filtro["_id"]] = dict(atualizacao["$set"])
            return
        if "$or" in filtro:
            livre = atual.get("dono") is None or (
                atual.get("expira_em") is not None
                and atual["expira_em"] < filtro["$or"][1]["expira_em"]["$lt"]
            )
        else:
            livre = atual.get("dono") == filtro.get("dono")
        if livre:
            atual.update(atualizacao["$set"])
        elif upsert:
            raise DuplicateKeyError("trava ocupada")


class BancoFalso:
    """Registra a coleção selecionada pelo repositório."""

    def __init__(self) -> None:
        self.colecao = ColecaoPublicacoesFalsa()
        self.travas = ColecaoTravasFalsa()
        self.nome_selecionado: str | None = None

    def __getitem__(self, nome: str) -> ColecaoPublicacoesFalsa | ColecaoTravasFalsa:
        if nome == COLECAO_TRAVAS:
            return self.travas
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


def _reservar_com_assinatura(
    repositorio: RepositorioPublicacoesEmpregosMongoDB,
    *,
    numero: int,
    assinatura: str,
    criado_em: datetime,
    situacao_final: SituacaoPublicacaoEmpregos | None = None,
) -> OperacaoPublicacaoEmpregos:
    operacao = OperacaoPublicacaoEmpregos(
        id=UUID(f"97000000-0000-4000-8000-{numero:012d}"),
        vaga_id=VAGA_ID,
        anuncio_id=ANUNCIO_ID,
        external_job_posting_id=f"VAGA-{numero}",
        operation_type="CREATE",
        payload_sha256="a" * 64,
        assinatura_conteudo=assinatura,
        criado_em=criado_em,
        atualizado_em=criado_em,
    )
    preparada = repositorio.reservar(operacao).operacao
    if situacao_final is None:
        return preparada
    enviando = repositorio.salvar_transicao(
        preparada.iniciar_envio(momento=criado_em),
        situacao_anterior=SituacaoPublicacaoEmpregos.PREPARADA,
    )
    return repositorio.salvar_transicao(
        enviando.concluir(
            situacao=situacao_final,
            momento=criado_em,
            status_http=201 if situacao_final is SituacaoPublicacaoEmpregos.SUCESSO else 400,
        ),
        situacao_anterior=SituacaoPublicacaoEmpregos.ENVIANDO,
    )


def test_lista_publicacoes_no_ar_pela_assinatura() -> None:
    """Rejeitada fica de fora; preparada conta (na dúvida, não publicar de novo)."""

    repositorio = RepositorioPublicacoesEmpregosMongoDB(BancoFalso())
    assinatura = "e" * 64
    recente = _reservar_com_assinatura(
        repositorio,
        numero=1,
        assinatura=assinatura,
        criado_em=DATA_FINAL,
        situacao_final=SituacaoPublicacaoEmpregos.SUCESSO,
    )
    antiga = _reservar_com_assinatura(
        repositorio, numero=2, assinatura=assinatura, criado_em=DATA_INICIAL
    )
    _reservar_com_assinatura(
        repositorio,
        numero=3,
        assinatura=assinatura,
        criado_em=DATA_ENVIO,
        situacao_final=SituacaoPublicacaoEmpregos.REJEITADA,
    )
    _reservar_com_assinatura(repositorio, numero=4, assinatura="f" * 64, criado_em=DATA_ENVIO)

    resultado = repositorio.listar_ativas_por_assinatura(assinatura.upper())

    assert [operacao.id for operacao in resultado] == [antiga.id, recente.id]

    with pytest.raises(ValueError, match="SHA-256"):
        repositorio.listar_ativas_por_assinatura("nao-e-hash")


def test_trava_impede_duas_publicacoes_ao_mesmo_tempo(monkeypatch) -> None:
    """Enquanto um processo confere e reserva, outro espera (e desiste no prazo)."""

    monkeypatch.setattr(modulo_publicacoes, "ESPERA_MAXIMA_TRAVA_S", 0.0)
    banco = BancoFalso()
    primeiro = RepositorioPublicacoesEmpregosMongoDB(banco)
    segundo = RepositorioPublicacoesEmpregosMongoDB(banco)

    with (
        primeiro.trava_publicacao(),
        pytest.raises(TravaPublicacaoOcupada),
        segundo.trava_publicacao(),
    ):
        pass

    # Liberada ao sair, inclusive depois de erro.
    with pytest.raises(RuntimeError), segundo.trava_publicacao():
        raise RuntimeError("falha no meio")
    with primeiro.trava_publicacao():
        assert banco.travas.documentos[NOME_TRAVA_PUBLICACAO]["dono"] is not None
    assert banco.travas.documentos[NOME_TRAVA_PUBLICACAO]["dono"] is None


def test_trava_vencida_de_processo_que_caiu_e_assumida() -> None:
    banco = BancoFalso()
    banco.travas.documentos[NOME_TRAVA_PUBLICACAO] = {
        "dono": "processo-que-caiu",
        "expira_em": datetime(2000, 1, 1, tzinfo=UTC),
    }

    with RepositorioPublicacoesEmpregosMongoDB(banco).trava_publicacao():
        assert banco.travas.documentos[NOME_TRAVA_PUBLICACAO]["dono"] != "processo-que-caiu"


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
