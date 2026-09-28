"""Testes do repositório MongoDB de anúncios."""

from __future__ import annotations

# Sequence permite que o banco falso receba uma sequência
# de operações em lote.
from collections.abc import Iterator, Sequence

# Dataclass cria uma resposta simples para simular
# o resultado devolvido pelo bulk_write do MongoDB.
from dataclasses import dataclass

# UTC e datetime criam datas previsíveis para os testes.
from datetime import UTC, datetime

# Any permite representar os diferentes tipos
# que podem existir dentro de um documento MongoDB.
from typing import Any

# UUID representa os identificadores usados nos testes.
from uuid import UUID

# Pytest fornece o recurso pytest.raises.
import pytest

# Estas constantes permitem simular o comportamento
# esperado do PyMongo.
from pymongo import DESCENDING, ReturnDocument

# Este erro será usado para simular um conflito
# causado por um índice único do MongoDB.
from pymongo.errors import DuplicateKeyError

# Modelo que o repositório precisa salvar e reconstruir.
from observatorio_vagas.domain.anuncio import AnuncioVaga

# Enumerações utilizadas pelos anúncios.
from observatorio_vagas.domain.enums import (
    Fonte,
    StatusAnuncio,
)

# Contrato que a implementação precisa atender.
from observatorio_vagas.storage.contracts import (
    RepositorioAnuncios,
)

# Classes concretas que estamos testando.
from observatorio_vagas.storage.mongodb.anuncios import (
    ConflitoAnuncioMongoDB,
    RepositorioAnunciosMongoDB,
)

# Nome oficial da coleção de anúncios.
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_ANUNCIOS,
)

# Datas fixas deixam o teste previsível.
#
# Se usássemos datetime.now(), o resultado mudaria
# a cada execução.
DATA_PRIMEIRA_OBSERVACAO = datetime(
    2026,
    8,
    20,
    10,
    0,
    tzinfo=UTC,
)

DATA_SEGUNDA_OBSERVACAO = datetime(
    2026,
    8,
    20,
    11,
    0,
    tzinfo=UTC,
)

DATA_TERCEIRA_OBSERVACAO = datetime(
    2026,
    8,
    20,
    12,
    0,
    tzinfo=UTC,
)


@dataclass
class ResultadoBulkFalso:
    """Simula os contadores devolvidos pelo bulk_write."""

    # Quantidade de documentos que ainda não existiam.
    upserted_count: int

    # Quantidade de documentos existentes que mudaram.
    modified_count: int


class CursorAnunciosFalso:
    """Simula o cursor devolvido pelo método find do MongoDB."""

    def __init__(
        self,
        documentos: list[dict[str, Any]],
    ) -> None:
        """Recebe os documentos encontrados pela consulta."""

        # Criamos cópias para os testes não alterarem
        # acidentalmente os documentos originais.
        self.documentos = [dict(documento) for documento in documentos]

    def sort(
        self,
        campo: str,
        direcao: int,
    ) -> CursorAnunciosFalso:
        """Organiza os documentos como o cursor do PyMongo."""

        # O repositório deve ordenar pela observação mais recente.
        assert campo == "ultima_observacao_em"
        assert direcao == DESCENDING

        self.documentos.sort(
            key=lambda documento: documento[campo],
            reverse=True,
        )

        # O cursor verdadeiro devolve ele mesmo,
        # permitindo encadear .sort().limit().
        return self

    def limit(
        self,
        quantidade: int,
    ) -> CursorAnunciosFalso:
        """Limita quantos documentos serão devolvidos."""

        self.documentos = self.documentos[:quantidade]
        return self

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """Permite percorrer o cursor usando um for."""

        return iter(self.documentos)


