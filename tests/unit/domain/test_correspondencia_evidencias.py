"""Testes de correspondência explicável e proveniência."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from observatorio_vagas.domain.correspondencia import (
    CorrespondenciaAnuncios,
    SinalCorrespondencia,
)
from observatorio_vagas.domain.enums import (
    ClassificacaoCorrespondencia,
    MetodoExtracao,
)
from observatorio_vagas.domain.evidencias import EvidenciaExtracao


def test_correspondencia_guarda_sinais_explicaveis() -> None:
    correspondencia = CorrespondenciaAnuncios(
        anuncio_origem_id=uuid4(),
        anuncio_destino_id=uuid4(),
        pontuacao=0.92,
        classificacao=ClassificacaoCorrespondencia.CONFIRMADA,
        sinais=[
            SinalCorrespondencia(
                nome="titulo",
                similaridade=0.95,
                peso=0.5,
                valor_origem="Analista de Dados",
                valor_destino="Analista Dados",
            )
        ],
        versao_algoritmo="1.0.0",
    )

    assert correspondencia.sinais[0].contribuicao == pytest.approx(0.475)


def test_correspondencia_nao_compara_anuncio_com_ele_mesmo() -> None:
    anuncio_id = uuid4()

    with pytest.raises(ValidationError, match="ele mesmo"):
        CorrespondenciaAnuncios(
            anuncio_origem_id=anuncio_id,
            anuncio_destino_id=anuncio_id,
            pontuacao=1,
            classificacao=ClassificacaoCorrespondencia.CONFIRMADA,
            sinais=[SinalCorrespondencia(nome="id", similaridade=1, peso=1)],
            versao_algoritmo="1.0.0",
        )


def test_revisao_exige_responsavel_e_data_juntos() -> None:
    with pytest.raises(ValidationError, match="devem ser informados juntos"):
        CorrespondenciaAnuncios(
            anuncio_origem_id=uuid4(),
            anuncio_destino_id=uuid4(),
            pontuacao=0.5,
            classificacao=ClassificacaoCorrespondencia.AMBIGUA,
            sinais=[SinalCorrespondencia(nome="titulo", similaridade=0.5, peso=1)],
            versao_algoritmo="1.0.0",
            revisado_por="analista@empresa",
        )


def test_metodo_inferido_exige_trecho_de_evidencia() -> None:
    with pytest.raises(ValidationError, match="trecho_evidencia"):
        EvidenciaExtracao(
            anuncio_id=uuid4(),
            campo="modalidade",
            valor_extraido="hibrido",
            metodo=MetodoExtracao.REGRA,
            confianca=0.9,
            versao_extrator="1.0.0",
        )


def test_evidencia_manual_pode_ser_revisada() -> None:
    agora = datetime.now(UTC)
    evidencia = EvidenciaExtracao(
        anuncio_id=uuid4(),
        campo="senioridade",
        valor_extraido="senior",
        metodo=MetodoExtracao.MANUAL,
        confianca=1,
        versao_extrator="manual-1",
        revisado_por="analista@empresa",
        revisado_em=agora,
    )

    assert evidencia.revisado_por == "analista@empresa"
