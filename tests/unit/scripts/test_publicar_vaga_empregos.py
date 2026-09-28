"""Testes do comando seguro de publicação no Empregos."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from scripts import publicar_vaga_empregos as comando

from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import OperacaoPublicacaoEmpregos
from observatorio_vagas.integrations.empregos import (
    ResultadoEnvioEmpregos,
    ResultadoPublicacaoEmpregosPersistida,
    SituacaoEnvioEmpregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ResultadoPreparacaoEmpregos,
)

VAGA_ID = UUID("b1000000-0000-4000-8000-0000000000b1")
ANUNCIO_ID = UUID("b2000000-0000-4000-8000-0000000000b2")


def criar_contexto() -> comando.ContextoPublicacao:
    """Cria somente os atributos usados pela execução do comando."""

    return cast(
        comando.ContextoPublicacao,
        SimpleNamespace(
            preparacao=cast(ResultadoPreparacaoEmpregos, object()),
            vaga=SimpleNamespace(id=VAGA_ID),
            anuncio=SimpleNamespace(id=ANUNCIO_ID),
        ),
    )


def criar_envio(
    situacao: SituacaoEnvioEmpregos,
    *,
    status_http: int | None = None,
) -> ResultadoEnvioEmpregos:
    """Monta o resultado que o publicador devolveria."""

    return ResultadoEnvioEmpregos(
        situacao=situacao,
        external_job_posting_id="VAGA-001",
        operation_type="CREATE",
        payload_sha256="a" * 64,
        tentativas=0 if situacao is SituacaoEnvioEmpregos.SIMULADO else 1,
        status_http=status_http,
    )


def test_parser_exige_anuncio_e_vaga() -> None:
    """O comando nunca escolhe uma vaga implicitamente."""

    parser = comando.criar_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_uuid_invalido_produz_mensagem_clara() -> None:
    """O texto de exemplo não pode chegar à consulta do MongoDB."""

    with pytest.raises(ValueError, match="anuncio-id não é um UUID válido"):
        comando._converter_uuid("UUID_DO_ANUNCIO", nome="anuncio-id")


def test_simulacao_nao_prepara_indices(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """Sem confirmação, nem o esquema do MongoDB deve ser alterado."""

    eventos: list[str] = []

    class ClienteFalso:
        def __init__(self, configuracoes: object) -> None:
            eventos.append("criar_cliente")

        def validar_configuracao_publicacao(self) -> None:
            eventos.append("validar")

    class PublicadorFalso:
        def __init__(self, cliente: object, repositorio: object) -> None:
            eventos.append("criar_publicador")

        def publicar(self, preparacao: object, **opcoes: object) -> object:
            eventos.append(f"publicar:{opcoes['confirmar_publicacao']}")
            return ResultadoPublicacaoEmpregosPersistida(
                envio=criar_envio(SituacaoEnvioEmpregos.SIMULADO),
                operacao=None,
            )

    monkeypatch.setattr(comando, "ClienteEmpregos", ClienteFalso)
    monkeypatch.setattr(
        comando,
        "RepositorioPublicacoesEmpregosMongoDB",
        lambda banco: object(),
    )
    monkeypatch.setattr(comando, "PublicadorEmpregos", PublicadorFalso)
    monkeypatch.setattr(
        comando,
        "preparar_banco",
        lambda banco: eventos.append("preparar_banco"),
    )

    comando.executar_publicacao(
        contexto=criar_contexto(),
        configuracoes=cast(Any, object()),
        conexao=cast(Any, SimpleNamespace(banco=object())),
        confirmar_publicacao=False,
    )

    assert "validar" not in eventos
    assert "preparar_banco" not in eventos
    assert "publicar:False" in eventos
    assert "POST executado: NÃO" in capsys.readouterr().out


def test_confirmacao_valida_antes_de_preparar_banco(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credenciais inválidas não podem causar nenhuma escrita."""

    eventos: list[str] = []

    class ClienteFalso:
        def __init__(self, configuracoes: object) -> None:
            eventos.append("criar_cliente")

        def validar_configuracao_publicacao(self) -> None:
            eventos.append("validar")

    class PublicadorFalso:
        def __init__(self, cliente: object, repositorio: object) -> None:
            eventos.append("criar_publicador")

        def publicar(self, preparacao: object, **opcoes: object) -> object:
            eventos.append(f"publicar:{opcoes['confirmar_publicacao']}")
            envio = criar_envio(SituacaoEnvioEmpregos.SUCESSO, status_http=201)
            operacao = (
                OperacaoPublicacaoEmpregos(
                    vaga_id=VAGA_ID,
                    anuncio_id=ANUNCIO_ID,
                    external_job_posting_id="VAGA-001",
                    operation_type="CREATE",
                    payload_sha256="a" * 64,
                )
                .iniciar_envio()
                .concluir(
                    situacao=SituacaoPublicacaoEmpregos.SUCESSO,
                    status_http=201,
                )
            )
            return ResultadoPublicacaoEmpregosPersistida(
                envio=envio,
                operacao=operacao,
            )

    monkeypatch.setattr(comando, "ClienteEmpregos", ClienteFalso)
    monkeypatch.setattr(
        comando,
        "RepositorioPublicacoesEmpregosMongoDB",
        lambda banco: object(),
    )
    monkeypatch.setattr(comando, "PublicadorEmpregos", PublicadorFalso)
    monkeypatch.setattr(
        comando,
        "preparar_banco",
        lambda banco: eventos.append("preparar_banco"),
    )

    comando.executar_publicacao(
        contexto=criar_contexto(),
        configuracoes=cast(Any, object()),
        conexao=cast(Any, SimpleNamespace(banco=object())),
        confirmar_publicacao=True,
    )

    assert eventos.index("validar") < eventos.index("preparar_banco")
    assert eventos.index("preparar_banco") < eventos.index("publicar:True")
