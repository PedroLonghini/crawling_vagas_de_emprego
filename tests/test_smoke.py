"""Testes mínimos da fundação do projeto."""

import json
import logging
from io import StringIO

import pytest
from pydantic import ValidationError

from observatorio_vagas import __version__
from observatorio_vagas.config import Settings
from observatorio_vagas.logging_config import configure_logging


def test_package_can_be_imported() -> None:
    assert __version__ == "0.1.0"


def test_development_settings_have_safe_defaults(development_settings: Settings) -> None:
    """Confirma que as configurações de desenvolvimento são seguras."""

    # O teste deve executar como desenvolvimento.
    assert development_settings.environment == "development"

    # INFO é o nível normal de logs.
    assert development_settings.log_level == "INFO"

    # O endereço foi armazenado como segredo.
    #
    # get_secret_value() deve ser utilizado somente quando o programa
    # realmente precisar abrir a conexão.
    assert development_settings.mongodb_uri.get_secret_value() == "mongodb://127.0.0.1:27017/"

    # Os testes devem usar um banco separado.
    assert development_settings.mongodb_database == "observatorio_vagas_test"

    # Ainda não temos a chave da API.
    assert development_settings.empregos_api_key is None

    # Garante que não estamos em produção.
    assert not development_settings.is_production

    # O endereço completo não deve aparecer quando Settings for impresso.
    # Isso evita revelar uma futura senha em logs.
    assert "mongodb://127.0.0.1:27017/" not in repr(development_settings)


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(request_timeout_seconds=0)


def test_logging_is_structured_and_redacts_secrets() -> None:
    stream = StringIO()
    configure_logging(stream=stream)

    logger = logging.getLogger("observatorio.test")
    logger.info(
        "request token=segredo",
        extra={"context": {"api_key": "valor-secreto", "source": "empregos"}},
    )

    payload = json.loads(stream.getvalue())

    assert payload["level"] == "INFO"
    assert payload["logger"] == "observatorio.test"
    assert payload["message"] == "request token=[REDACTED]"
    assert payload["context"]["api_key"] == "[REDACTED]"
    assert payload["context"]["source"] == "empregos"
