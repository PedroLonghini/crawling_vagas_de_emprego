"""Fixtures compartilhadas pelos testes."""

# Pytest executa os testes e permite criar fixtures.
import pytest

# SecretStr representa um valor que não deve aparecer
# acidentalmente em logs ou mensagens de erro.
from pydantic import SecretStr

# Settings contém as configurações validadas da aplicação.
from observatorio_vagas.config import Settings


@pytest.fixture
def development_settings() -> Settings:
    """Cria configurações previsíveis e sem credenciais reais."""

    # Os testes usam valores próprios.
    #
    # Isso impede que eles dependam do MongoDB de desenvolvimento
    # e, principalmente, impede que utilizem um futuro banco de produção.
    return Settings(
        environment="development",
        # Esse endereço é apenas um valor seguro para os testes.
        # Neste momento o teste não abrirá uma conexão real.
        mongodb_uri=SecretStr("mongodb://127.0.0.1:27017/"),
        # O nome deixa claro que esse banco pertence aos testes.
        mongodb_database="observatorio_vagas_test",
        # Ainda não possuímos a chave da API.
        empregos_api_key=None,
    )
