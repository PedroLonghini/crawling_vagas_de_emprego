"""Testes da preparação das coleções e índices do MongoDB."""

from __future__ import annotations

# Sequence permite receber uma lista ou uma tupla de índices.
from collections.abc import Sequence

# IndexModel é o tipo utilizado pelas definições dos índices.
from pymongo import IndexModel

# Importamos as configurações que serão verificadas.
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_ANUNCIOS,
    COLECAO_EMPRESAS,
    COLECAO_EXECUCOES_COLETA,
    COLECAO_PUBLICACOES_EMPREGOS,
    COLECAO_VAGAS_CANONICAS,
    INDICES_POR_COLECAO,
    preparar_banco,
)


class ColecaoFalsa:
    """Simula uma coleção sem acessar o MongoDB real."""

    def __init__(self, nome: str) -> None:
        """Guarda o nome e prepara a lista de índices recebidos."""

        self.nome = nome
        self.indices_recebidos: list[IndexModel] = []

    def create_indexes(
        self,
        indices: Sequence[IndexModel],
    ) -> list[str]:
        """Simula a criação dos índices."""

        # Copiamos os índices para verificar depois.
        self.indices_recebidos = list(indices)

        # O MongoDB real devolve uma lista com os nomes
        # dos índices que foram criados ou confirmados.
        return [str(indice.document["name"]) for indice in self.indices_recebidos]


class BancoFalso:
    """Simula a seleção de coleções com banco[nome]."""

    def __init__(self) -> None:
        """Começa sem nenhuma coleção selecionada."""

        self.colecoes: dict[str, ColecaoFalsa] = {}

    def __getitem__(self, nome: str) -> ColecaoFalsa:
        """Cria ou reutiliza uma coleção falsa."""

        # Se a coleção ainda não existe, criamos uma.
        if nome not in self.colecoes:
            self.colecoes[nome] = ColecaoFalsa(nome)

        return self.colecoes[nome]


def test_esquema_possui_as_colecoes_principais() -> None:
    """Todas as coleções necessárias devem estar configuradas."""

    assert set(INDICES_POR_COLECAO) == {
        COLECAO_EMPRESAS,
        COLECAO_ANUNCIOS,
        COLECAO_VAGAS_CANONICAS,
        COLECAO_EXECUCOES_COLETA,
        COLECAO_PUBLICACOES_EMPREGOS,
    }


def test_indices_que_impedem_duplicacao_sao_unicos() -> None:
    """CNPJ, domínio e identidade do anúncio não podem repetir."""

    # Transformamos a lista de índices de empresas em um dicionário
    # organizado pelo nome de cada índice.
    indices_empresas = {
        indice.document["name"]: indice.document for indice in INDICES_POR_COLECAO[COLECAO_EMPRESAS]
    }

    # Os dois identificadores de empresa precisam ser únicos.
    assert indices_empresas["uq_empresas_cnpj"]["unique"] is True
    assert indices_empresas["uq_empresas_dominio"]["unique"] is True

    # Fazemos a mesma organização para os anúncios.
    indices_anuncios = {
        indice.document["name"]: indice.document for indice in INDICES_POR_COLECAO[COLECAO_ANUNCIOS]
    }

    # A combinação fonte + id externo impede
    # que o crawler grave o mesmo anúncio várias vezes.
    assert indices_anuncios["uq_anuncios_fonte_id_externo"]["unique"] is True

    indices_publicacoes = {
        indice.document["name"]: indice.document
        for indice in INDICES_POR_COLECAO[COLECAO_PUBLICACOES_EMPREGOS]
    }
    assert indices_publicacoes["uq_publicacoes_empregos_idempotencia"]["unique"] is True


def test_preparar_banco_envia_indices_para_cada_colecao() -> None:
    """A preparação deve visitar todas as coleções configuradas."""

    banco = BancoFalso()

    # A função acredita que recebeu um banco verdadeiro,
    # mas todas as operações acontecem apenas na memória.
    resultado = preparar_banco(banco)

    # Todas as coleções precisam ter sido selecionadas.
    assert set(banco.colecoes) == set(INDICES_POR_COLECAO)

    # O resultado também precisa mencionar todas as coleções.
    assert set(resultado) == set(INDICES_POR_COLECAO)

    # Comparamos quantos índices cada coleção recebeu.
    for nome_colecao, indices_esperados in INDICES_POR_COLECAO.items():
        colecao = banco.colecoes[nome_colecao]

        assert len(colecao.indices_recebidos) == len(indices_esperados)
        assert len(resultado[nome_colecao]) == len(indices_esperados)