class ColecaoAnunciosFalsa:
    """Simula somente as operações utilizadas pelo repositório."""

    def __init__(self) -> None:
        """Começa sem documentos e sem operações em lote."""

        # Cada documento será guardado pelo seu UUID.
        self.documentos: dict[
            UUID,
            dict[str, Any],
        ] = {}

        # Estes campos permitem conferir o comportamento do lote.
        self.quantidade_chamadas_bulk = 0
        self.quantidade_operacoes_bulk = 0
        self.ordered_recebido: bool | None = None

        # O teste poderá modificar estes contadores
        # antes de chamar salvar_lote.
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
        """Simula a inserção ou atualização atômica."""

        # O repositório deve permitir a criação
        # quando o anúncio ainda não existir.
        assert upsert is True

        # O repositório deve solicitar o documento atualizado.
        assert return_document == ReturnDocument.AFTER

        # Procuramos um documento que corresponda
        # à combinação fonte + id_externo.
        documento_existente = self.find_one(filtro)

        if documento_existente is None:
            # Se não existe, aplicamos primeiro os campos
            # exclusivos da inserção.
            documento_salvo = dict(atualizacao["$setOnInsert"])

            # Depois adicionamos os demais campos do anúncio.
            documento_salvo.update(atualizacao["$set"])
            documento_salvo.update(atualizacao.get("$max", {}))

            anuncio_id = documento_salvo["_id"]

            assert isinstance(anuncio_id, UUID)

            self.documentos[anuncio_id] = documento_salvo

        else:
            # Se já existe, preservamos seu UUID.
            anuncio_id = documento_existente["_id"]

            assert isinstance(anuncio_id, UUID)

            # Aplicamos somente o $set.
            #
            # O $setOnInsert não pode ser aplicado em atualizações.
            self.documentos[anuncio_id].update(atualizacao["$set"])
            for campo, valor in atualizacao.get("$max", {}).items():
                if self.documentos[anuncio_id].get(campo) is None or valor > self.documentos[anuncio_id][campo]:
                    self.documentos[anuncio_id][campo] = valor

        return dict(self.documentos[anuncio_id])

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
    ) -> CursorAnunciosFalso:
        """Procura todos os documentos correspondentes."""

        encontrados = []

        for documento in self.documentos.values():
            corresponde = all(documento.get(campo) == valor for campo, valor in filtro.items())

            if corresponde:
                encontrados.append(dict(documento))

        return CursorAnunciosFalso(encontrados)

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
    """Simula a seleção da coleção de anúncios."""

    def __init__(
        self,
        colecao: ColecaoAnunciosFalsa | None = None,
    ) -> None:
        """Permite receber uma coleção já preparada."""

        self.colecao = colecao if colecao is not None else ColecaoAnunciosFalsa()

        self.nome_selecionado: str | None = None

    def __getitem__(
        self,
        nome: str,
    ) -> ColecaoAnunciosFalsa:
        """Registra qual coleção foi solicitada."""

        self.nome_selecionado = nome
        return self.colecao


def criar_anuncio(
    *,
    id_externo: str = "vaga-001",
    titulo: str = "Pessoa Desenvolvedora Python",
    fonte: Fonte = Fonte.GUPY,
    empresa_id: UUID | None = None,
    anuncio_id: UUID | None = None,
    primeira_observacao: datetime = DATA_PRIMEIRA_OBSERVACAO,
    ultima_observacao: datetime = DATA_PRIMEIRA_OBSERVACAO,
    hash_conteudo: str = "a" * 64,
) -> AnuncioVaga:
    """Cria um anúncio válido para os testes."""

    # O dicionário contém todos os campos obrigatórios.
    dados: dict[str, Any] = {
        "fonte": fonte,
        "id_externo": id_externo,
        "url": f"https://empresa.example.com/vagas/{id_externo}",
        "empresa_id": empresa_id,
        "titulo_original": titulo,
        "descricao_original": ("Desenvolvimento de aplicações em Python."),
        "status": StatusAnuncio.ATIVO,
        "hash_conteudo": hash_conteudo,
        "referencia_bruta": f"raw/gupy/{id_externo}.json",
        "primeira_observacao_em": primeira_observacao,
        "ultima_observacao_em": ultima_observacao,
    }

    # Quando nenhum UUID é informado, o próprio modelo
    # cria um UUID novo.
    if anuncio_id is not None:
        dados["id"] = anuncio_id

    return AnuncioVaga.model_validate(dados)


