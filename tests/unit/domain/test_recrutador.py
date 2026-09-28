"""Testes da entidade responsável pelos recrutadores."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from observatorio_vagas.domain.recrutador import Recrutador


def test_recrutador_guarda_dados_da_empresa() -> None:
    """Verifica a criação de um recrutador válido."""

    empresa_id = uuid4()

    recrutador = Recrutador(
        empresa_id=empresa_id,
        id_externo="RECRUTADOR-123",
        nome="Maria da Silva",
        email="  MARIA@EMPRESA.COM.BR  ",
    )

    # O recrutador precisa continuar ligado à empresa correta.
    assert recrutador.empresa_id == empresa_id

    # O e-mail deve ser armazenado sem espaços e em minúsculas.
    assert recrutador.email == "maria@empresa.com.br"

    # O recrutador começa ativo por padrão.
    assert recrutador.ativo is True


def test_recrutador_aceita_id_externo_ausente() -> None:
    """Permite salvar um recrutador ainda sem identificador externo."""

    recrutador = Recrutador(
        empresa_id=uuid4(),
        id_externo="   ",
        nome="João da Silva",
        email="joao@empresa.com.br",
    )

    # Uma string vazia deve ser convertida para None.
    assert recrutador.id_externo is None


def test_recrutador_rejeita_email_invalido() -> None:
    """Impede o armazenamento de um e-mail claramente incorreto."""

    with pytest.raises(
        ValidationError,
        match="e-mail inválido",
    ):
        Recrutador(
            empresa_id=uuid4(),
            nome="Pessoa Recrutadora",
            email="email-sem-arroba",
        )


def test_recrutador_rejeita_datas_invertidas() -> None:
    """Impede uma atualização anterior à criação."""

    criado_em = datetime.now(UTC)

    with pytest.raises(
        ValidationError,
        match="atualizado_em não pode ser anterior",
    ):
        Recrutador(
            empresa_id=uuid4(),
            nome="Pessoa Recrutadora",
            email="recrutador@empresa.com.br",
            criado_em=criado_em,
            atualizado_em=criado_em - timedelta(days=1),
        )
