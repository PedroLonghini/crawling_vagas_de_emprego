"""Testes dos contratos de armazenamento."""

import pytest

from observatorio_vagas.storage import ResultadoEscrita


def test_resultado_escrita_calcula_processados() -> None:
    """Soma corretamente inseridos, atualizados e inalterados."""

    resultado = ResultadoEscrita(
        recebidos=10,
        inseridos=4,
        atualizados=3,
        inalterados=3,
    )

    assert resultado.processados == 10


def test_resultado_escrita_rejeita_numero_negativo() -> None:
    """Um banco nunca pode informar quantidade negativa."""

    with pytest.raises(
        ValueError,
        match="inseridos não pode ser negativo",
    ):
        ResultadoEscrita(
            recebidos=1,
            inseridos=-1,
            atualizados=1,
            inalterados=1,
        )


def test_resultado_escrita_rejeita_total_incorreto() -> None:
    """A soma dos resultados deve ser igual ao total recebido."""

    with pytest.raises(
        ValueError,
        match="devem totalizar",
    ):
        ResultadoEscrita(
            recebidos=10,
            inseridos=2,
            atualizados=2,
            inalterados=2,
        )