def test_repositorio_atende_ao_contrato() -> None:
    """A implementação deve possuir todos os métodos definidos."""

    banco = BancoFalso()

    repositorio = RepositorioAnunciosMongoDB(banco)

    assert isinstance(
        repositorio,
        RepositorioAnuncios,
    )

    # Confirma que o repositório selecionou
    # a coleção oficial de anúncios.
    assert banco.nome_selecionado == COLECAO_ANUNCIOS


def test_salvar_e_atualizar_preserva_identidade() -> None:
    """Encontrar a mesma vaga novamente não deve duplicá-la."""

    colecao = ColecaoAnunciosFalsa()
    repositorio = RepositorioAnunciosMongoDB(BancoFalso(colecao))

    id_original = UUID("10000000-0000-4000-8000-000000000001")

    id_da_nova_observacao = UUID("20000000-0000-4000-8000-000000000002")

    # Esta é a primeira vez que o crawler encontrou a vaga.
    anuncio_original = criar_anuncio(
        anuncio_id=id_original,
    )

    anuncio_salvo = repositorio.salvar(anuncio_original)

    assert anuncio_salvo.id == id_original
    assert len(colecao.documentos) == 1

    # Simulamos o crawler encontrando a mesma vaga novamente.
    #
    # O objeto novo possui:
    #
    # - outro UUID temporário;
    # - título alterado;
    # - hash alterado;
    # - horário mais recente.
    nova_observacao = criar_anuncio(
        anuncio_id=id_da_nova_observacao,
        titulo="Pessoa Desenvolvedora Python Sênior",
        primeira_observacao=DATA_SEGUNDA_OBSERVACAO,
        ultima_observacao=DATA_SEGUNDA_OBSERVACAO,
        hash_conteudo="b" * 64,
    )

    anuncio_atualizado = repositorio.salvar(nova_observacao)

    # O UUID armazenado na primeira observação é preservado.
    assert anuncio_atualizado.id == id_original

    # A data histórica também é preservada.
    assert anuncio_atualizado.primeira_observacao_em == DATA_PRIMEIRA_OBSERVACAO

    # Os campos novos são atualizados.
    assert anuncio_atualizado.titulo_original == "Pessoa Desenvolvedora Python Sênior"

    assert anuncio_atualizado.hash_conteudo == "b" * 64

    # Continua existindo somente um documento.
    assert len(colecao.documentos) == 1

    # O anúncio pode ser encontrado pelo UUID preservado.
    assert repositorio.buscar_por_id(id_original) == anuncio_atualizado

    # Também pode ser encontrado pela identidade externa.
    assert (
        repositorio.buscar_por_fonte(
            Fonte.GUPY,
            " vaga-001 ",
        )
        == anuncio_atualizado
    )


def test_listar_recentes_sem_fonte_retorna_todos() -> None:
    """Sem filtro, anúncios de todas as fontes devem aparecer."""

    repositorio = RepositorioAnunciosMongoDB(BancoFalso())

    repositorio.salvar(
        criar_anuncio(
            id_externo="gupy",
            fonte=Fonte.GUPY,
            ultima_observacao=DATA_PRIMEIRA_OBSERVACAO,
        )
    )

    repositorio.salvar(
        criar_anuncio(
            id_externo="outra",
            fonte=Fonte.OUTRA,
            ultima_observacao=DATA_SEGUNDA_OBSERVACAO,
            hash_conteudo="b" * 64,
        )
    )

    encontrados = repositorio.listar_recentes(
        limite=10,
    )

    assert len(encontrados) == 2
    assert encontrados[0].fonte == Fonte.OUTRA
    assert encontrados[1].fonte == Fonte.GUPY


