"""Testes das execuções e observações históricas."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from observatorio_vagas.domain.coleta import ExecucaoColeta, MetricasColeta
from observatorio_vagas.domain.enums import Fonte, StatusAnuncio, StatusColeta
from observatorio_vagas.domain.historico import AlteracaoCampo, ObservacaoAnuncio


def test_metricas_calculam_processados() -> None:
    metricas = MetricasColeta(
        recebidos=10,
        novos=3,
        alterados=2,
        inalterados=4,
        invalidos=1,
    )

    assert metricas.processados == 10


def test_metricas_rejeitam_processamento_acima_do_recebido() -> None:
    with pytest.raises(ValidationError, match="não podem superar recebidos"):
        MetricasColeta(recebidos=1, novos=2)


def test_execucao_rejeita_data_final_anterior() -> None:
    inicio = datetime.now(UTC)

    with pytest.raises(ValidationError, match="não pode ser anterior"):
        ExecucaoColeta(
            fonte=Fonte.EMPREGOS,
            versao_conector="1.0.0",
            status=StatusColeta.FALHOU,
            iniciado_em=inicio,
            finalizado_em=inicio - timedelta(seconds=1),
        )


def test_observacao_registra_alteracao() -> None:
    observacao = ObservacaoAnuncio(
        anuncio_id=uuid4(),
        execucao_coleta_id=uuid4(),
        status=StatusAnuncio.ALTERADO,
        hash_conteudo="b" * 64,
        snapshot={"titulo": "Analista de Dados Pleno"},
        alteracoes=[
            AlteracaoCampo(
                campo="titulo",
                valor_anterior="Analista de Dados",
                valor_atual="Analista de Dados Pleno",
            )
        ],
        referencia_bruta="empregos/vaga-123.json",
    )

    assert observacao.houve_alteracao
    assert observacao.alteracoes[0].campo == "titulo"


def test_alteracao_exige_valores_diferentes() -> None:
    with pytest.raises(ValidationError, match="valores diferentes"):
        AlteracaoCampo(campo="titulo", valor_anterior="Analista", valor_atual="Analista")
