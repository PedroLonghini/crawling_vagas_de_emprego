"""Testes do repositório de execuções de coleta no MongoDB."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from observatorio_vagas.domain.coleta import ExecucaoColeta, MetricasColeta
from observatorio_vagas.domain.enums import Fonte, StatusColeta
from observatorio_vagas.storage.contracts import RepositorioColetas
from observatorio_vagas.storage.mongodb.coletas import (
    ConflitoExecucaoColetaMongoDB,
    RepositorioColetasMongoDB,
)
from observatorio_vagas.storage.mongodb.schema import COLECAO_EXECUCOES_COLETA

DATA_INICIAL = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
DATA_INTERMEDIARIA = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)
DATA_RECENTE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class CursorColetasFalso:
    """Simula o cursor retornado pelo MongoDB."""

    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        """Recebe os documentos encontrados."""

        self.documentos = [dict(documento) for documento in documentos]

    def sort(self, campo: str, direcao: int) -> CursorColetasFalso:
        """Ordena as execuções mais recentes primeiro."""

        assert campo == "iniciado_em"
        assert direcao == DESCENDING

        self.documentos.sort(
            key=lambda documento: documento[campo],
            reverse=True,
        )

        return self

    def limit(self, quantidade: int) -> CursorColetasFalso:
        """Limita a quantidade de documentos devolvidos."""

        self.documentos = self.documentos[:quantidade]
        return self

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Permite percorrer o cursor."""

        return iter(self.documentos)


class ColecaoColetasFalsa:
    """Simula as operações utilizadas pelo repositório."""

    def __init__(self) -> None:
        """Começa sem documentos."""

        self.documentos: dict[UUID, dict[str, Any]] = {}

    def find_one_and_update(
        self,
        filtro: dict[str, object],
        atualizacao: dict[str, dict[str, Any]],
        *,
        upsert: bool,
        return_document: bool,
    ) -> dict[str, Any]:
        """Simula uma inserção ou atualização atômica."""

        assert upsert is True
        assert return_document == ReturnDocument.AFTER

        execucao_id = filtro["_id"]

        assert isinstance(execucao_id, UUID)

        documento_existente = self.documentos.get(execucao_id)

        if documento_existente is None:
            documento_salvo = dict(atualizacao["$setOnInsert"])
            documento_salvo.update(atualizacao["$set"])
            self.documentos[execucao_id] = documento_salvo
        else:
            documento_existente.update(atualizacao["$set"])

        return dict(self.documentos[execucao_id])

    def find_one(
        self,
        filtro: dict[str, object],
    ) -> dict[str, Any] | None:
        """Procura uma execução pela igualdade dos campos."""

        for documento in self.documentos.values():
            corresponde = all(documento.get(campo) == valor for campo, valor in filtro.items())

            if corresponde:
                return dict(documento)

        return None

    def find(
        self,
        filtro: dict[str, object],
    ) -> CursorColetasFalso:
        """Procura todas as execuções correspondentes."""

        encontrados = []

        for documento in self.documentos.values():
            corresponde = all(documento.get(campo) == valor for campo, valor in filtro.items())

            if corresponde:
                encontrados.append(dict(documento))

        return CursorColetasFalso(encontrados)


class BancoFalso:
    """Simula a seleção da coleção de execuções."""

    def __init__(self, colecao: ColecaoColetasFalsa | None = None) -> None:
        """Permite utilizar uma coleção preparada pelo teste."""

        if colecao is None:
            colecao = ColecaoColetasFalsa()

        self.colecao = colecao
        self.nome_selecionado: str | None = None

    def __getitem__(self, nome: str) -> ColecaoColetasFalsa:
        """Registra o nome da coleção selecionada."""

        self.nome_selecionado = nome
        return self.colecao


def criar_execucao(
    *,
    execucao_id: UUID | None = None,
    fonte: Fonte = Fonte.GUPY,
    status: StatusColeta = StatusColeta.PENDENTE,
    iniciado_em: datetime = DATA_INICIAL,
    finalizado_em: datetime | None = None,
    recebidos: int = 0,
) -> ExecucaoColeta:
    """Cria uma execução de coleta válida."""

    metricas = MetricasColeta(
        requisicoes=1 if recebidos else 0,
        recebidos=recebidos,
        novos=recebidos,
    )

    dados: dict[str, Any] = {
        "fonte": fonte,
        "versao_conector": "0.1.0",
        "status": status,
        "iniciado_em": iniciado_em,
        "finalizado_em": finalizado_em,
        "checkpoint": {"pagina": 1},
        "metricas": metricas,
    }

    if execucao_id is not None:
        dados["id"] = execucao_id

    return ExecucaoColeta.model_validate(dados)