def test_listar_recentes_rejeita_limite_inseguro() -> None:
    """A consulta não deve carregar uma quantidade ilimitada."""

    repositorio = RepositorioAnunciosMongoDB(BancoFalso())

    with pytest.raises(
        ValueError,
        match="entre 1 e 10000",
    ):
        repositorio.listar_recentes(
            limite=0,
        )

    with pytest.raises(
        ValueError,
        match="entre 1 e 10000",
    ):
        repositorio.listar_recentes(
            limite=10001,
        )


def test_salvar_lote_classifica_resultados() -> None:
    """O lote deve separar inserções, alterações e repetições."""

    colecao = ColecaoAnunciosFalsa()

    # Simulamos cinco anúncios:
    #
    # - dois novos;
    # - um atualizado;
    # - dois sem alterações.
    colecao.resultado_bulk = ResultadoBulkFalso(
        upserted_count=2,
        modified_count=1,
    )

    repositorio = RepositorioAnunciosMongoDB(BancoFalso(colecao))

    anuncios = [criar_anuncio(id_externo=f"vaga-{numero}") for numero in range(5)]

    resultado = repositorio.salvar_lote(anuncios)

    assert resultado.recebidos == 5
    assert resultado.inseridos == 2
    assert resultado.atualizados == 1
    assert resultado.inalterados == 2

    # Todos os anúncios foram enviados
    # em uma única chamada ao MongoDB.
    assert colecao.quantidade_chamadas_bulk == 1
    assert colecao.quantidade_operacoes_bulk == 5

    # ordered=False permite continuar o lote
    # depois de uma falha individual.
    assert colecao.ordered_recebido is False


def test_lote_vazio_nao_acessa_mongodb() -> None:
    """Um lote vazio não deve executar bulk_write."""

    colecao = ColecaoAnunciosFalsa()

    repositorio = RepositorioAnunciosMongoDB(BancoFalso(colecao))

    resultado = repositorio.salvar_lote([])

    assert resultado.recebidos == 0
    assert resultado.processados == 0
    assert colecao.quantidade_chamadas_bulk == 0


def test_identidade_duplicada_vira_erro_seguro() -> None:
    """O erro técnico deve virar uma mensagem compreensível."""

    class ColecaoComConflito(ColecaoAnunciosFalsa):
        """Simula um índice único recusando o anúncio."""

        def find_one_and_update(
            self,
            filtro: dict[str, object],
            atualizacao: dict[
                str,
                dict[str, Any],
            ],
            *,
            upsert: bool,
            return_document: bool,
        ) -> dict[str, Any]:
            """Simula o conflito do MongoDB."""

            raise DuplicateKeyError("identidade duplicada")

    repositorio = RepositorioAnunciosMongoDB(BancoFalso(ColecaoComConflito()))

    with pytest.raises(
        ConflitoAnuncioMongoDB,
        match="mesma fonte e ID externo",
    ):
        repositorio.salvar(criar_anuncio())


def test_listar_recentes_filtra_por_fonte() -> None:
    """A consulta deve filtrar e ordenar os anúncios."""

    repositorio = RepositorioAnunciosMongoDB(BancoFalso())

    repositorio.salvar(
        criar_anuncio(
            id_externo="gupy-antigo",
            fonte=Fonte.GUPY,
            ultima_observacao=DATA_PRIMEIRA_OBSERVACAO,
        )
    )

    repositorio.salvar(
        criar_anuncio(
            id_externo="outra-fonte",
            fonte=Fonte.OUTRA,
            ultima_observacao=DATA_TERCEIRA_OBSERVACAO,
            hash_conteudo="b" * 64,
        )
    )

    repositorio.salvar(
        criar_anuncio(
            id_externo="gupy-recente",
            fonte=Fonte.GUPY,
            ultima_observacao=DATA_SEGUNDA_OBSERVACAO,
            hash_conteudo="c" * 64,
        )
    )

    encontrados = repositorio.listar_recentes(
        fonte=Fonte.GUPY,
        limite=10,
    )

    assert [anuncio.id_externo for anuncio in encontrados] == [
        "gupy-recente",
        "gupy-antigo",
    ]
