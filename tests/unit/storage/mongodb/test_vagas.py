"""Testes do repositório MongoDB de vagas canônicas."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from observatorio_vagas.domain.enums import (
    ModalidadeTrabalho,
    NaturezaSalario,
    PeriodoSalario,
    RegimeContratacao,
    Senioridade,
)
from observatorio_vagas.domain.vaga import SalarioNormalizado, VagaCanonica
from observatorio_vagas.storage.contracts import RepositorioVagas
from observatorio_vagas.storage.mongodb.schema import COLECAO_VAGAS_CANONICAS
from observatorio_vagas.storage.mongodb.vagas import (
    ConflitoVagaMongoDB,
    RepositorioVagasMongoDB,
)

# Identificador da empresa usada nos testes.
EMPRESA_ID = UUID("80000000-0000-4000-8000-000000000008")

# Datas fixas tornam os testes previsíveis.
DATA_INICIAL = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
DATA_INTERMEDIARIA = datetime(2026, 8, 20, 11, 0, tzinfo=UTC)
DATA_RECENTE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@dataclass
class ResultadoBulkFalso:
    """Simula os contadores devolvidos pelo MongoDB."""

    upserted_count: int
    modified_count: int


class CursorVagasFalso:
    """Simula o cursor devolvido pelo método find."""

    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        """Recebe os documentos encontrados."""

        self.documentos = [dict(documento) for documento in documentos]

    def sort(self, campo: str, direcao: int) -> CursorVagasFalso:
        """Ordena os documentos pelo campo solicitado."""

        assert campo == "atualizado_em"
        assert direcao == DESCENDING

        self.documentos.sort(
            key=lambda documento: documento[campo],
            reverse=True,
        )

        return self

    def limit(self, quantidade: int) -> CursorVagasFalso:
        """Limita a quantidade de documentos."""

        self.documentos = self.documentos[:quantidade]
        return self

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Permite percorrer o cursor com um for."""

        return iter(self.documentos)


class ColecaoVagasFalsa:
    """Simula as operações MongoDB utilizadas pelo repositório."""

    def __init__(self) -> None:
        """Começa sem documentos."""

        self.documentos: dict[UUID, dict[str, Any]] = {}
        self.quantidade_chamadas_bulk = 0
        self.quantidade_operacoes_bulk = 0
        self.ordered_recebido: bool | None = None

        self.resultado_bulk = ResultadoBulkFalso(
            upserted_count=0,
            modified_count=0,
        )

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

        vaga_id = filtro["_id"]

        assert isinstance(vaga_id, UUID)

        documento_existente = self.documentos.get(vaga_id)

        if documento_existente is None:
            documento_salvo = dict(atualizacao["$setOnInsert"])
            documento_salvo.update(atualizacao["$set"])
            self.documentos[vaga_id] = documento_salvo
        else:
            documento_existente.update(atualizacao["$set"])

        return dict(self.documentos[vaga_id])

    def find_one(
        self,
        filtro: dict[str, object],
    ) -> dict[str, Any] | None:
        """Procura um documento pela igualdade dos campos."""

        for documento in self.documentos.values():
            corresponde = all(documento.get(campo) == valor for campo, valor in filtro.items())

            if corresponde:
                return dict(documento)

        return None

    def find(
        self,
        filtro: dict[str, object],
    ) -> CursorVagasFalso:
        """Procura todos os documentos correspondentes."""

        encontrados = []

        for documento in self.documentos.values():
            corresponde = all(documento.get(campo) == valor for campo, valor in filtro.items())

            if corresponde:
                encontrados.append(dict(documento))

        return CursorVagasFalso(encontrados)

    def bulk_write(
        self,
        operacoes: Sequence[object],
        *,
        ordered: bool,
    ) -> ResultadoBulkFalso:
        """Simula uma gravação em lote."""

        self.quantidade_chamadas_bulk += 1
        self.quantidade_operacoes_bulk = len(operacoes)
        self.ordered_recebido = ordered

        return self.resultado_bulk


