"""Implementação MongoDB do repositório de anúncios de vagas."""

from __future__ import annotations

# Sequence aceita listas e tuplas.
#
# O crawler poderá entregar vários anúncios de uma vez,
# sem ficar preso a somente um tipo de coleção Python.
from collections.abc import Sequence

# UUID representa os identificadores internos dos anúncios e empresas.
from uuid import UUID

# DESCENDING organiza os anúncios mais recentes primeiro.
#
# ReturnDocument.AFTER pede ao MongoDB que devolva o documento
# depois da inserção ou atualização.
#
# UpdateOne representa uma atualização utilizada dentro de um lote.
from pymongo import DESCENDING, ReturnDocument, UpdateOne

# Collection representa uma coleção MongoDB.
from pymongo.collection import Collection

# Database representa o banco selecionado.
from pymongo.database import Database

# Erros que podem acontecer durante operações do PyMongo.
from pymongo.errors import (
    BulkWriteError,
    DuplicateKeyError,
    PyMongoError,
)

# AnuncioVaga é o modelo que este repositório armazena.
from observatorio_vagas.domain.anuncio import AnuncioVaga

# Fonte identifica de onde o anúncio veio.
#
# Exemplos:
# - empregos;
# - gupy;
# - página de carreiras;
# - JSON-LD.
from observatorio_vagas.domain.enums import Fonte

# ResultadoEscrita informa quantos documentos foram:
#
# - recebidos;
# - inseridos;
# - atualizados;
# - mantidos sem alteração.
from observatorio_vagas.storage.contracts import ResultadoEscrita

# Funções que transformam o modelo em BSON
# e transformam BSON novamente no modelo.
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)

# Nome centralizado da coleção de anúncios.
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_ANUNCIOS,
)


class ErroRepositorioAnunciosMongoDB(RuntimeError):
    """Erro seguro durante uma operação com anúncios."""


class ConflitoAnuncioMongoDB(ErroRepositorioAnunciosMongoDB):
    """Indica conflito de identidade durante a gravação."""


