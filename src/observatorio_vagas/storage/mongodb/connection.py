"""Criação e gerenciamento da conexão com o MongoDB."""

from __future__ import annotations

# UTC garante que todas as datas retornem com um fuso conhecido.
from datetime import UTC

# TracebackType representa as informações internas de um erro.
#
# Ele será usado no método __exit__, responsável por fechar
# automaticamente a conexão.
from types import TracebackType

# MongoClient é o cliente oficial para conversar com o MongoDB.
from pymongo import MongoClient

# Database representa um banco selecionado dentro do MongoDB.
from pymongo.database import Database

# PyMongoError representa os erros que podem acontecer
# durante a comunicação com o MongoDB.
from pymongo.errors import PyMongoError

# Settings contém o endereço, o nome do banco e o tempo de espera.
from observatorio_vagas.config import Settings


class ErroConexaoMongoDB(RuntimeError):
    """Erro seguro apresentado quando o MongoDB não responde."""


class ConexaoMongoDB:
    """Gerencia uma conexão reutilizável com o MongoDB."""

    def __init__(self, settings: Settings) -> None:
        """Recebe as configurações sem abrir a conexão imediatamente."""

        # Guardamos as configurações recebidas.
        #
        # Elas serão utilizadas quando o programa realmente
        # precisar acessar o MongoDB.
        self._settings = settings

        # No começo ainda não existe nenhum cliente.
        #
        # O caractere "_" indica que este atributo é interno
        # e não deveria ser alterado diretamente por outros arquivos.
        self._cliente: MongoClient | None = None

    @property
    def cliente(self) -> MongoClient:
        """Cria o cliente na primeira utilização e depois o reutiliza."""

        # A conexão só será preparada quando alguém solicitar o cliente.
        #
        # Isso evita conectar ao banco apenas por importar este arquivo.
        if self._cliente is None:
            # mongodb_uri é um SecretStr.
            #
            # get_secret_value() revela o endereço somente aqui,
            # porque o PyMongo precisa do valor verdadeiro.
            uri = self._settings.mongodb_uri.get_secret_value()

            # Criamos o cliente oficial do MongoDB.
            self._cliente = MongoClient(
                uri,
                # Identifica nossa aplicação nos registros do MongoDB.
                appname="observatorio-vagas",
                # Solicita que o PyMongo devolva datas com fuso horário.
                #
                # Sem isso, o MongoDB devolveria datetime sem tzinfo,
                # mas nossos modelos exigem datas conscientes do fuso.
                tz_aware=True,
                # Define UTC como o fuso usado nas datas recebidas.
                tzinfo=UTC,
                # Limita quanto tempo o programa esperará
                # para localizar o servidor.
                serverSelectionTimeoutMS=(self._settings.mongodb_server_selection_timeout_ms),
                # Informa ao MongoDB como armazenar os UUIDs
                # utilizados nos modelos do projeto.
                uuidRepresentation="standard",
            )

        # Se o cliente já existia, ele será simplesmente reutilizado.
        return self._cliente

    @property
    def banco(self) -> Database:
        """Seleciona o banco informado nas configurações."""

        # Os colchetes selecionam o banco pelo nome.
        #
        # Esta linha ainda não cria fisicamente o banco.
        # O MongoDB cria o banco quando o primeiro documento
        # é realmente armazenado.
        return self.cliente[self._settings.mongodb_database]

    def verificar_conexao(self) -> bool:
        """Executa um ping para confirmar que o MongoDB responde."""

        try:
            # ping é uma operação segura.
            #
            # Ele apenas pergunta se o servidor está disponível
            # e não cria, altera ou apaga nenhum dado.
            resposta = self.cliente.admin.command("ping")

            # Normalmente a resposta será parecida com:
            #
            # {"ok": 1.0}
            #
            # bool transforma 1.0 em True.
            return bool(resposta.get("ok"))

        except PyMongoError as erro_original:
            # Criamos uma mensagem simples e segura.
            #
            # Não incluímos a URI porque futuramente ela poderá
            # conter usuário e senha.
            raise ErroConexaoMongoDB(
                "Não foi possível conectar ao MongoDB. "
                "Verifique o endereço, a rede e se o servidor está ligado."
            ) from erro_original

    def fechar(self) -> None:
        """Fecha o cliente caso ele tenha sido criado."""

        # Se o cliente continua sendo None, não existe nada para fechar.
        if self._cliente is not None:
            # close libera as conexões e outros recursos utilizados.
            self._cliente.close()

            # Depois de fechar, voltamos para o estado inicial.
            #
            # Caso a classe seja utilizada novamente,
            # um novo cliente será criado.
            self._cliente = None

    def __enter__(self) -> ConexaoMongoDB:
        """Permite utilizar esta classe com a palavra `with`."""

        # Antes de entregar a conexão, verificamos se ela funciona.
        self.verificar_conexao()

        # O próprio objeto será disponibilizado dentro do bloco with.
        return self

    def __exit__(
        self,
        tipo_erro: type[BaseException] | None,
        erro: BaseException | None,
        rastreamento: TracebackType | None,
    ) -> None:
        """Fecha a conexão automaticamente ao sair do bloco `with`."""

        # O fechamento acontecerá mesmo se ocorrer um erro
        # dentro do bloco with.
        self.fechar()
