"""Configurações validadas e carregadas a partir do ambiente."""

# lru_cache evita recriar as configurações toda vez que uma função pedir.
from functools import lru_cache

# Path representa caminhos de arquivos de maneira compatível com sistemas diferentes.
from pathlib import Path

# Literal limita uma variável a valores específicos.
from typing import Literal

# Field define validações.
# HttpUrl valida endereços.
# SecretStr protege segredos contra exibição acidental.
from pydantic import Field, HttpUrl, SecretStr

# BaseSettings lê configurações de variáveis de ambiente e do arquivo .env.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações compartilhadas pela aplicação."""

    # Configura como as variáveis serão lidas.
    model_config = SettingsConfigDict(
        # Durante desenvolvimento, procura um arquivo chamado .env.
        env_file=".env",
        # Lê o arquivo usando UTF-8.
        env_file_encoding="utf-8",
        # Todas as variáveis novas começam com OBS_.
        # Exemplo: OBS_LOG_LEVEL.
        env_prefix="OBS_",
        # Permite OBS_LOG_LEVEL ou obs_log_level.
        case_sensitive=False,
        # Ignora variáveis adicionais presentes no ambiente.
        extra="ignore",
        # Revalida o objeto se alguém alterar uma configuração.
        validate_assignment=True,
    )

    # Só aceita um destes três ambientes.
    environment: Literal[
        "development",
        "staging",
        "production",
    ] = "development"

    # Só aceita níveis válidos de log.
    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    # Endereço utilizado para conectar ao MongoDB.
    #
    # SecretStr protege o endereço porque uma conexão de produção
    # pode conter usuário e senha.
    #
    # O valor abaixo aponta para um MongoDB instalado no próprio computador.
    # Futuramente, se utilizarmos MongoDB Atlas, o endereço real será
    # informado no arquivo .env e não ficará escrito dentro do código.
    mongodb_uri: SecretStr = Field(
        default=SecretStr("mongodb://127.0.0.1:27017/"),
        repr=False,
    )

    # Nome do banco utilizado pela aplicação.
    #
    # Colocamos "_dev" no final para deixar claro que este é o banco
    # de desenvolvimento. Isso reduz o risco de executar testes
    # acidentalmente no banco de produção.
    mongodb_database: str = Field(
        default="observatorio_vagas_dev",
        min_length=1,
        max_length=63,
    )

    # Tempo máximo que a aplicação aguardará para descobrir
    # se o servidor do MongoDB está disponível.
    #
    # O valor é medido em milissegundos:
    # 5000 milissegundos = 5 segundos.
    #
    # Sem esse limite, uma conexão incorreta poderia deixar
    # o programa aparentemente travado por muito tempo.
    mongodb_server_selection_timeout_ms: int = Field(
        default=5000,
        ge=100,
        le=60000,
    )
    # Local onde respostas brutas poderão ser armazenadas.
    raw_storage_path: Path = Path("data/raw")

    # URL da API.
    # None significa que ainda não foi configurada.
    empregos_api_base_url: HttpUrl | None = None

    # Caminho exato do endpoint de publicação.
    #
    # Não existe valor padrão porque ele deverá ser copiado do
    # contrato oficial da API, sem adivinhação dentro do código.
    empregos_api_publication_path: str | None = None

    # Nome e prefixo do cabeçalho de autenticação.
    #
    # Exemplos possíveis de uma API seriam Authorization/Bearer ou
    # X-API-Key/sem prefixo. O projeto exigirá os valores oficiais.
    empregos_api_auth_header: str | None = None
    empregos_api_auth_prefix: str = ""

    # Chave protegida.
    # repr=False ajuda a impedir que ela apareça ao imprimir Settings.
    empregos_api_key: SecretStr | None = Field(
        default=None,
        repr=False,
    )

    # Trava operacional independente da chave e da URL.
    #
    # Mesmo com credenciais configuradas, nenhum POST real será feito
    # enquanto esta opção permanecer False.
    empregos_publicacao_habilitada: bool = False

    # Credenciais dos agregadores licenciados de vagas. Elas começam vazias:
    # cadastrar uma chave não ativa automaticamente uma fonte no catálogo.
    adzuna_app_id: SecretStr | None = Field(default=None, repr=False)
    adzuna_app_key: SecretStr | None = Field(default=None, repr=False)
    jooble_api_key: SecretStr | None = Field(default=None, repr=False)

    # Timeout maior que zero e limitado a 300 segundos.
    request_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=300,
    )

    # Quantidade de tentativas adicionais.
    max_retries: int = Field(
        default=3,
        ge=0,
        le=20,
    )

    # Intervalo entre coletas.
    collection_interval_hours: int = Field(
        default=24,
        ge=1,
        le=168,
    )

    @property
    def is_production(self) -> bool:
        """Retorna True somente quando o ambiente é produção."""

        return self.environment == "production"


# Guarda o resultado da função em memória.
@lru_cache
def get_settings() -> Settings:
    """Cria as configurações apenas uma vez por processo."""

    return Settings()