class RepositorioAnunciosMongoDB:
    """Salva e consulta anúncios encontrados nas fontes."""

    def __init__(self, banco: Database) -> None:
        """Seleciona a coleção sem executar nenhuma consulta."""

        # O repositório recebe o banco já conectado.
        #
        # Assim, esta classe não precisa conhecer:
        #
        # - a URI do MongoDB;
        # - a senha;
        # - o túnel SSH;
        # - o nome do banco.
        self._colecao: Collection = banco[COLECAO_ANUNCIOS]

    def salvar(
        self,
        anuncio: AnuncioVaga,
    ) -> AnuncioVaga:
        """Insere ou atualiza um anúncio de maneira idempotente."""

        # "Idempotente" significa que encontrar a mesma vaga
        # várias vezes não criará várias cópias dela.
        #
        # A verdadeira identidade externa do anúncio é:
        #
        # fonte + id_externo
        #
        # Exemplo:
        #
        # fonte = "gupy"
        # id_externo = "12345"
        #
        # O ID "12345" poderia existir também no Empregos.
        # Por isso precisamos utilizar os dois campos juntos.
        filtro = {
            "fonte": anuncio.fonte.value,
            "id_externo": anuncio.id_externo,
        }

        # Convertemos o modelo validado em um documento BSON.
        documento = modelo_para_documento(anuncio)

        # O MongoDB chama o identificador principal de "_id".
        #
        # Retiramos esse campo da atualização comum porque o MongoDB
        # não permite alterar o _id de um documento existente.
        id_novo = documento.pop("_id")

        # A primeira observação também não deve ser substituída
        # quando o crawler encontrar novamente a mesma vaga.
        #
        # Ela representa quando o anúncio entrou no nosso histórico.
        primeira_observacao = documento.pop("primeira_observacao_em")
        # Uma coleta mais antiga pode terminar depois de uma mais recente.
        # Nunca retrocedemos a última observação nesse caso.
        ultima_observacao = documento.pop("ultima_observacao_em")

        try:
            # find_one_and_update realiza tudo como uma operação atômica.
            #
            # Operação atômica significa que o MongoDB executa a
            # alteração como uma única ação protegida.
            documento_salvo = self._colecao.find_one_and_update(
                filtro,
                {
                    # $set atualiza os dados observados agora.
                    "$set": documento,
                    "$max": {"ultima_observacao_em": ultima_observacao},
                    # $setOnInsert é aplicado somente se o anúncio
                    # ainda não existir.
                    #
                    # Se ele já existir:
                    # - o UUID antigo será preservado;
                    # - a primeira observação será preservada.
                    "$setOnInsert": {
                        "_id": id_novo,
                        "primeira_observacao_em": (primeira_observacao),
                    },
                },
                # Se não existir, cria um novo documento.
                upsert=True,
                # Devolve o documento depois da alteração.
                return_document=ReturnDocument.AFTER,
            )

        except DuplicateKeyError as erro_original:
            # Normalmente significa que a mesma combinação
            # fonte + id_externo tentou ser criada simultaneamente.
            raise ConflitoAnuncioMongoDB(
                "já existe um anúncio com a mesma fonte e ID externo"
            ) from erro_original

        except PyMongoError as erro_original:
            # Não mostramos URI ou credenciais na mensagem.
            raise ErroRepositorioAnunciosMongoDB(
                "não foi possível salvar o anúncio no MongoDB"
            ) from erro_original

        # Em uma operação com upsert=True e ReturnDocument.AFTER,
        # o MongoDB normalmente sempre devolve um documento.
        #
        # Ainda assim, verificamos para evitar que um problema
        # inesperado passe silenciosamente.
        if documento_salvo is None:
            raise ErroRepositorioAnunciosMongoDB("o MongoDB não devolveu o anúncio salvo")

        # Reconstruímos o modelo para devolver:
        #
        # - o UUID realmente armazenado;
        # - a primeira observação original;
        # - todos os campos novamente validados.
        return documento_para_modelo(
            documento_salvo,
            AnuncioVaga,
        )

    def salvar_lote(
        self,
        anuncios: Sequence[AnuncioVaga],
    ) -> ResultadoEscrita:
        """Salva vários anúncios em uma única chamada ao MongoDB."""

        # Um lote vazio não precisa acessar o banco.
        if not anuncios:
            return ResultadoEscrita(
                recebidos=0,
                inseridos=0,
                atualizados=0,
                inalterados=0,
            )

        # Esta lista receberá uma operação MongoDB
        # para cada anúncio entregue pelo crawler.
        operacoes: list[UpdateOne] = []

        for anuncio in anuncios:
            # Identidade externa do anúncio.
            filtro = {
                "fonte": anuncio.fonte.value,
                "id_externo": anuncio.id_externo,
            }

            # Converte o modelo em documento BSON.
            documento = modelo_para_documento(anuncio)

            # _id só será utilizado caso o anúncio seja novo.
            id_novo = documento.pop("_id")

            # A data da primeira observação também só será utilizada
            # caso o anúncio ainda não exista.
            primeira_observacao = documento.pop("primeira_observacao_em")
            ultima_observacao = documento.pop("ultima_observacao_em")

            # Preparamos a operação sem executá-la imediatamente.
            operacoes.append(
                UpdateOne(
                    filtro,
                    {
                        "$set": documento,
                        "$max": {"ultima_observacao_em": ultima_observacao},
                        "$setOnInsert": {
                            "_id": id_novo,
                            "primeira_observacao_em": (primeira_observacao),
                        },
                    },
                    upsert=True,
                )
            )

        try:
            # Envia todas as operações em uma única chamada.
            #
            # ordered=False permite que o MongoDB continue processando
            # os outros anúncios se uma operação específica falhar.
            resultado = self._colecao.bulk_write(
                operacoes,
                ordered=False,
            )

        except BulkWriteError as erro_original:
            raise ConflitoAnuncioMongoDB(
                "o lote possui anúncios com identidades conflitantes"
            ) from erro_original

        except PyMongoError as erro_original:
            raise ErroRepositorioAnunciosMongoDB(
                "não foi possível salvar o lote de anúncios"
            ) from erro_original

        # upserted_count informa quantos anúncios eram novos.
        inseridos = resultado.upserted_count

        # modified_count informa quantos anúncios existentes mudaram.
        atualizados = resultado.modified_count

        # O restante já existia com o mesmo conteúdo.
        inalterados = len(anuncios) - inseridos - atualizados

        return ResultadoEscrita(
            recebidos=len(anuncios),
            inseridos=inseridos,
            atualizados=atualizados,
            inalterados=inalterados,
        )

    def buscar_por_id(
        self,
        anuncio_id: UUID,
    ) -> AnuncioVaga | None:
        """Procura um anúncio pelo UUID interno."""

        return self._buscar_um(
            {"_id": anuncio_id},
        )

    def buscar_por_fonte(
        self,
        fonte: Fonte,
        id_externo: str,
    ) -> AnuncioVaga | None:
        """Procura um anúncio usando sua identidade na fonte."""

        # Removemos espaços acidentais.
        id_normalizado = id_externo.strip()

        # Uma busca vazia não deve acessar o banco.
        if not id_normalizado:
            return None

        return self._buscar_um(
            {
                "fonte": fonte.value,
                "id_externo": id_normalizado,
            }
        )

    def listar_recentes(
        self,
        fonte: Fonte | None = None,
        limite: int = 100,
    ) -> list[AnuncioVaga]:
        """Lista os anúncios com observações mais recentes."""

        if limite < 1 or limite > 10000:
            raise ValueError("limite deve estar entre 1 e 10000")

        # Um dicionário vazio seleciona todas as fontes.
        filtro: dict[str, object] = {}

        if fonte is not None:
            filtro["fonte"] = fonte.value

        try:
            cursor = (
                self._colecao.find(filtro)
                .sort(
                    "ultima_observacao_em",
                    DESCENDING,
                )
                .limit(limite)
            )

            return [
                documento_para_modelo(
                    documento,
                    AnuncioVaga,
                )
                for documento in cursor
            ]

        except PyMongoError as erro_original:
            raise ErroRepositorioAnunciosMongoDB(
                "não foi possível listar os anúncios recentes"
            ) from erro_original

    def listar_por_alvo(
        self,
        alvo_id: str,
        limite: int = 100,
    ) -> list[AnuncioVaga]:
        """Lista anúncios pertencentes a um alvo do catálogo."""

        alvo_normalizado = alvo_id.strip()

        if not alvo_normalizado:
            raise ValueError("alvo_id não pode ser vazio")

        if limite < 1 or limite > 10000:
            raise ValueError("limite deve estar entre 1 e 10000")

        try:
            cursor = (
                self._colecao.find(
                    {
                        "alvo_id": alvo_normalizado,
                    }
                )
                .sort(
                    "ultima_observacao_em",
                    DESCENDING,
                )
                .limit(limite)
            )

            return [
                documento_para_modelo(
                    documento,
                    AnuncioVaga,
                )
                for documento in cursor
            ]

        except PyMongoError as erro_original:
            raise ErroRepositorioAnunciosMongoDB(
                "não foi possível listar os anúncios do alvo"
            ) from erro_original

    def listar_por_empresa(
        self,
        empresa_id: UUID,
        limite: int = 100,
    ) -> list[AnuncioVaga]:
        """Lista os anúncios relacionados a uma empresa."""

        # Um limite evita que uma consulta acidental tente colocar
        # milhões de anúncios na memória do programa.
        if limite < 1 or limite > 1000:
            raise ValueError("limite deve estar entre 1 e 1000")

        try:
            # find cria a consulta.
            #
            # sort coloca a observação mais recente primeiro.
            #
            # limit impede carregar documentos demais.
            cursor = (
                self._colecao.find({"empresa_id": empresa_id})
                .sort(
                    "ultima_observacao_em",
                    DESCENDING,
                )
                .limit(limite)
            )

            # Cada documento volta a passar pelas validações
            # do modelo AnuncioVaga.
            return [
                documento_para_modelo(
                    documento,
                    AnuncioVaga,
                )
                for documento in cursor
            ]

        except PyMongoError as erro_original:
            raise ErroRepositorioAnunciosMongoDB(
                "não foi possível listar os anúncios da empresa"
            ) from erro_original

    def _buscar_um(
        self,
        filtro: dict[str, object],
    ) -> AnuncioVaga | None:
        """Executa uma busca e reconstrói o modelo do anúncio."""

        try:
            documento = self._colecao.find_one(filtro)

        except PyMongoError as erro_original:
            raise ErroRepositorioAnunciosMongoDB(
                "não foi possível consultar anúncios no MongoDB"
            ) from erro_original

        # None significa que nenhum anúncio foi encontrado.
        if documento is None:
            return None

        return documento_para_modelo(
            documento,
            AnuncioVaga,
        )
