"""Testes da conexão com o MongoDB."""

# UTC é o fuso esperado na configuração do cliente.
from datetime import UTC

# Pytest permite verificar se um erro esperado foi produzido.
import pytest

# SecretStr cria uma URI protegida durante os testes.
from pydantic import SecretStr

# ConnectionFailure representa uma falha de conexão do PyMongo.
from pymongo.errors import ConnectionFailure

# Settings fornece configurações controladas para o teste.
from observatorio_vagas.config import Settings

# Importamos o módulo connection.
#
# Isso permite trocar temporariamente o MongoClient verdadeiro
# por um cliente falso.
# Este é o erro seguro criado pelo nosso projeto.
from observatorio_vagas.storage.mongodb import ErroConexaoMongoDB, connection


def test_conexao_cria_cliente_somente_quando_necessario(
    monkeypatch,
) -> None:
    """Cria um único cliente e utiliza as configurações corretas."""

    # Este dicionário funcionará como um relatório.
    #
    # O cliente falso anotará aqui tudo que recebeu.
    chamadas: dict[str, object] = {}

    class AdministracaoFalsa:
        """Simula a área administrativa de um MongoDB funcionando."""

        def command(self, nome: str) -> dict[str, float]:
            """Simula a execução do comando ping."""

            chamadas["comando"] = nome

            # Esta é a resposta normal de um MongoDB disponível.
            return {"ok": 1.0}

    class ClienteFalso:
        """Substitui MongoClient sem acessar um servidor real."""

        def __init__(self, uri: str, **opcoes: object) -> None:
            """Registra as configurações recebidas."""

            chamadas["uri"] = uri
            chamadas["opcoes"] = opcoes

            # Contamos quantos clientes foram criados.
            chamadas["quantidade_clientes"] = int(chamadas.get("quantidade_clientes", 0)) + 1

            # A classe de conexão acessará cliente.admin.command().
            self.admin = AdministracaoFalsa()

        def __getitem__(self, nome_banco: str) -> str:
            """Simula a seleção de um banco com colchetes."""

            chamadas["banco"] = nome_banco
            return f"banco-falso:{nome_banco}"

        def close(self) -> None:
            """Registra que a conexão foi fechada."""

            chamadas["fechado"] = True

    # monkeypatch troca MongoClient somente durante este teste.
    #
    # Quando o teste terminar, o Pytest restaura o objeto verdadeiro.
    monkeypatch.setattr(
        connection,
        "MongoClient",
        ClienteFalso,
    )

    # Criamos configurações exclusivas para o teste.
    settings = Settings(
        mongodb_uri=SecretStr("mongodb://usuario:segredo@servidor:27017/"),
        mongodb_database="observatorio_vagas_test",
        mongodb_server_selection_timeout_ms=3210,
    )

    # Criar ConexaoMongoDB ainda não deve criar MongoClient.
    conexao = connection.ConexaoMongoDB(settings)

    # O dicionário continua vazio porque nenhuma conexão foi solicitada.
    assert chamadas == {}

    # O ping solicitará o cliente pela primeira vez.
    assert conexao.verificar_conexao() is True

    # Confirma que o comando correto foi enviado.
    assert chamadas["comando"] == "ping"

    # Confirma que o endereço correto chegou ao cliente.
    assert chamadas["uri"] == "mongodb://usuario:segredo@servidor:27017/"

    # Recuperamos as opções entregues ao cliente falso.
    opcoes = chamadas["opcoes"]

    # Esta confirmação ajuda o Python a saber que opcoes é um dicionário.
    assert isinstance(opcoes, dict)

    assert opcoes["appname"] == "observatorio-vagas"

    # O cliente precisa devolver datas com fuso UTC.
    assert opcoes["tz_aware"] is True
    assert opcoes["tzinfo"] is UTC

    assert opcoes["serverSelectionTimeoutMS"] == 3210
    assert opcoes["uuidRepresentation"] == "standard"

    # Selecionamos o banco configurado.
    assert conexao.banco == "banco-falso:observatorio_vagas_test"
    assert chamadas["banco"] == "observatorio_vagas_test"

    # O mesmo cliente deve ter sido reutilizado.
    assert chamadas["quantidade_clientes"] == 1

    # Fechamos a conexão.
    conexao.fechar()

    # Confirmamos que close foi executado.
    assert chamadas["fechado"] is True


def test_conexao_transforma_falha_em_erro_seguro(
    monkeypatch,
) -> None:
    """Uma falha do PyMongo deve virar uma mensagem segura."""

    class AdministracaoComFalha:
        """Simula a área administrativa de um servidor indisponível."""

        def command(self, nome: str) -> dict[str, float]:
            """Produz o mesmo tipo de erro de uma conexão real."""

            raise ConnectionFailure("servidor indisponível")

    class ClienteComFalha:
        """Simula um MongoClient que não consegue conectar."""

        def __init__(self, uri: str, **opcoes: object) -> None:
            """Prepara a administração que sempre falhará."""

            self.admin = AdministracaoComFalha()

        def close(self) -> None:
            """Simula o encerramento do cliente."""

    # Substituímos novamente o cliente verdadeiro.
    monkeypatch.setattr(
        connection,
        "MongoClient",
        ClienteComFalha,
    )

    settings = Settings(
        mongodb_uri=SecretStr("mongodb://usuario:segredo@servidor:27017/"),
        mongodb_database="observatorio_vagas_test",
    )

    conexao = connection.ConexaoMongoDB(settings)

    # Confirmamos que a aplicação apresenta nosso erro seguro.
    with pytest.raises(
        ErroConexaoMongoDB,
        match="Não foi possível conectar ao MongoDB",
    ) as erro_recebido:
        conexao.verificar_conexao()

    # A senha não pode aparecer na mensagem apresentada.
    assert "segredo" not in str(erro_recebido.value)
