"""Contratos que definem como os dados serão armazenados."""

from __future__ import annotations

# Sequence representa uma coleção ordenada.
#
# Aceita lista e tupla sem obrigar o crawler a usar
# somente um tipo específico de coleção.
from collections.abc import Sequence

# dataclass cria uma classe simples usada para transportar resultados.
from dataclasses import dataclass

# Protocol define um contrato.
#
# Uma classe concreta não precisa herdar diretamente do Protocol.
# Ela precisa apenas possuir os mesmos métodos.
#
# runtime_checkable permite verificar o contrato durante os testes.
from typing import Protocol, runtime_checkable

# UUID é o tipo utilizado nos IDs internos dos nossos modelos.
from uuid import UUID

# Importamos os modelos que serão salvos.
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.coleta import ExecucaoColeta
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import Fonte, SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.domain.vaga import VagaCanonica


@dataclass(frozen=True, slots=True)
class ResultadoEscrita:
    """Resume o que aconteceu durante a gravação de um lote."""

    # Quantidade total de registros recebidos pelo repositório.
    recebidos: int

    # Registros que ainda não existiam e foram inseridos.
    inseridos: int

    # Registros que já existiam, mas possuíam informações diferentes.
    atualizados: int

    # Registros que já existiam com o mesmo conteúdo.
    #
    # Eles não precisam ser gravados novamente.
    inalterados: int

    def __post_init__(self) -> None:
        """Valida se os contadores formam um resultado possível."""

        # Guardamos os valores com seus nomes para produzir
        # uma mensagem de erro fácil de entender.
        contadores = {
            "recebidos": self.recebidos,
            "inseridos": self.inseridos,
            "atualizados": self.atualizados,
            "inalterados": self.inalterados,
        }

        # Nenhum contador pode ser negativo.
        for nome, valor in contadores.items():
            if valor < 0:
                raise ValueError(f"{nome} não pode ser negativo")

        # Tudo que foi recebido precisa ter um resultado.
        #
        # Exemplo:
        # recebidos = 10
        # inseridos = 4
        # atualizados = 3
        # inalterados = 3
        #
        # 4 + 3 + 3 = 10
        if self.processados != self.recebidos:
            raise ValueError(
                "inseridos, atualizados e inalterados devem totalizar a quantidade recebida"
            )

    @property
    def processados(self) -> int:
        """Retorna quantos registros receberam uma classificação."""

        return self.inseridos + self.atualizados + self.inalterados


@runtime_checkable
class RepositorioEmpresas(Protocol):
    """Operações necessárias para armazenar e consultar empresas."""

    def salvar(self, empresa: Empresa) -> Empresa:
        """Insere uma empresa nova ou atualiza uma empresa existente."""

        ...

    def salvar_lote(
        self,
        empresas: Sequence[Empresa],
    ) -> ResultadoEscrita:
        """Salva várias empresas em uma única operação."""

        ...

    def buscar_por_id(self, empresa_id: UUID) -> Empresa | None:
        """Procura uma empresa usando seu ID interno."""

        ...

    def buscar_por_cnpj(self, cnpj: str) -> Empresa | None:
        """Procura uma empresa usando seu CNPJ normalizado."""

        ...

    def buscar_por_dominio(self, dominio: str) -> Empresa | None:
        """Procura uma empresa usando seu domínio de internet."""

        ...


