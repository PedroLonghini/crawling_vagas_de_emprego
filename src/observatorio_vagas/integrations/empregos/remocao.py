"""O que a varredura semanal tira do Empregos, e o envio dessa remoção.

Duas origens:

1. vaga publicada cujo anúncio foi encerrado (saiu do site de origem, venceu);
2. duplicata que escapou da proteção: duas publicações no ar que são a mesma
   vaga pela regra de ``domain/assinatura_vaga.py``. Fica a mais antiga.

Decisão do usuário (08/10/2026): a remoção é automática. Ela só acontece de
verdade quando a API de remoção estiver definida e configurada; até lá o
``RemovedorNaoConfigurado`` devolve tudo como simulado e a fila sai no resumo.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from observatorio_vagas.domain.assinatura_vaga import mesma_vaga
from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos

# Só sai do Empregos o que de fato pode estar lá: preparada nunca foi enviada,
# rejeitada nunca entrou. Indeterminada pode ter entrado: na dúvida, remove.
SITUACOES_REMOVIVEIS = frozenset(
    {SituacaoPublicacaoEmpregos.SUCESSO, SituacaoPublicacaoEmpregos.INDETERMINADA}
)


class MotivoRemocao(StrEnum):
    VAGA_ENCERRADA_NA_ORIGEM = "vaga_encerrada_na_origem"
    DUPLICATA = "duplicata"


@dataclass(frozen=True, slots=True)
class ItemRemocao:
    """Uma publicação que deve sair do Empregos e por quê."""

    operacao: OperacaoPublicacaoEmpregos
    motivo: MotivoRemocao
    detalhe: str = ""


class SituacaoRemocao(StrEnum):
    REMOVIDA = "removida"
    SIMULADA = "simulada"
    FALHA = "falha"


@dataclass(frozen=True, slots=True)
class ResultadoRemocao:
    item: ItemRemocao
    situacao: SituacaoRemocao
    mensagem: str = ""


class RemovedorEmpregos(Protocol):
    """Tira uma publicação do Empregos (contrato a definir com a API)."""

    def remover(self, item: ItemRemocao) -> ResultadoRemocao: ...


class RemovedorNaoConfigurado:
    """Enquanto a API de remoção não existe, nada é enviado: tudo fica simulado."""

    def remover(self, item: ItemRemocao) -> ResultadoRemocao:
        return ResultadoRemocao(
            item=item,
            situacao=SituacaoRemocao.SIMULADA,
            mensagem=(
                "a remoção pela API do Empregos ainda não foi definida; "
                "a vaga continua no ar até a remoção ser configurada"
            ),
        )


def encontrar_duplicatas_publicadas(
    publicadas: Iterable[OperacaoPublicacaoEmpregos],
) -> list[ItemRemocao]:
    """Publicações no ar que repetem uma mais antiga (a mais antiga fica)."""

    por_assinatura: dict[str, list[OperacaoPublicacaoEmpregos]] = {}
    for operacao in sorted(publicadas, key=lambda operacao: operacao.criado_em):
        if operacao.assinatura_conteudo is None:
            continue
        por_assinatura.setdefault(operacao.assinatura_conteudo, []).append(operacao)

    duplicatas: list[ItemRemocao] = []
    for operacoes in por_assinatura.values():
        mantidas: list[OperacaoPublicacaoEmpregos] = []
        for operacao in operacoes:
            original = next(
                (
                    mantida
                    for mantida in mantidas
                    if mantida.external_job_posting_id != operacao.external_job_posting_id
                    and mesma_vaga(
                        dominio_a=mantida.dominio_origem,
                        assinatura_completa_a=mantida.assinatura_descricao_completa,
                        dominio_b=operacao.dominio_origem,
                        assinatura_completa_b=operacao.assinatura_descricao_completa,
                    )
                ),
                None,
            )
            if original is None:
                mantidas.append(operacao)
                continue
            duplicatas.append(
                ItemRemocao(
                    operacao=operacao,
                    motivo=MotivoRemocao.DUPLICATA,
                    detalhe=f"repete a publicação do anúncio {original.anuncio_id}",
                )
            )
    return duplicatas


def montar_fila_remocao(
    *,
    publicadas: Sequence[OperacaoPublicacaoEmpregos],
    anuncios_encerrados: Iterable[UUID],
) -> list[ItemRemocao]:
    """Encerradas na origem + duplicatas, só entre o que pode estar no Empregos."""

    removiveis = [operacao for operacao in publicadas if operacao.situacao in SITUACOES_REMOVIVEIS]
    encerrados = set(anuncios_encerrados)
    fila: list[ItemRemocao] = [
        ItemRemocao(operacao=operacao, motivo=MotivoRemocao.VAGA_ENCERRADA_NA_ORIGEM)
        for operacao in removiveis
        if operacao.anuncio_id in encerrados
    ]
    ja_na_fila = {item.operacao.id for item in fila}
    fila.extend(
        item
        for item in encontrar_duplicatas_publicadas(removiveis)
        if item.operacao.id not in ja_na_fila
    )
    return fila


def remover_publicacoes(
    fila: Sequence[ItemRemocao],
    *,
    removedor: RemovedorEmpregos,
) -> list[ResultadoRemocao]:
    """Envia cada remoção isoladamente: uma falha não interrompe as outras."""

    resultados: list[ResultadoRemocao] = []
    for item in fila:
        try:
            resultados.append(removedor.remover(item))
        except Exception as erro:  # noqa: BLE001 - uma remoção não derruba a varredura
            resultados.append(
                ResultadoRemocao(
                    item=item,
                    situacao=SituacaoRemocao.FALHA,
                    mensagem=str(erro) or type(erro).__name__,
                )
            )
    return resultados
