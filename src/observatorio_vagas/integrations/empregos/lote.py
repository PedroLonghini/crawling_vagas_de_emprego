"""Publicação controlada dos itens previamente classificados pela fila."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from observatorio_vagas.integrations.empregos.fila import (
    ItemFilaEmpregos,
    ResultadoFilaEmpregos,
    SituacaoItemFilaEmpregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
)
from observatorio_vagas.integrations.empregos.publicador import (
    ResultadoPublicacaoEmpregosPersistida,
    VagaJaPublicadaPorOutraFonte,
)
from observatorio_vagas.storage.mongodb.publicacoes import TravaPublicacaoOcupada


class PublicadorVagaEmpregos(Protocol):
    """Operação individual exigida pelo coordenador do lote."""

    def publicar(
        self,
        preparacao: ResultadoPreparacaoEmpregos,
        *,
        vaga_id: UUID,
        anuncio_id: UUID,
        confirmar_publicacao: bool = False,
    ) -> ResultadoPublicacaoEmpregosPersistida:
        """Simula ou publica uma única vaga."""

        ...


class SituacaoResultadoLoteEmpregos(StrEnum):
    """Resultado operacional de cada item do lote."""

    IGNORADA_BLOQUEIO = "ignorada_bloqueio"
    IGNORADA_HISTORICO = "ignorada_historico"
    IGNORADA_DUPLICADA = "ignorada_duplicada"
    ADIADA_LIMITE = "adiada_limite"
    SIMULADA = "simulada"
    PUBLICADA = "publicada"
    REUTILIZADA = "reutilizada"
    FALHA = "falha"


@dataclass(frozen=True, slots=True)
class ItemResultadoLoteEmpregos:
    """Resultado auditável de uma vaga sem incluir payload ou segredo."""

    anuncio_id: UUID
    vaga_id: UUID | None
    titulo: str
    situacao: SituacaoResultadoLoteEmpregos
    operacao_id: UUID | None = None
    status_http: int | None = None
    request_id: str | None = None
    mensagem: str | None = None


@dataclass(frozen=True, slots=True)
class ResultadoPublicacaoLoteEmpregos:
    """Consolida o resultado sem perder falhas individuais."""

    itens: tuple[ItemResultadoLoteEmpregos, ...]
    confirmada: bool
    limite_envios: int

    def _por_situacao(
        self,
        *situacoes: SituacaoResultadoLoteEmpregos,
    ) -> tuple[ItemResultadoLoteEmpregos, ...]:
        return tuple(item for item in self.itens if item.situacao in situacoes)

    @property
    def simuladas(self) -> tuple[ItemResultadoLoteEmpregos, ...]:
        """Vagas que passaram por simulação sem escrita ou POST."""

        return self._por_situacao(SituacaoResultadoLoteEmpregos.SIMULADA)

    @property
    def publicadas(self) -> tuple[ItemResultadoLoteEmpregos, ...]:
        """Vagas confirmadas pela API neste lote."""

        return self._por_situacao(SituacaoResultadoLoteEmpregos.PUBLICADA)

    @property
    def reutilizadas(self) -> tuple[ItemResultadoLoteEmpregos, ...]:
        """Sucessos encontrados pelo publicador durante uma condição de corrida."""

        return self._por_situacao(SituacaoResultadoLoteEmpregos.REUTILIZADA)

    @property
    def falhas(self) -> tuple[ItemResultadoLoteEmpregos, ...]:
        """Falhas isoladas que não interromperam os itens seguintes."""

        return self._por_situacao(SituacaoResultadoLoteEmpregos.FALHA)

    @property
    def ignoradas(self) -> tuple[ItemResultadoLoteEmpregos, ...]:
        """Itens que não poderiam participar de uma nova publicação."""

        return self._por_situacao(
            SituacaoResultadoLoteEmpregos.IGNORADA_BLOQUEIO,
            SituacaoResultadoLoteEmpregos.IGNORADA_HISTORICO,
            SituacaoResultadoLoteEmpregos.IGNORADA_DUPLICADA,
            SituacaoResultadoLoteEmpregos.ADIADA_LIMITE,
        )


def _resultado_ignorado(
    item: ItemFilaEmpregos,
    *,
    situacao: SituacaoResultadoLoteEmpregos,
    mensagem: str,
) -> ItemResultadoLoteEmpregos:
    """Transforma uma decisão da fila em resultado operacional."""

    return ItemResultadoLoteEmpregos(
        anuncio_id=item.anuncio_id,
        vaga_id=item.vaga_id,
        titulo=item.titulo,
        situacao=situacao,
        mensagem=mensagem,
    )


def _mensagem_bloqueios(item: ItemFilaEmpregos) -> str:
    """Resume motivos já sanitizados pela camada de preparação."""

    if not item.bloqueios:
        return "O item foi bloqueado pela fila de preparação."

    return "; ".join(f"{motivo.codigo}: {motivo.mensagem}" for motivo in item.bloqueios)


def publicar_fila_empregos(
    fila: ResultadoFilaEmpregos,
    *,
    publicador: PublicadorVagaEmpregos,
    confirmar_publicacao: bool = False,
    limite_envios: int = 10,
) -> ResultadoPublicacaoLoteEmpregos:
    """Simula ou publica elegíveis, isolando toda falha por vaga.

    O limite vale tanto para a prévia quanto para a execução real. Assim, a
    simulação mostra exatamente o conjunto que o mesmo comando tentará enviar.
    """

    if limite_envios < 1 or limite_envios > 100:
        raise ValueError("limite_envios deve estar entre 1 e 100")

    resultados: list[ItemResultadoLoteEmpregos] = []
    elegiveis_processadas = 0
    trava_ocupada = False

    for item in fila.itens:
        if item.situacao is SituacaoItemFilaEmpregos.BLOQUEADA:
            resultados.append(
                _resultado_ignorado(
                    item,
                    situacao=SituacaoResultadoLoteEmpregos.IGNORADA_BLOQUEIO,
                    mensagem=_mensagem_bloqueios(item),
                )
            )
            continue

        if item.situacao is SituacaoItemFilaEmpregos.JA_REGISTRADA:
            resultados.append(
                _resultado_ignorado(
                    item,
                    situacao=SituacaoResultadoLoteEmpregos.IGNORADA_HISTORICO,
                    mensagem=(
                        "A operação já possui histórico e não será repetida automaticamente."
                    ),
                )
            )
            continue

        if item.situacao is SituacaoItemFilaEmpregos.DUPLICADA:
            # A mesma vaga já está no lote ou no ar: não é falha e não ocupa
            # uma das vagas do limite de envios.
            resultados.append(
                _resultado_ignorado(
                    item,
                    situacao=SituacaoResultadoLoteEmpregos.IGNORADA_DUPLICADA,
                    mensagem=_mensagem_bloqueios(item),
                )
            )
            continue

        if trava_ocupada:
            resultados.append(
                _resultado_ignorado(
                    item,
                    situacao=SituacaoResultadoLoteEmpregos.ADIADA_LIMITE,
                    mensagem="Outra publicação estava em andamento; fica para a próxima execução.",
                )
            )
            continue

        if elegiveis_processadas >= limite_envios:
            resultados.append(
                _resultado_ignorado(
                    item,
                    situacao=SituacaoResultadoLoteEmpregos.ADIADA_LIMITE,
                    mensagem="O limite seguro de envios deste lote foi atingido.",
                )
            )
            continue

        elegiveis_processadas += 1

        if item.vaga_id is None or item.preparacao is None:
            resultados.append(
                ItemResultadoLoteEmpregos(
                    anuncio_id=item.anuncio_id,
                    vaga_id=item.vaga_id,
                    titulo=item.titulo,
                    situacao=SituacaoResultadoLoteEmpregos.FALHA,
                    mensagem="O item elegível não possui preparação ou vaga canônica.",
                )
            )
            continue

        try:
            resultado = publicador.publicar(
                item.preparacao,
                vaga_id=item.vaga_id,
                anuncio_id=item.anuncio_id,
                confirmar_publicacao=confirmar_publicacao,
            )
        except VagaJaPublicadaPorOutraFonte as erro:
            # Outra execução publicou a mesma vaga depois que a fila foi montada:
            # é duplicada, não falha, e devolve a vaga no limite.
            elegiveis_processadas -= 1
            resultados.append(
                _resultado_ignorado(
                    item,
                    situacao=SituacaoResultadoLoteEmpregos.IGNORADA_DUPLICADA,
                    mensagem=str(erro),
                )
            )
            continue
        except TravaPublicacaoOcupada as erro:
            # Outra publicação segura a trava: esperar 60 s por item não ajuda.
            # Este item e os seguintes ficam para a próxima execução.
            resultados.append(
                ItemResultadoLoteEmpregos(
                    anuncio_id=item.anuncio_id,
                    vaga_id=item.vaga_id,
                    titulo=item.titulo,
                    situacao=SituacaoResultadoLoteEmpregos.FALHA,
                    mensagem=str(erro),
                )
            )
            trava_ocupada = True
            continue
        except Exception as erro:
            resultados.append(
                ItemResultadoLoteEmpregos(
                    anuncio_id=item.anuncio_id,
                    vaga_id=item.vaga_id,
                    titulo=item.titulo,
                    situacao=SituacaoResultadoLoteEmpregos.FALHA,
                    mensagem=str(erro) or type(erro).__name__,
                )
            )
            continue

        if resultado.simulada:
            situacao = SituacaoResultadoLoteEmpregos.SIMULADA
        elif resultado.reutilizada:
            situacao = SituacaoResultadoLoteEmpregos.REUTILIZADA
        else:
            situacao = SituacaoResultadoLoteEmpregos.PUBLICADA

        operacao = resultado.operacao
        resultados.append(
            ItemResultadoLoteEmpregos(
                anuncio_id=item.anuncio_id,
                vaga_id=item.vaga_id,
                titulo=item.titulo,
                situacao=situacao,
                operacao_id=operacao.id if operacao is not None else None,
                status_http=resultado.envio.status_http,
                request_id=resultado.envio.request_id,
            )
        )

    return ResultadoPublicacaoLoteEmpregos(
        itens=tuple(resultados),
        confirmada=confirmar_publicacao,
        limite_envios=limite_envios,
    )