class BancoFalso:
    """Simula a seleção da coleção de vagas canônicas."""

    def __init__(self, colecao: ColecaoVagasFalsa | None = None) -> None:
        """Permite utilizar uma coleção preparada pelo teste."""

        if colecao is None:
            colecao = ColecaoVagasFalsa()

        self.colecao = colecao
        self.nome_selecionado: str | None = None

    def __getitem__(self, nome: str) -> ColecaoVagasFalsa:
        """Registra qual coleção foi selecionada."""

        self.nome_selecionado = nome
        return self.colecao


def criar_vaga(
    *,
    vaga_id: UUID | None = None,
    empresa_id: UUID = EMPRESA_ID,
    titulo: str = "Pessoa Desenvolvedora Python",
    criado_em: datetime = DATA_INICIAL,
    atualizado_em: datetime = DATA_INICIAL,
) -> VagaCanonica:
    """Cria uma vaga canônica válida para os testes."""

    salario = SalarioNormalizado(
        natureza=NaturezaSalario.PUBLICADO,
        moeda="BRL",
        periodo=PeriodoSalario.MES,
        minimo=Decimal("5000.00"),
        maximo=Decimal("7000.00"),
        mensal_minimo=Decimal("5000.00"),
        mensal_maximo=Decimal("7000.00"),
        regra_normalizacao="valor mensal publicado",
    )

    dados: dict[str, Any] = {
        "empresa_id": empresa_id,
        "titulo_normalizado": titulo,
        "descricao_normalizada": "Desenvolvimento de sistemas em Python.",
        "familia_cargo": "Desenvolvimento de software",
        "area": "Tecnologia",
        "senioridade": Senioridade.PLENO,
        "cidade": "São Paulo",
        "estado": "SP",
        "pais": "BR",
        "modalidade": ModalidadeTrabalho.REMOTO,
        "regime": RegimeContratacao.CLT,
        "salario": salario,
        "quantidade_vagas": 1,
        "tecnologias": ("Python", "MongoDB"),
        "requisitos": ("Experiência com Python",),
        "responsabilidades": ("Desenvolver APIs",),
        "beneficios": ("Plano de saúde",),
        "criado_em": criado_em,
        "atualizado_em": atualizado_em,
    }

    if vaga_id is not None:
        dados["id"] = vaga_id

    return VagaCanonica.model_validate(dados)


def test_repositorio_atende_ao_contrato() -> None:
    """A implementação deve atender ao contrato de vagas."""

    banco = BancoFalso()
    repositorio = RepositorioVagasMongoDB(banco)

    assert isinstance(repositorio, RepositorioVagas)
    assert banco.nome_selecionado == COLECAO_VAGAS_CANONICAS


def test_salvar_atualizar_e_buscar_vaga() -> None:
    """Uma atualização deve preservar o ID e a criação."""

    vaga_id = UUID("81000000-0000-4000-8000-000000000081")

    colecao = ColecaoVagasFalsa()
    repositorio = RepositorioVagasMongoDB(BancoFalso(colecao))

    vaga_original = criar_vaga(
        vaga_id=vaga_id,
        titulo="Pessoa Desenvolvedora Python",
        criado_em=DATA_INICIAL,
        atualizado_em=DATA_INICIAL,
    )

    vaga_salva = repositorio.salvar(vaga_original)

    assert vaga_salva.id == vaga_id
    assert vaga_salva.criado_em == DATA_INICIAL
    assert len(colecao.documentos) == 1

    vaga_atualizada = criar_vaga(
        vaga_id=vaga_id,
        titulo="Pessoa Desenvolvedora Python Sênior",
        criado_em=DATA_INTERMEDIARIA,
        atualizado_em=DATA_INTERMEDIARIA,
    )

    resultado = repositorio.salvar(vaga_atualizada)

    assert resultado.id == vaga_id
    assert resultado.criado_em == DATA_INICIAL
    assert resultado.atualizado_em == DATA_INTERMEDIARIA
    assert resultado.titulo_normalizado == "Pessoa Desenvolvedora Python Sênior"
    assert len(colecao.documentos) == 1
    assert repositorio.buscar_por_id(vaga_id) == resultado