@runtime_checkable
class RepositorioAnuncios(Protocol):
    """Operações necessárias para os anúncios encontrados nas fontes."""

    def salvar(self, anuncio: AnuncioVaga) -> AnuncioVaga:
        """Insere ou atualiza um anúncio de maneira idempotente."""

        ...

    def salvar_lote(
        self,
        anuncios: Sequence[AnuncioVaga],
    ) -> ResultadoEscrita:
        """Salva vários anúncios reduzindo acessos ao banco."""

        ...

    def buscar_por_id(
        self,
        anuncio_id: UUID,
    ) -> AnuncioVaga | None:
        """Procura o anúncio pelo ID interno."""

        ...

    def buscar_por_fonte(
        self,
        fonte: Fonte,
        id_externo: str,
    ) -> AnuncioVaga | None:
        """Procura o anúncio usando sua identidade na fonte original."""

        ...

    def listar_recentes(
        self,
        fonte: Fonte | None = None,
        limite: int = 100,
    ) -> list[AnuncioVaga]:
        """Lista anúncios recentes, com filtro opcional de fonte."""

        ...

    def listar_por_alvo(
        self,
        alvo_id: str,
        limite: int = 100,
    ) -> list[AnuncioVaga]:
        """Lista anúncios originados de um alvo específico."""

        ...

    def listar_por_empresa(
        self,
        empresa_id: UUID,
        limite: int = 100,
    ) -> list[AnuncioVaga]:
        """Lista anúncios associados a uma empresa."""

        ...


@runtime_checkable
class RepositorioVagas(Protocol):
    """Operações necessárias para as vagas normalizadas."""

    def salvar(self, vaga: VagaCanonica) -> VagaCanonica:
        """Insere ou atualiza uma vaga canônica."""

        ...

    def salvar_lote(
        self,
        vagas: Sequence[VagaCanonica],
    ) -> ResultadoEscrita:
        """Salva várias vagas canônicas."""

        ...

    def buscar_por_id(self, vaga_id: UUID) -> VagaCanonica | None:
        """Procura uma vaga canônica pelo ID."""

        ...

    def listar_por_empresa(
        self,
        empresa_id: UUID,
        limite: int = 100,
    ) -> list[VagaCanonica]:
        """Lista as vagas normalizadas de uma empresa."""

        ...


@runtime_checkable
class RepositorioColetas(Protocol):
    """Operações necessárias para acompanhar execuções do crawler."""

    def salvar(self, execucao: ExecucaoColeta) -> ExecucaoColeta:
        """Insere ou atualiza as informações de uma execução."""

        ...

    def buscar_por_id(
        self,
        execucao_id: UUID,
    ) -> ExecucaoColeta | None:
        """Procura uma execução de coleta pelo ID."""

        ...

    def listar_recentes(
        self,
        fonte: Fonte | None = None,
        limite: int = 50,
    ) -> list[ExecucaoColeta]:
        """Lista as execuções mais recentes, com filtro opcional de fonte."""

        ...


@dataclass(frozen=True, slots=True)
class ResultadoReservaPublicacao:
    """Informa se a reserva criou ou reutilizou uma operação."""

    operacao: OperacaoPublicacaoEmpregos
    criada: bool


@runtime_checkable
class RepositorioPublicacoesEmpregos(Protocol):
    """Operações atômicas do histórico de publicação no Empregos."""

    def reservar(
        self,
        operacao: OperacaoPublicacaoEmpregos,
    ) -> ResultadoReservaPublicacao:
        """Reserva a operação ou devolve o registro idempotente existente."""

        ...

    def salvar_transicao(
        self,
        operacao: OperacaoPublicacaoEmpregos,
        *,
        situacao_anterior: SituacaoPublicacaoEmpregos,
    ) -> OperacaoPublicacaoEmpregos:
        """Persiste uma mudança somente se o estado anterior ainda coincidir."""

        ...

    def buscar_por_id(
        self,
        operacao_id: UUID,
    ) -> OperacaoPublicacaoEmpregos | None:
        """Procura uma operação pelo UUID interno."""

        ...

    def buscar_por_chave(
        self,
        chave_idempotencia: str,
    ) -> OperacaoPublicacaoEmpregos | None:
        """Procura uma operação por sua identidade lógica."""

        ...

    def listar_por_vaga(
        self,
        vaga_id: UUID,
        limite: int = 100,
    ) -> list[OperacaoPublicacaoEmpregos]:
        """Lista o histórico recente de uma vaga."""

        ...
