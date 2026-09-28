"""Testes da interface segura de publicação em lote."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from scripts import publicar_lote_empregos as comando

from observatorio_vagas.config import Settings
from observatorio_vagas.integrations.empregos import (
    ItemFilaEmpregos,
    ResultadoFilaEmpregos,
    ResultadoPublicacaoLoteEmpregos,
    SituacaoItemFilaEmpregos,
)


def _fila_elegivel() -> ResultadoFilaEmpregos:
    return ResultadoFilaEmpregos(
        itens=(
            ItemFilaEmpregos(
                anuncio_id=UUID("10000000-0000-4000-8000-000000000001"),
                vaga_id=UUID("20000000-0000-4000-8000-000000000002"),
                empresa_id=UUID("30000000-0000-4000-8000-000000000003"),
                alvo_id="empresa_teste",
                titulo="Pessoa Desenvolvedora",
                situacao=SituacaoItemFilaEmpregos.ELEGIVEL,
            ),
        )
    )


def test_parser_usa_limites_seguros_e_catalogo_central() -> None:
    """Os padrões devem limitar o impacto de uma primeira execução."""

    opcoes = comando.criar_parser().parse_args([])

    assert opcoes.catalogo == Path("config/catalogo_fontes.csv")
    assert opcoes.limite == 100
    assert opcoes.maximo_envios == 10
    assert opcoes.confirmar_publicacao is False


def test_maximo_invalido_falha_antes_do_mongodb(capsys: Any) -> None:
    """Uma entrada perigosa não pode alcançar configurações nem banco."""

    codigo = comando.executar(["--maximo-envios", "101"])

    assert codigo == 2
    assert "maximo-envios deve estar entre 1 e 100" in capsys.readouterr().out


@pytest.mark.parametrize("confirmar", [False, True])
def test_confirmacao_controla_validacao_e_primeira_escrita(
    confirmar: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A simulação não escreve; o modo real valida antes de preparar o banco."""

    eventos: list[str] = []

    class ClienteFalso:
        def __init__(self, configuracoes: Settings) -> None:
            eventos.append("cliente")

        def validar_configuracao_publicacao(self) -> None:
            eventos.append("validar")

    monkeypatch.setattr(comando, "ClienteEmpregos", ClienteFalso)
    monkeypatch.setattr(
        comando,
        "RepositorioPublicacoesEmpregosMongoDB",
        lambda banco: eventos.append("repositorio") or object(),
    )
    monkeypatch.setattr(
        comando,
        "PublicadorEmpregos",
        lambda cliente, repositorio: object(),
    )
    monkeypatch.setattr(
        comando,
        "preparar_banco",
        lambda banco: eventos.append("preparar_banco"),
    )

    def publicar_falso(
        fila: ResultadoFilaEmpregos,
        **opcoes: Any,
    ) -> ResultadoPublicacaoLoteEmpregos:
        assert fila.elegiveis
        eventos.append(f"lote:{opcoes['confirmar_publicacao']}")
        return ResultadoPublicacaoLoteEmpregos(
            itens=(),
            confirmada=opcoes["confirmar_publicacao"],
            limite_envios=opcoes["limite_envios"],
        )

    monkeypatch.setattr(comando, "publicar_fila_empregos", publicar_falso)

    comando.executar_publicacao_lote(
        fila=_fila_elegivel(),
        configuracoes=cast(Settings, object()),
        conexao=cast(Any, SimpleNamespace(banco=object())),
        confirmar_publicacao=confirmar,
        maximo_envios=5,
    )

    if confirmar:
        assert eventos.index("validar") < eventos.index("preparar_banco")
        assert eventos.index("preparar_banco") < eventos.index("lote:True")
    else:
        assert "validar" not in eventos
        assert "preparar_banco" not in eventos
        assert "lote:False" in eventos
