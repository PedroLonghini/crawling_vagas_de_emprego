"""Testes do histórico idempotente de publicação no Empregos."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from observatorio_vagas.domain.enums import SituacaoPublicacaoEmpregos
from observatorio_vagas.domain.publicacao import (
    OperacaoPublicacaoEmpregos,
    calcular_chave_idempotencia_empregos,
)

VAGA_ID = UUID("91000000-0000-4000-8000-000000000091")
ANUNCIO_ID = UUID("92000000-0000-4000-8000-000000000092")
DATA_PREPARACAO = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
DATA_ENVIO = datetime(2026, 8, 31, 10, 1, tzinfo=UTC)
DATA_RESULTADO = datetime(2026, 8, 31, 10, 2, tzinfo=UTC)
HASH_PAYLOAD = "a" * 64


def criar_operacao(**alteracoes: object) -> OperacaoPublicacaoEmpregos:
    """Cria uma operação preparada e previsível."""

    dados: dict[str, object] = {
        "vaga_id": VAGA_ID,
        "anuncio_id": ANUNCIO_ID,
        "external_job_posting_id": "VAGA-001",
        "operation_type": "create",
        "payload_sha256": HASH_PAYLOAD,
        "criado_em": DATA_PREPARACAO,
        "atualizado_em": DATA_PREPARACAO,
    }
    dados.update(alteracoes)
    return OperacaoPublicacaoEmpregos.model_validate(dados)


def test_calcula_chave_estavel_para_a_mesma_operacao() -> None:
    """Espaços e caixa não podem criar outra identidade lógica."""

    primeira = calcular_chave_idempotencia_empregos(
        external_job_posting_id="VAGA-001",
        operation_type="CREATE",
    )
    segunda = calcular_chave_idempotencia_empregos(
        external_job_posting_id=" VAGA-001 ",
        operation_type="create",
    )

    assert primeira == segunda
    assert len(primeira) == 64


def test_operacao_preparada_calcula_chave_e_normaliza_tipo() -> None:
    """A chave nasce antes de qualquer tentativa de rede."""

    operacao = criar_operacao()

    assert operacao.operation_type == "CREATE"
    assert operacao.chave_idempotencia is not None
    assert operacao.situacao is SituacaoPublicacaoEmpregos.PREPARADA
    assert operacao.tentativas == 0


def test_rejeita_hash_de_payload_invalido() -> None:
    """O histórico não pode aceitar uma impressão digital ambígua."""

    with pytest.raises(ValueError, match="SHA-256"):
        criar_operacao(payload_sha256="invalido")


def test_percorre_fluxo_preparada_enviando_sucesso() -> None:
    """As transições carregam datas e resultado HTTP."""

    enviando = criar_operacao().iniciar_envio(momento=DATA_ENVIO)
    sucesso = enviando.concluir(
        situacao=SituacaoPublicacaoEmpregos.SUCESSO,
        momento=DATA_RESULTADO,
        status_http=201,
        request_id="req-001",
    )

    assert enviando.situacao is SituacaoPublicacaoEmpregos.ENVIANDO
    assert enviando.tentativas == 1
    assert enviando.enviado_em == DATA_ENVIO
    assert sucesso.situacao is SituacaoPublicacaoEmpregos.SUCESSO
    assert sucesso.status_http == 201
    assert sucesso.request_id == "req-001"
    assert sucesso.finalizado_em == DATA_RESULTADO


def test_sucesso_exige_status_http_2xx() -> None:
    """Um erro HTTP nunca pode ser registrado como sucesso."""

    enviando = criar_operacao().iniciar_envio(momento=DATA_ENVIO)

    with pytest.raises(ValueError, match="2xx"):
        enviando.concluir(
            situacao=SituacaoPublicacaoEmpregos.SUCESSO,
            momento=DATA_RESULTADO,
            status_http=500,
        )


def test_nao_conclui_operacao_que_ainda_nao_iniciou() -> None:
    """A resposta da API exige que o estado enviando tenha sido persistido."""

    with pytest.raises(ValueError, match="em envio"):
        criar_operacao().concluir(
            situacao=SituacaoPublicacaoEmpregos.REJEITADA,
            momento=DATA_RESULTADO,
            status_http=400,
        )
