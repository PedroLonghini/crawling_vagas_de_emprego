"""Testes do coordenador seguro de publicações em lote."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

import pytest

from observatorio_vagas.domain.enums import (
    SituacaoPublicacaoEmpregos,
)
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.integrations.empregos import (
    ItemFilaEmpregos,
    MotivoFilaEmpregos,
    ResultadoEnvioEmpregos,
    ResultadoFilaEmpregos,
    ResultadoPublicacaoEmpregosPersistida,
    SituacaoEnvioEmpregos,
    SituacaoItemFilaEmpregos,
    SituacaoResultadoLoteEmpregos,
    publicar_fila_empregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
)


def _uuid(numero: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{numero:012d}")


def _item_elegivel(numero: int) -> ItemFilaEmpregos:
    return ItemFilaEmpregos(
        anuncio_id=_uuid(numero),
        vaga_id=_uuid(100 + numero),
        empresa_id=_uuid(200 + numero),
        alvo_id="empresa_teste",
        titulo=f"Vaga {numero}",
        situacao=SituacaoItemFilaEmpregos.ELEGIVEL,
        preparacao=cast(ResultadoPreparacaoEmpregos, object()),
    )


def _operacao_sucesso(item: ItemFilaEmpregos) -> OperacaoPublicacaoEmpregos:
    assert item.vaga_id is not None
    return (
        OperacaoPublicacaoEmpregos(
            vaga_id=item.vaga_id,
            anuncio_id=item.anuncio_id,
            external_job_posting_id=f"VAGA-{item.anuncio_id}",
            operation_type="CREATE",
            payload_sha256="a" * 64,
        )
        .iniciar_envio()
        .concluir(
            situacao=SituacaoPublicacaoEmpregos.SUCESSO,
            status_http=201,
        )
    )


@dataclass
class PublicadorFalso:
    """Registra chamadas e permite falhar anúncios específicos."""

    erros: dict[UUID, Exception] = field(default_factory=dict)
    chamadas: list[tuple[UUID, bool]] = field(default_factory=list)

    def publicar(
        self,
        preparacao: ResultadoPreparacaoEmpregos,
        *,
        vaga_id: UUID,
        anuncio_id: UUID,
        confirmar_publicacao: bool = False,
    ) -> ResultadoPublicacaoEmpregosPersistida:
        assert preparacao is not None
        self.chamadas.append((anuncio_id, confirmar_publicacao))

        erro = self.erros.get(anuncio_id)
        if erro is not None:
            raise erro

        envio = ResultadoEnvioEmpregos(
            situacao=(
                SituacaoEnvioEmpregos.SUCESSO
                if confirmar_publicacao
                else SituacaoEnvioEmpregos.SIMULADO
            ),
            external_job_posting_id=f"VAGA-{anuncio_id}",
            operation_type="CREATE",
            payload_sha256="a" * 64,
            tentativas=1 if confirmar_publicacao else 0,
            status_http=201 if confirmar_publicacao else None,
        )
        operacao = None

        if confirmar_publicacao:
            item = ItemFilaEmpregos(
                anuncio_id=anuncio_id,
                vaga_id=vaga_id,
                titulo="Vaga",
                alvo_id="empresa_teste",
                empresa_id=None,
                situacao=SituacaoItemFilaEmpregos.ELEGIVEL,
            )
            operacao = _operacao_sucesso(item)

        return ResultadoPublicacaoEmpregosPersistida(
            envio=envio,
            operacao=operacao,
        )


def test_simulacao_processa_elegivel_e_ignora_demais() -> None:
    """Bloqueios e histórico nunca podem chegar ao publicador individual."""

    elegivel = _item_elegivel(1)
    bloqueado = ItemFilaEmpregos(
        anuncio_id=_uuid(2),
        vaga_id=None,
        empresa_id=None,
        alvo_id="empresa_teste",
        titulo="Vaga bloqueada",
        situacao=SituacaoItemFilaEmpregos.BLOQUEADA,
        bloqueios=(
            MotivoFilaEmpregos(
                codigo="fonte_sem_permissao",
                mensagem="A fonte não autoriza republicação.",
            ),
        ),
    )
    registrada = ItemFilaEmpregos(
        anuncio_id=_uuid(3),
        vaga_id=_uuid(103),
        empresa_id=_uuid(203),
        alvo_id="empresa_teste",
        titulo="Vaga registrada",
        situacao=SituacaoItemFilaEmpregos.JA_REGISTRADA,
    )
    publicador = PublicadorFalso()

    resultado = publicar_fila_empregos(
        ResultadoFilaEmpregos((elegivel, bloqueado, registrada)),
        publicador=publicador,
    )

    assert [item.situacao for item in resultado.itens] == [
        SituacaoResultadoLoteEmpregos.SIMULADA,
        SituacaoResultadoLoteEmpregos.IGNORADA_BLOQUEIO,
        SituacaoResultadoLoteEmpregos.IGNORADA_HISTORICO,
    ]
    assert publicador.chamadas == [(elegivel.anuncio_id, False)]
    assert resultado.confirmada is False


def test_limite_adia_elegiveis_excedentes() -> None:
    """A prévia precisa representar o mesmo subconjunto permitido no envio."""

    itens = tuple(_item_elegivel(numero) for numero in range(1, 4))
    publicador = PublicadorFalso()

    resultado = publicar_fila_empregos(
        ResultadoFilaEmpregos(itens),
        publicador=publicador,
        limite_envios=2,
    )

    assert len(resultado.simuladas) == 2
    assert resultado.itens[2].situacao is SituacaoResultadoLoteEmpregos.ADIADA_LIMITE
    assert len(publicador.chamadas) == 2


def test_duplicada_e_ignorada_sem_falha_e_sem_ocupar_o_limite() -> None:
    """Repetida não é falha (o script sairia com erro) nem toma lugar de vaga nova."""

    duplicadas = tuple(
        ItemFilaEmpregos(
            anuncio_id=_uuid(10 + numero),
            vaga_id=_uuid(110 + numero),
            empresa_id=None,
            alvo_id="agregador",
            titulo="Vaga repetida",
            situacao=SituacaoItemFilaEmpregos.DUPLICADA,
            bloqueios=(
                MotivoFilaEmpregos(
                    codigo="duplicada_de_vaga_publicada",
                    mensagem="Mesma vaga já publicada.",
                ),
            ),
        )
        for numero in range(3)
    )
    novas = (_item_elegivel(1), _item_elegivel(2))
    publicador = PublicadorFalso()

    resultado = publicar_fila_empregos(
        ResultadoFilaEmpregos((*duplicadas, *novas)),
        publicador=publicador,
        limite_envios=2,
    )

    assert [item.situacao for item in resultado.itens] == [
        *([SituacaoResultadoLoteEmpregos.IGNORADA_DUPLICADA] * 3),
        SituacaoResultadoLoteEmpregos.SIMULADA,
        SituacaoResultadoLoteEmpregos.SIMULADA,
    ]
    assert resultado.falhas == ()
    assert len(resultado.ignoradas) == 3
    assert len(publicador.chamadas) == 2


def test_duplicada_descoberta_no_envio_nao_vira_falha_nem_gasta_limite() -> None:
    """Outra execução publicou a mesma vaga entre a fila e o envio."""

    from observatorio_vagas.integrations.empregos.publicador import (
        VagaJaPublicadaPorOutraFonte,
    )

    itens = tuple(_item_elegivel(numero) for numero in range(1, 4))
    publicada = _operacao_sucesso(_item_elegivel(9))
    publicador = PublicadorFalso(
        erros={itens[0].anuncio_id: VagaJaPublicadaPorOutraFonte(publicada)}
    )

    resultado = publicar_fila_empregos(
        ResultadoFilaEmpregos(itens), publicador=publicador, limite_envios=2
    )

    assert [item.situacao for item in resultado.itens] == [
        SituacaoResultadoLoteEmpregos.IGNORADA_DUPLICADA,
        SituacaoResultadoLoteEmpregos.SIMULADA,
        SituacaoResultadoLoteEmpregos.SIMULADA,
    ]
    assert resultado.falhas == ()


def test_trava_ocupada_adia_o_resto_do_lote() -> None:
    """Esperar 60 s por item não ajuda: o resto fica para a próxima execução."""

    from observatorio_vagas.storage.mongodb.publicacoes import TravaPublicacaoOcupada

    itens = tuple(_item_elegivel(numero) for numero in range(1, 4))
    publicador = PublicadorFalso(erros={itens[0].anuncio_id: TravaPublicacaoOcupada("ocupada")})

    resultado = publicar_fila_empregos(ResultadoFilaEmpregos(itens), publicador=publicador)

    assert [item.situacao for item in resultado.itens] == [
        SituacaoResultadoLoteEmpregos.FALHA,
        SituacaoResultadoLoteEmpregos.ADIADA_LIMITE,
        SituacaoResultadoLoteEmpregos.ADIADA_LIMITE,
    ]
    assert len(publicador.chamadas) == 1


def test_falha_confirmada_nao_interrompe_proxima_vaga() -> None:
    """Uma resposta ruim deve ficar isolada no item que a produziu."""

    primeira = _item_elegivel(1)
    segunda = _item_elegivel(2)
    publicador = PublicadorFalso(erros={primeira.anuncio_id: RuntimeError("recusada")})

    resultado = publicar_fila_empregos(
        ResultadoFilaEmpregos((primeira, segunda)),
        publicador=publicador,
        confirmar_publicacao=True,
    )

    assert resultado.itens[0].situacao is SituacaoResultadoLoteEmpregos.FALHA
    assert resultado.itens[0].mensagem == "recusada"
    assert resultado.itens[1].situacao is SituacaoResultadoLoteEmpregos.PUBLICADA
    assert len(resultado.publicadas) == 1
    assert len(resultado.falhas) == 1
    assert publicador.chamadas == [
        (primeira.anuncio_id, True),
        (segunda.anuncio_id, True),
    ]


def test_item_elegivel_incompleto_vira_falha_sem_chamar_publicador() -> None:
    """Uma inconsistência interna não pode provocar uma tentativa parcial."""

    incompleto = _item_elegivel(1)
    incompleto = ItemFilaEmpregos(
        anuncio_id=incompleto.anuncio_id,
        vaga_id=incompleto.vaga_id,
        empresa_id=incompleto.empresa_id,
        alvo_id=incompleto.alvo_id,
        titulo=incompleto.titulo,
        situacao=incompleto.situacao,
    )
    publicador = PublicadorFalso()

    resultado = publicar_fila_empregos(
        ResultadoFilaEmpregos((incompleto,)),
        publicador=publicador,
    )

    assert resultado.itens[0].situacao is SituacaoResultadoLoteEmpregos.FALHA
    assert publicador.chamadas == []


@pytest.mark.parametrize("limite", [0, 101])
def test_rejeita_limite_de_envios_inseguro(limite: int) -> None:
    """O serviço não aceita um lote vazio nem grande demais."""

    with pytest.raises(ValueError, match="entre 1 e 100"):
        publicar_fila_empregos(
            ResultadoFilaEmpregos(()),
            publicador=PublicadorFalso(),
            limite_envios=limite,
        )
