"""Prepara uma fila auditável de vagas candidatas à publicação.

Este módulo somente consulta modelos e repositórios. Ele não grava histórico,
não altera vagas e não realiza chamadas HTTP.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from observatorio_vagas.crawling.catalog import AlvoColeta
from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.assinatura_vaga import (
    IdentidadeConteudo,
    identidade_de_payload,
    mesma_vaga,
)
from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.prontidao import CAMPOS_API_EMPREGOS
from observatorio_vagas.domain.publicacao import (
    calcular_chave_idempotencia_empregos,
    encontrar_publicacao_da_mesma_vaga,
)
from observatorio_vagas.extraction.normalizacao_vaga import (
    converter_anuncio_em_vaga_canonica,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
    preparar_publicacao_empregos,
)
from observatorio_vagas.storage.contracts import (
    RepositorioEmpresas,
    RepositorioPublicacoesEmpregos,
    RepositorioVagas,
)


class SituacaoItemFilaEmpregos(StrEnum):
    """Classifica o próximo passo seguro de uma vaga."""

    ELEGIVEL = "elegivel"
    BLOQUEADA = "bloqueada"
    JA_REGISTRADA = "ja_registrada"
    DUPLICADA = "duplicada"


@dataclass(frozen=True, slots=True)
class MotivoFilaEmpregos:
    """Explica um bloqueio ou alerta sem depender de texto livre."""

    codigo: str
    mensagem: str
    campo: str | None = None


@dataclass(frozen=True, slots=True)
class ItemFilaEmpregos:
    """Resumo de uma vaga avaliada para o lote de publicação."""

    anuncio_id: UUID
    titulo: str
    alvo_id: str | None
    empresa_id: UUID | None
    vaga_id: UUID | None
    situacao: SituacaoItemFilaEmpregos
    campos_preenchidos: int = 0
    total_campos: int = len(CAMPOS_API_EMPREGOS)
    bloqueios: tuple[MotivoFilaEmpregos, ...] = ()
    alertas: tuple[MotivoFilaEmpregos, ...] = ()
    chave_idempotencia: str | None = None
    situacao_publicacao_existente: SituacaoPublicacaoEmpregos | None = None
    preparacao: ResultadoPreparacaoEmpregos | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def percentual_preenchimento(self) -> float:
        """Calcula a cobertura sem dividir por zero."""

        if self.total_campos == 0:
            return 0.0

        return self.campos_preenchidos / self.total_campos * 100


@dataclass(frozen=True, slots=True)
class ResultadoFilaEmpregos:
    """Resultado consolidado de uma preparação em lote."""

    itens: tuple[ItemFilaEmpregos, ...]

    @property
    def elegiveis(self) -> tuple[ItemFilaEmpregos, ...]:
        """Devolve somente vagas liberadas para revisão final."""

        return tuple(
            item for item in self.itens if item.situacao is SituacaoItemFilaEmpregos.ELEGIVEL
        )

    @property
    def bloqueadas(self) -> tuple[ItemFilaEmpregos, ...]:
        """Devolve vagas com dados ou políticas impeditivas."""

        return tuple(
            item for item in self.itens if item.situacao is SituacaoItemFilaEmpregos.BLOQUEADA
        )

    @property
    def ja_registradas(self) -> tuple[ItemFilaEmpregos, ...]:
        """Devolve operações que exigem consulta ao histórico."""

        return tuple(
            item for item in self.itens if item.situacao is SituacaoItemFilaEmpregos.JA_REGISTRADA
        )

    @property
    def duplicadas(self) -> tuple[ItemFilaEmpregos, ...]:
        """Devolve anúncios repetidos que foram removidos da saída de payloads."""

        return tuple(
            item for item in self.itens if item.situacao is SituacaoItemFilaEmpregos.DUPLICADA
        )


def _item_bloqueado(
    anuncio: AnuncioVaga,
    *,
    codigo: str,
    mensagem: str,
    vaga_id: UUID | None = None,
) -> ItemFilaEmpregos:
    """Cria um resultado uniforme para uma pré-condição ausente."""

    return ItemFilaEmpregos(
        anuncio_id=anuncio.id,
        titulo=anuncio.titulo_original,
        alvo_id=anuncio.alvo_id,
        empresa_id=anuncio.empresa_id,
        vaga_id=vaga_id,
        situacao=SituacaoItemFilaEmpregos.BLOQUEADA,
        bloqueios=(
            MotivoFilaEmpregos(
                codigo=codigo,
                mensagem=mensagem,
            ),
        ),
    )


def _preparar_item(
    anuncio: AnuncioVaga,
    *,
    alvos: Mapping[str, AlvoColeta],
    repositorio_empresas: RepositorioEmpresas,
    repositorio_vagas: RepositorioVagas,
    repositorio_publicacoes: RepositorioPublicacoesEmpregos,
    momento_referencia: datetime | None,
) -> ItemFilaEmpregos:
    """Cruza todas as entidades necessárias para uma publicação."""

    if anuncio.alvo_id is None:
        return _item_bloqueado(
            anuncio,
            codigo="alvo_ausente",
            mensagem="O anúncio não possui alvo_id.",
        )

    alvo = alvos.get(anuncio.alvo_id)

    if alvo is None:
        return _item_bloqueado(
            anuncio,
            codigo="alvo_nao_encontrado",
            mensagem="O alvo do anúncio não existe no catálogo atual.",
        )

    if alvo.fonte is not anuncio.fonte:
        return _item_bloqueado(
            anuncio,
            codigo="fonte_divergente",
            mensagem="A fonte do anúncio diverge da fonte cadastrada no alvo.",
        )

    if anuncio.empresa_id is None:
        return _item_bloqueado(
            anuncio,
            codigo="empresa_nao_associada",
            mensagem="O anúncio ainda não foi associado a uma empresa.",
        )

    empresa = repositorio_empresas.buscar_por_id(anuncio.empresa_id)

    if empresa is None:
        return _item_bloqueado(
            anuncio,
            codigo="empresa_nao_encontrada",
            mensagem="A empresa associada ao anúncio não foi encontrada.",
        )

    try:
        vaga_esperada = converter_anuncio_em_vaga_canonica(anuncio)
    except ValueError as erro:
        return _item_bloqueado(
            anuncio,
            codigo="vaga_nao_normalizavel",
            mensagem=str(erro),
        )

    vaga = repositorio_vagas.buscar_por_id(vaga_esperada.id)

    if vaga is None:
        return _item_bloqueado(
            anuncio,
            codigo="vaga_nao_encontrada",
            mensagem="A vaga canônica esperada ainda não foi criada.",
            vaga_id=vaga_esperada.id,
        )

    try:
        preparacao = preparar_publicacao_empregos(
            empresa=empresa,
            recrutador=None,
            anuncio=anuncio,
            vaga=vaga,
            politica_fonte=alvo.politica,
            momento_referencia=momento_referencia,
        )
    except (TypeError, ValueError) as erro:
        return _item_bloqueado(
            anuncio,
            codigo="preparacao_invalida",
            mensagem=str(erro),
            vaga_id=vaga.id,
        )
    relatorio = preparacao.relatorio
    alertas = tuple(
        MotivoFilaEmpregos(
            codigo="campo_opcional",
            campo=alerta.campo,
            mensagem=alerta.mensagem,
        )
        for alerta in relatorio.alertas
    )

    if not preparacao.pronto_para_envio:
        bloqueios = tuple(
            MotivoFilaEmpregos(
                codigo=bloqueio.codigo.value,
                campo=bloqueio.campo,
                mensagem=bloqueio.mensagem,
            )
            for bloqueio in preparacao.motivos_bloqueio
        )
        return ItemFilaEmpregos(
            anuncio_id=anuncio.id,
            titulo=vaga.titulo_normalizado,
            alvo_id=anuncio.alvo_id,
            empresa_id=empresa.id,
            vaga_id=vaga.id,
            situacao=SituacaoItemFilaEmpregos.BLOQUEADA,
            campos_preenchidos=len(relatorio.campos_preenchidos),
            total_campos=relatorio.total_campos,
            bloqueios=bloqueios,
            alertas=alertas,
        )

    assert preparacao.payload is not None
    external_id = preparacao.payload["externalJobPostingId"]
    operation_type = preparacao.payload["jobPostingOperationType"]

    if not isinstance(external_id, str) or not isinstance(operation_type, str):
        return _item_bloqueado(
            anuncio,
            codigo="identidade_publicacao_invalida",
            mensagem="O payload não produziu uma identidade de publicação válida.",
            vaga_id=vaga.id,
        )

    chave = calcular_chave_idempotencia_empregos(
        external_job_posting_id=external_id,
        operation_type=operation_type,
    )
    existente = repositorio_publicacoes.buscar_por_chave(chave)

    # A mesma vaga já pode estar no ar vinda de outro site (outro id, outra
    # chave): a assinatura de conteúdo a reconhece entre lotes de dias diferentes.
    identidade = identidade_de_payload(
        preparacao.payload,
        dominio=(
            preparacao.proveniencia_fonte.dominio
            if preparacao.proveniencia_fonte is not None
            else None
        ),
    )
    publicada = (
        encontrar_publicacao_da_mesma_vaga(
            repositorio_publicacoes.listar_ativas_por_assinatura(identidade.assinatura),
            identidade=identidade,
            external_job_posting_id=external_id,
        )
        if existente is None and identidade is not None
        else None
    )
    if publicada is not None:
        return ItemFilaEmpregos(
            anuncio_id=anuncio.id,
            titulo=vaga.titulo_normalizado,
            alvo_id=anuncio.alvo_id,
            empresa_id=empresa.id,
            vaga_id=vaga.id,
            situacao=SituacaoItemFilaEmpregos.DUPLICADA,
            campos_preenchidos=len(relatorio.campos_preenchidos),
            total_campos=relatorio.total_campos,
            bloqueios=(
                MotivoFilaEmpregos(
                    codigo="duplicada_de_vaga_publicada",
                    mensagem=(
                        "Mesmo título, empresa, local e descrição de uma vaga já publicada "
                        f"(anúncio {publicada.anuncio_id}, situação {publicada.situacao.value})."
                    ),
                ),
            ),
            alertas=alertas,
            chave_idempotencia=chave,
            situacao_publicacao_existente=publicada.situacao,
        )

    if existente is not None:
        return ItemFilaEmpregos(
            anuncio_id=anuncio.id,
            titulo=vaga.titulo_normalizado,
            alvo_id=anuncio.alvo_id,
            empresa_id=empresa.id,
            vaga_id=vaga.id,
            situacao=SituacaoItemFilaEmpregos.JA_REGISTRADA,
            campos_preenchidos=len(relatorio.campos_preenchidos),
            total_campos=relatorio.total_campos,
            bloqueios=(
                MotivoFilaEmpregos(
                    codigo="publicacao_existente",
                    mensagem=(
                        "Já existe uma operação com estado "
                        f"{existente.situacao.value}; consulte o histórico antes de repetir."
                    ),
                ),
            ),
            alertas=alertas,
            chave_idempotencia=chave,
            situacao_publicacao_existente=existente.situacao,
        )

    return ItemFilaEmpregos(
        anuncio_id=anuncio.id,
        titulo=vaga.titulo_normalizado,
        alvo_id=anuncio.alvo_id,
        empresa_id=empresa.id,
        vaga_id=vaga.id,
        situacao=SituacaoItemFilaEmpregos.ELEGIVEL,
        campos_preenchidos=len(relatorio.campos_preenchidos),
        total_campos=relatorio.total_campos,
        alertas=alertas,
        chave_idempotencia=chave,
        preparacao=preparacao,
    )


def preparar_fila_empregos(
    anuncios: Sequence[AnuncioVaga],
    *,
    alvos: Mapping[str, AlvoColeta],
    repositorio_empresas: RepositorioEmpresas,
    repositorio_vagas: RepositorioVagas,
    repositorio_publicacoes: RepositorioPublicacoesEmpregos,
    momento_referencia: datetime | None = None,
) -> ResultadoFilaEmpregos:
    """Avalia anúncios em ordem sem interromper o lote por bloqueios esperados."""

    itens = tuple(
        _preparar_item(
            anuncio,
            alvos=alvos,
            repositorio_empresas=repositorio_empresas,
            repositorio_vagas=repositorio_vagas,
            repositorio_publicacoes=repositorio_publicacoes,
            momento_referencia=momento_referencia,
        )
        for anuncio in anuncios
    )
    return ResultadoFilaEmpregos(itens=_deduplicar_itens(itens))


def _deduplicar_itens(
    itens: tuple[ItemFilaEmpregos, ...],
) -> tuple[ItemFilaEmpregos, ...]:
    """Impede publicar duas vezes a mesma vaga vinda de fontes diferentes."""

    aceitas_por_assinatura: dict[str, list[tuple[ItemFilaEmpregos, IdentidadeConteudo]]] = {}
    resultado: list[ItemFilaEmpregos] = []
    for item in itens:
        # Mesma regra usada contra as vagas já publicadas: começo da descrição
        # entre sites diferentes, descrição inteira no mesmo site.
        identidade = _identidade_do_item(item)
        if item.situacao is not SituacaoItemFilaEmpregos.ELEGIVEL or identidade is None:
            resultado.append(item)
            continue
        aceitas = aceitas_por_assinatura.setdefault(identidade.assinatura, [])
        original = next(
            (
                aceito
                for aceito, identidade_aceita in aceitas
                if mesma_vaga(
                    dominio_a=identidade_aceita.dominio,
                    assinatura_completa_a=identidade_aceita.assinatura_completa,
                    dominio_b=identidade.dominio,
                    assinatura_completa_b=identidade.assinatura_completa,
                )
            ),
            None,
        )
        if original is None:
            aceitas.append((item, identidade))
            resultado.append(item)
            continue
        resultado.append(
            replace(
                item,
                situacao=SituacaoItemFilaEmpregos.DUPLICADA,
                bloqueios=(
                    MotivoFilaEmpregos(
                        codigo="duplicada_entre_fontes",
                        mensagem=(
                            "Mesmo título, empresa, local e descrição de uma vaga já elegível "
                            f"(alvo original: {original.alvo_id or 'não informado'})."
                        ),
                    ),
                ),
                preparacao=None,
            )
        )
    return tuple(resultado)


def _identidade_do_item(item: ItemFilaEmpregos) -> IdentidadeConteudo | None:
    preparacao = item.preparacao
    if preparacao is None:
        return None
    proveniencia = getattr(preparacao, "proveniencia_fonte", None)
    return identidade_de_payload(
        preparacao.payload,
        dominio=proveniencia.dominio if proveniencia is not None else None,
    )