def test_repositorio_atende_ao_contrato() -> None:
    """A implementação deve atender ao contrato de coletas."""

    banco = BancoFalso()
    repositorio = RepositorioColetasMongoDB(banco)

    assert isinstance(repositorio, RepositorioColetas)
    assert banco.nome_selecionado == COLECAO_EXECUCOES_COLETA


def test_salvar_atualizar_e_buscar_execucao() -> None:
    """A finalização deve preservar o horário inicial."""

    execucao_id = UUID("a0000000-0000-4000-8000-00000000000a")

    colecao = ColecaoColetasFalsa()
    repositorio = RepositorioColetasMongoDB(BancoFalso(colecao))

    execucao_inicial = criar_execucao(
        execucao_id=execucao_id,
        status=StatusColeta.EXECUTANDO,
        iniciado_em=DATA_INICIAL,
    )

    execucao_salva = repositorio.salvar(execucao_inicial)

    assert execucao_salva.id == execucao_id
    assert execucao_salva.iniciado_em == DATA_INICIAL

    execucao_finalizada = criar_execucao(
        execucao_id=execucao_id,
        status=StatusColeta.CONCLUIDA,
        iniciado_em=DATA_INTERMEDIARIA,
        finalizado_em=DATA_RECENTE,
        recebidos=10,
    )

    resultado = repositorio.salvar(execucao_finalizada)

    assert resultado.id == execucao_id
    assert resultado.status == StatusColeta.CONCLUIDA
    assert resultado.iniciado_em == DATA_INICIAL
    assert resultado.finalizado_em == DATA_RECENTE
    assert resultado.metricas.recebidos == 10
    assert resultado.metricas.novos == 10
    assert len(colecao.documentos) == 1
    assert repositorio.buscar_por_id(execucao_id) == resultado


def test_listar_recentes_filtra_por_fonte() -> None:
    """A listagem deve filtrar e ordenar as execuções."""

    repositorio = RepositorioColetasMongoDB(BancoFalso())

    id_gupy_antigo = UUID("a1000000-0000-4000-8000-000000000001")
    id_gupy_recente = UUID("a2000000-0000-4000-8000-000000000002")
    id_empregos = UUID("a3000000-0000-4000-8000-000000000003")

    repositorio.salvar(
        criar_execucao(
            execucao_id=id_gupy_antigo,
            fonte=Fonte.GUPY,
            iniciado_em=DATA_INICIAL,
        )
    )

    repositorio.salvar(
        criar_execucao(
            execucao_id=id_empregos,
            fonte=Fonte.EMPREGOS,
            iniciado_em=DATA_RECENTE,
        )
    )

    repositorio.salvar(
        criar_execucao(
            execucao_id=id_gupy_recente,
            fonte=Fonte.GUPY,
            iniciado_em=DATA_INTERMEDIARIA,
        )
    )

    encontradas = repositorio.listar_recentes(
        fonte=Fonte.GUPY,
        limite=10,
    )

    assert [execucao.id for execucao in encontradas] == [
        id_gupy_recente,
        id_gupy_antigo,
    ]


def test_listar_sem_fonte_retorna_todas() -> None:
    """Sem filtro, todas as fontes devem participar."""

    repositorio = RepositorioColetasMongoDB(BancoFalso())

    repositorio.salvar(
        criar_execucao(
            fonte=Fonte.GUPY,
            iniciado_em=DATA_INICIAL,
        )
    )

    repositorio.salvar(
        criar_execucao(
            fonte=Fonte.EMPREGOS,
            iniciado_em=DATA_RECENTE,
        )
    )

    encontradas = repositorio.listar_recentes(limite=10)

    assert len(encontradas) == 2
    assert encontradas[0].fonte == Fonte.EMPREGOS
    assert encontradas[1].fonte == Fonte.GUPY


def test_limite_invalido_e_rejeitado() -> None:
    """O limite deve permanecer dentro da faixa segura."""

    repositorio = RepositorioColetasMongoDB(BancoFalso())

    with pytest.raises(ValueError, match="entre 1 e 1000"):
        repositorio.listar_recentes(limite=0)

    with pytest.raises(ValueError, match="entre 1 e 1000"):
        repositorio.listar_recentes(limite=1001)


def test_conflito_vira_erro_seguro() -> None:
    """Um conflito técnico deve produzir uma mensagem segura."""

    class ColecaoComConflito(ColecaoColetasFalsa):
        """Simula um índice recusando a execução."""

        def find_one_and_update(
            self,
            filtro: dict[str, object],
            atualizacao: dict[str, dict[str, Any]],
            *,
            upsert: bool,
            return_document: bool,
        ) -> dict[str, Any]:
            """Interrompe a gravação com conflito."""

            raise DuplicateKeyError("identificador duplicado")

    repositorio = RepositorioColetasMongoDB(BancoFalso(ColecaoComConflito()))

    with pytest.raises(
        ConflitoExecucaoColetaMongoDB,
        match="identificador conflitante",
    ):
        repositorio.salvar(criar_execucao())
