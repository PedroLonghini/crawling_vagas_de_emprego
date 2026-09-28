"""Fronteiras do calendário local na seleção de ontem."""

from datetime import UTC, date, datetime

import pytest

from observatorio_vagas.extraction.data_publicacao import dia_publicacao, ontem_brasilia


def test_ontem_nao_depende_do_calendario_utc():
    assert ontem_brasilia(datetime(2026, 9, 16, 2, tzinfo=UTC)) == date(2026, 9, 14)
    assert ontem_brasilia(datetime(2026, 9, 16, 3, tzinfo=UTC)) == date(2026, 9, 15)


def test_nao_inventa_data_ou_fuso():
    assert dia_publicacao(None) is None
    assert dia_publicacao(datetime(2026, 9, 15, 23)) is None
    assert dia_publicacao(date(2026, 9, 15)) == date(2026, 9, 15)
    with pytest.raises(ValueError):
        ontem_brasilia(datetime(2026, 9, 15))


def test_meia_noite_utc_restaurada_do_mongodb_preserva_data_calendario():
    """Um date salvo no MongoDB não pode voltar um dia ao filtrar no Brasil."""

    assert dia_publicacao(datetime(2026, 9, 17, tzinfo=UTC)) == date(2026, 9, 17)
    assert dia_publicacao(datetime(2026, 9, 17, 1, tzinfo=UTC)) == date(2026, 9, 16)
