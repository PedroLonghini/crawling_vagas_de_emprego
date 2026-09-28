"""Implementação MongoDB do repositório de empresas."""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit
from uuid import UUID

# ReplaceOne substitui um documento inteiro.
#
# Com upsert=True:
# - se a empresa existe, ela é atualizada;
# - se não existe, ela é inserida.
from pymongo import ReplaceOne

# Collection representa uma coleção MongoDB.
from pymongo.collection import Collection

# Database representa o banco selecionado.
from pymongo.database import Database

# Erros específicos do PyMongo.
from pymongo.errors import (
    BulkWriteError,
    DuplicateKeyError,
    PyMongoError,
)

# Empresa é o modelo que este repositório armazena.
from observatorio_vagas.domain.empresa import Empresa

# ResultadoEscrita informa quantos documentos foram
# inseridos, atualizados ou permaneceram iguais.
from observatorio_vagas.storage.contracts import ResultadoEscrita

# Estas funções convertem Empresa para BSON e BSON para Empresa.
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)

# Nome centralizado da coleção.
from observatorio_vagas.storage.mongodb.schema import (
    COLECAO_EMPRESAS,
)


class ErroRepositorioEmpresasMongoDB(RuntimeError):
    """Erro seguro durante uma operação com empresas."""


class ConflitoEmpresaMongoDB(ErroRepositorioEmpresasMongoDB):
    """Indica conflito de CNPJ, domínio ou identificador."""


class RepositorioEmpresasMongoDB:
    """Salva e consulta empresas na coleção `empresas`."""

    def __init__(self, banco: Database) -> None:
        """Seleciona a coleção, mas não executa nenhuma escrita."""

        # Guardamos somente a coleção necessária.
        #
        # O repositório não precisa conhecer a URI,
        # senha ou detalhes da conexão.
        self._colecao: Collection = banco[COLECAO_EMPRESAS]

    def salvar(self, empresa: Empresa) -> Empresa:
        """Insere uma empresa nova ou substitui a existente."""

        # Convertemos o modelo validado em documento BSON.
        documento = modelo_para_documento(empresa)

        try:
            # Procuramos usando _id.
            #
            # replace_one substitui todo o documento.
            # Isso também remove campos antigos que agora estão ausentes.
            self._colecao.replace_one(
                {"_id": empresa.id},
                documento,
                upsert=True,
            )

        except DuplicateKeyError as erro_original:
            # Este erro normalmente significa que já existe
            # outra empresa com o mesmo CNPJ ou domínio.
            raise ConflitoEmpresaMongoDB(
                "já existe uma empresa com o mesmo CNPJ ou domínio"
            ) from erro_original

        except PyMongoError as erro_original:
            # Não revelamos endereço ou credenciais na mensagem.
            raise ErroRepositorioEmpresasMongoDB(
                "não foi possível salvar a empresa no MongoDB"
            ) from erro_original

        # Devolvemos o mesmo modelo entregue.
        return empresa

    def salvar_lote(
        self,
        empresas: Sequence[Empresa],
    ) -> ResultadoEscrita:
        """Salva várias empresas em uma única chamada ao MongoDB."""

        # Um lote vazio não precisa acessar o banco.
        if not empresas:
            return ResultadoEscrita(
                recebidos=0,
                inseridos=0,
                atualizados=0,
                inalterados=0,
            )

        # Preparamos uma substituição para cada empresa.
        operacoes: list[ReplaceOne] = []

        for empresa in empresas:
            documento = modelo_para_documento(empresa)

            operacoes.append(
                ReplaceOne(
                    {"_id": empresa.id},
                    documento,
                    upsert=True,
                )
            )

        try:
            # ordered=False permite que o MongoDB processe
            # outras operações mesmo que uma delas encontre erro.
            resultado = self._colecao.bulk_write(
                operacoes,
                ordered=False,
            )

        except BulkWriteError as erro_original:
            # Um lote pode encontrar CNPJ ou domínio repetido.
            raise ConflitoEmpresaMongoDB(
                "o lote possui empresas com CNPJ, domínio ou identificador conflitante"
            ) from erro_original

        except PyMongoError as erro_original:
            raise ErroRepositorioEmpresasMongoDB(
                "não foi possível salvar o lote de empresas"
            ) from erro_original

        # upserted_count informa quantos documentos eram novos.
        inseridos = resultado.upserted_count

        # modified_count informa quantos documentos existentes mudaram.
        atualizados = resultado.modified_count

        # O restante já existia com o mesmo conteúdo.
        inalterados = len(empresas) - inseridos - atualizados

        return ResultadoEscrita(
            recebidos=len(empresas),
            inseridos=inseridos,
            atualizados=atualizados,
            inalterados=inalterados,
        )

    def buscar_por_id(
        self,
        empresa_id: UUID,
    ) -> Empresa | None:
        """Procura uma empresa pelo UUID interno."""

        return self._buscar_um(
            {"_id": empresa_id},
        )

    def buscar_por_cnpj(
        self,
        cnpj: str,
    ) -> Empresa | None:
        """Procura uma empresa pelo CNPJ normalizado."""

        # Removemos pontos, barras, traços e espaços.
        #
        # Também preservamos letras porque o modelo aceita
        # o novo formato alfanumérico de CNPJ.
        cnpj_normalizado = re.sub(
            r"[^A-Za-z0-9]",
            "",
            cnpj,
        ).upper()

        # Uma busca vazia não deve consultar o banco.
        if not cnpj_normalizado:
            return None

        return self._buscar_um(
            {"cnpj": cnpj_normalizado},
        )

    def buscar_por_dominio(
        self,
        dominio: str,
    ) -> Empresa | None:
        """Procura uma empresa pelo domínio de internet."""

        texto = dominio.strip().lower()

        if not texto:
            return None

        # Aceitamos domínio com ou sem https://.
        endereco = texto if "://" in texto else f"https://{texto}"

        hostname = urlsplit(endereco).hostname

        if hostname is None:
            return None

        # www.exemplo.com e exemplo.com representam
        # a mesma identidade empresarial.
        dominio_normalizado = hostname.removeprefix("www.")

        return self._buscar_um(
            {"dominio": dominio_normalizado},
        )

    def _buscar_um(
        self,
        filtro: dict[str, object],
    ) -> Empresa | None:
        """Executa uma busca e reconstrói o modelo Empresa."""

        try:
            documento = self._colecao.find_one(filtro)

        except PyMongoError as erro_original:
            raise ErroRepositorioEmpresasMongoDB(
                "não foi possível consultar empresas no MongoDB"
            ) from erro_original

        # None significa que nada foi encontrado.
        if documento is None:
            return None

        # Todos os dados passam novamente pelas validações
        # do modelo Empresa.
        return documento_para_modelo(
            documento,
            Empresa,
        )
