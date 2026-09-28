"""Testes do repositório MongoDB de empresas."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from pymongo.errors import DuplicateKeyError

from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.storage.contracts import RepositorioEmpresas
from observatorio_vagas.storage.mongodb.empresas import (
    ConflitoEmpresaMongoDB,
    RepositorioEmpresasMongoDB,
)
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_EMPRESAS,
)


@dataclass
class ResultadoBulkFalso:
    """Simula os contadores devolvidos pelo MongoDB."""

    upserted_count: int
    modified_count: int


class ColecaoEmpresasFalsa:
    """Simula somente as operações MongoDB usadas pelo repositório."""

    def __init__(self) -> None:
        """Começa sem documentos e sem chamadas em lote."""

        self.documentos: dict[UUID, dict[str, Any]] = {}
        self.quantidade_chamadas_bulk = 0
        self.quantidade_operacoes_bulk = 0
        self.ordered_recebido: bool | None = None

        # O teste poderá trocar estes valores.
        self.resultado_bulk = ResultadoBulkFalso(
            upserted_count=0,
            modified_count=0,
        )

    def replace_one(
        self,
        filtro: dict[str, object],
        documento: dict[str, Any],
        *,
        upsert: bool,
    ) -> None:
        """Simula a inserção ou substituição de uma empresa."""

        assert upsert is True

        empresa_id = filtro["_id"]

        assert isinstance(empresa_id, UUID)

        # Guardamos uma cópia para evitar alterações externas.
        self.documentos[empresa_id] = dict(documento)

    def find_one(
        self,
        filtro: dict[str, object],
    ) -> dict[str, Any] | None:
        """Procura um documento usando igualdade dos campos."""

        for documento in self.documentos.values():
            corresponde = all(documento.get(campo) == valor for campo, valor in filtro.items())

            if corresponde:
                return dict(documento)

        return None

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
    """Simula a seleção da coleção empresas."""

    def __init__(
        self,
        colecao: ColecaoEmpresasFalsa | None = None,
    ) -> None:
        """Permite receber uma coleção preparada pelo teste."""

        self.colecao = colecao or ColecaoEmpresasFalsa()
        self.nome_selecionado: str | None = None

    def __getitem__(
        self,
        nome: str,
    ) -> ColecaoEmpresasFalsa:
        """Registra qual coleção o repositório solicitou."""

        self.nome_selecionado = nome
        return self.colecao


def test_repositorio_atende_ao_contrato() -> None:
    """A implementação precisa possuir todos os métodos definidos."""

    banco = BancoFalso()
    repositorio = RepositorioEmpresasMongoDB(banco)

    assert isinstance(
        repositorio,
        RepositorioEmpresas,
    )

    # Confirma que o nome centralizado foi utilizado.
    assert banco.nome_selecionado == COLECAO_EMPRESAS


def test_salvar_e_buscar_empresa() -> None:
    """Uma empresa deve ser recuperada pelos seus identificadores."""

    banco = BancoFalso()
    repositorio = RepositorioEmpresasMongoDB(banco)

    empresa = Empresa(
        razao_social="Tecnologia Atlas S.A.",
        nome_fantasia="Atlas",
        cnpj="12.345.678/0001-90",
        dominio="https://www.atlas.example.com/",
        cidade="São Paulo",
        estado="SP",
    )

    # salvar devolve o mesmo modelo entregue.
    assert repositorio.salvar(empresa) is empresa

    # O documento foi armazenado usando o UUID como _id.
    assert empresa.id in banco.colecao.documentos

    # Busca pelo identificador interno.
    assert repositorio.buscar_por_id(empresa.id) == empresa

    # A busca aceita CNPJ com pontuação.
    assert repositorio.buscar_por_cnpj("12.345.678/0001-90") == empresa

    # A busca aceita URL completa e remove www.
    assert repositorio.buscar_por_dominio("https://www.atlas.example.com/carreiras") == empresa


def test_salvar_lote_classifica_resultados() -> None:
    """O lote deve separar inseridos, atualizados e inalterados."""

    colecao = ColecaoEmpresasFalsa()

    # Simulamos a resposta de cinco operações:
    # - duas inserções;
    # - uma atualização;
    # - duas sem alteração.
    colecao.resultado_bulk = ResultadoBulkFalso(
        upserted_count=2,
        modified_count=1,
    )

    repositorio = RepositorioEmpresasMongoDB(
        BancoFalso(colecao),
    )

    empresas = [Empresa(razao_social=f"Empresa {numero}") for numero in range(5)]

    resultado = repositorio.salvar_lote(empresas)

    assert resultado.recebidos == 5
    assert resultado.inseridos == 2
    assert resultado.atualizados == 1
    assert resultado.inalterados == 2

    # O lote deve utilizar uma única chamada.
    assert colecao.quantidade_chamadas_bulk == 1
    assert colecao.quantidade_operacoes_bulk == 5

    # ordered=False permite continuar após um erro individual.
    assert colecao.ordered_recebido is False


def test_lote_vazio_nao_acessa_mongodb() -> None:
    """Não faz sentido enviar uma operação vazia ao banco."""

    colecao = ColecaoEmpresasFalsa()
    repositorio = RepositorioEmpresasMongoDB(
        BancoFalso(colecao),
    )

    resultado = repositorio.salvar_lote([])

    assert resultado.recebidos == 0
    assert resultado.processados == 0
    assert colecao.quantidade_chamadas_bulk == 0


def test_cnpj_duplicado_vira_erro_de_negocio() -> None:
    """Um erro técnico deve virar uma mensagem compreensível."""

    class ColecaoComConflito(ColecaoEmpresasFalsa):
        """Simula um índice único recusando a empresa."""

        def replace_one(
            self,
            filtro: dict[str, object],
            documento: dict[str, Any],
            *,
            upsert: bool,
        ) -> None:
            raise DuplicateKeyError("valor duplicado")

    repositorio = RepositorioEmpresasMongoDB(
        BancoFalso(ColecaoComConflito()),
    )

    empresa = Empresa(
        razao_social="Empresa Duplicada",
        cnpj="12.345.678/0001-90",
    )

    with pytest.raises(
        ConflitoEmpresaMongoDB,
        match="mesmo CNPJ ou domínio",
    ):
        repositorio.salvar(empresa)
