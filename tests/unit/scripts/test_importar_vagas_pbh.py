"""Testes da interface segura do importador PBH."""

from datetime import UTC, datetime
from functools import partial

from scripts import importar_vagas_pbh


def test_previa_nao_abre_mongodb(capsys: object) -> None:
    """A execução padrão deve terminar antes de qualquer gravação."""

    codigo = importar_vagas_pbh.executar(
        [
            "--arquivo",
            "tests/fixtures/pbh/vagas_ofertadas.csv",
        ]
    )

    saida = capsys.readouterr().out
    assert codigo == 0
    assert "Anúncios válidos: 2" in saida
    assert "SIMULAÇÃO CONCLUÍDA" in saida


def test_somente_vigentes_aplica_filtro(capsys: object, monkeypatch: object) -> None:
    """O filtro deve retirar anúncios encerrados sem apagar o histórico."""

    # Congela o relógio: a validade operacional depende da data da observação.
    monkeypatch.setattr(
        importar_vagas_pbh,
        "importar_vagas_pbh_csv",
        partial(
            importar_vagas_pbh.importar_vagas_pbh_csv,
            observado_em=datetime(2026, 9, 10, tzinfo=UTC),
        ),
    )
    codigo = importar_vagas_pbh.executar(
        [
            "--arquivo",
            "tests/fixtures/pbh/vagas_ofertadas.csv",
            "--somente-vigentes",
        ]
    )

    saida = capsys.readouterr().out
    assert codigo == 0
    assert "Linhas inválidas ignoradas: 1" in saida
    assert "Selecionados para esta execução: 1" in saida


def test_limite_inseguro_falha_antes_de_ler_arquivo(capsys: object) -> None:
    """Limites inválidos não podem iniciar a importação."""

    codigo = importar_vagas_pbh.executar(
        [
            "--arquivo",
            "arquivo-que-nao-existe.csv",
            "--limite",
            "0",
        ]
    )

    assert codigo == 2
    assert "limite deve estar entre 1 e 10000" in capsys.readouterr().out