def test_listar_por_empresa_mostra_recentes_primeiro() -> None:
    """A consulta deve ordenar pela atualização mais recente."""

    repositorio = RepositorioVagasMongoDB(BancoFalso())

    repositorio.salvar(
        criar_vaga(
            vaga_id=UUID("82000000-0000-4000-8000-000000000082"),
            titulo="Vaga antiga",
            atualizado_em=DATA_INICIAL,
        )
    )

    repositorio.salvar(
        criar_vaga(
            vaga_id=UUID("83000000-0000-4000-8000-000000000083"),
            titulo="Vaga intermediária",
            atualizado_em=DATA_INTERMEDIARIA,
        )
    )

    repositorio.salvar(
        criar_vaga(
            vaga_id=UUID("84000000-0000-4000-8000-000000000084"),
            titulo="Vaga recente",
            atualizado_em=DATA_RECENTE,
        )
    )

    encontradas = repositorio.listar_por_empresa(
        EMPRESA_ID,
        limite=2,
    )

    assert [vaga.titulo_normalizado for vaga in encontradas] == [
        "Vaga recente",
        "Vaga intermediária",
    ]


def test_limite_invalido_e_rejeitado() -> None:
    """O limite deve permanecer dentro da faixa segura."""

    repositorio = RepositorioVagasMongoDB(BancoFalso())

    with pytest.raises(ValueError, match="entre 1 e 1000"):
        repositorio.listar_por_empresa(EMPRESA_ID, limite=0)

    with pytest.raises(ValueError, match="entre 1 e 1000"):
        repositorio.listar_por_empresa(EMPRESA_ID, limite=1001)


def test_salvar_lote_classifica_resultados() -> None:
    """O lote deve classificar todas as vagas recebidas."""

    colecao = ColecaoVagasFalsa()
    colecao.resultado_bulk = ResultadoBulkFalso(
        upserted_count=2,
        modified_count=1,
    )

    repositorio = RepositorioVagasMongoDB(BancoFalso(colecao))

    vagas = [
        criar_vaga(vaga_id=UUID(f"85000000-0000-4000-8000-{numero:012d}")) for numero in range(5)
    ]

    resultado = repositorio.salvar_lote(vagas)

    assert resultado.recebidos == 5
    assert resultado.inseridos == 2
    assert resultado.atualizados == 1
    assert resultado.inalterados == 2
    assert colecao.quantidade_chamadas_bulk == 1
    assert colecao.quantidade_operacoes_bulk == 5
    assert colecao.ordered_recebido is False


def test_lote_vazio_nao_acessa_mongodb() -> None:
    """Um lote vazio não deve executar bulk_write."""

    colecao = ColecaoVagasFalsa()
    repositorio = RepositorioVagasMongoDB(BancoFalso(colecao))

    resultado = repositorio.salvar_lote([])

    assert resultado.recebidos == 0
    assert resultado.processados == 0
    assert colecao.quantidade_chamadas_bulk == 0


def test_conflito_vira_erro_seguro() -> None:
    """O erro técnico deve virar um erro compreensível."""

    class ColecaoComConflito(ColecaoVagasFalsa):
        """Simula um conflito de índice no MongoDB."""

        def find_one_and_update(
            self,
            filtro: dict[str, object],
            atualizacao: dict[str, dict[str, Any]],
            *,
            upsert: bool,
            return_document: bool,
        ) -> dict[str, Any]:
            """Interrompe a gravação com um conflito."""

            raise DuplicateKeyError("identificador duplicado")

    repositorio = RepositorioVagasMongoDB(BancoFalso(ColecaoComConflito()))

    with pytest.raises(
        ConflitoVagaMongoDB,
        match="identificador conflitante",
    ):
        repositorio.salvar(criar_vaga())
