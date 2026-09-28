"""Testes da verificação local da configuração de publicação."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr
from scripts import verificar_configuracao_publicacao_empregos as comando

from observatorio_vagas.config import Settings


def _configuracoes_validas() -> Settings:
    """Monta um ambiente de homologação sem usar uma credencial real."""

    return Settings(
        environment="staging",
        empregos_api_base_url="https://parceiro.example.com/api",
        empregos_api_publication_path="/v1/job-postings",
        empregos_api_auth_header="X-API-Key",
        empregos_api_key=SecretStr("chave-de-teste-que-nao-pode-aparecer"),
        empregos_publicacao_habilitada=True,
    )


def test_verificacao_pronta_nao_exibe_chave(capsys: Any) -> None:
    """O diagnóstico pode ser compartilhado sem vazar a credencial."""

    assert comando.verificar(_configuracoes_validas()) == 0

    saida = capsys.readouterr().out
    assert "PRONTO PARA UMA PUBLICAÇÃO-TESTE" in saida
    assert "chave-de-teste-que-nao-pode-aparecer" not in saida


def test_verificacao_incompleta_nao_faz_passar_ambiente_development(capsys: Any) -> None:
    """A publicação real continua vedada em desenvolvimento."""

    configuracoes = _configuracoes_validas().model_copy(update={"environment": "development"})

    assert comando.verificar(configuracoes) == 2
    assert "AINDA NÃO PRONTO" in capsys.readouterr().out
