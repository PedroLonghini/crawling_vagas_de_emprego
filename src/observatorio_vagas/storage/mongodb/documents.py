"""Conversão entre modelos de domínio e documentos BSON do MongoDB."""

from __future__ import annotations

# Mapping representa objetos parecidos com dicionários.
from collections.abc import Mapping

# O MongoDB armazena data e hora, mas não possui um tipo separado
# somente para data.
from datetime import UTC, date, datetime, time

# Decimal é utilizado nos salários para evitar erros de arredondamento.
from decimal import Decimal

# Enum é a classe base das enumerações como Fonte e StatusAnuncio.
from enum import Enum

# Any permite converter estruturas que podem conter tipos diferentes.
#
# TypeVar permite informar que a função devolverá o mesmo tipo
# de modelo solicitado por quem chamou.
from typing import Any, TypeVar

# UUID é utilizado nos identificadores internos do projeto.
from uuid import UUID

# Decimal128 é o formato decimal nativo do BSON.
from bson.decimal128 import Decimal128

# AnyUrl é a classe base das URLs validadas pelo Pydantic.
from pydantic import AnyUrl

# Todos os nossos modelos principais herdam de ModeloDominio.
from observatorio_vagas.domain.common import ModeloDominio

# ModeloT representa qualquer classe que herde de ModeloDominio.
ModeloT = TypeVar(
    "ModeloT",
    bound=ModeloDominio,
)


def _converter_para_bson(valor: Any) -> Any:
    """Converte recursivamente um valor Python para BSON."""

    # Se recebermos outro modelo dentro do modelo principal,
    # primeiro transformamos esse modelo em dicionário.
    if isinstance(valor, ModeloDominio):
        return _converter_para_bson(
            valor.model_dump(
                mode="python",
                exclude_none=True,
            )
        )

    # Enumerações devem ser armazenadas pelo seu valor textual.
    #
    # Exemplo:
    # Fonte.EMPREGOS vira "empregos".
    if isinstance(valor, Enum):
        return valor.value

    # URLs do Pydantic precisam virar texto.
    #
    # O PyMongo não sabe armazenar diretamente um HttpUrl.
    if isinstance(valor, AnyUrl):
        return str(valor)

    # Decimal não pode ser transformado em float.
    #
    # float poderia introduzir diferenças como:
    # 0.1 virar 0.10000000000000001.
    if isinstance(valor, Decimal):
        return Decimal128(valor)

    # datetime já é um tipo aceito pelo MongoDB.
    #
    # Exigimos fuso horário porque uma data sem fuso
    # pode representar horários diferentes em servidores diferentes.
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            raise ValueError("datetime sem fuso horário não pode ser armazenado")

        # Internamente armazenamos todas as datas em UTC.
        return valor.astimezone(UTC)

    # O MongoDB não possui um tipo exclusivo para date.
    #
    # Por isso uma data como 20/08/2026 vira:
    # 20/08/2026 às 00:00:00 em UTC.
    if isinstance(valor, date):
        return datetime.combine(
            valor,
            time.min,
            tzinfo=UTC,
        )

    # UUID pode ser enviado diretamente.
    #
    # Nossa conexão já utiliza uuidRepresentation="standard".
    if isinstance(valor, UUID):
        return valor

    # Dicionários precisam ter chaves textuais.
    if isinstance(valor, Mapping):
        documento: dict[str, Any] = {}

        for chave, item in valor.items():
            if not isinstance(chave, str):
                raise TypeError("documentos MongoDB exigem chaves de texto")

            documento[chave] = _converter_para_bson(item)

        return documento

    # Tuplas são utilizadas nos modelos para impedir
    # alterações acidentais.
    #
    # BSON armazena sequências como listas.
    if isinstance(
        valor,
        (list, tuple, set, frozenset),
    ):
        return [_converter_para_bson(item) for item in valor]

    # Estes tipos já são aceitos diretamente pelo BSON.
    if valor is None or isinstance(
        valor,
        (str, int, float, bool, bytes),
    ):
        return valor

    # Falhar é melhor do que converter silenciosamente
    # um tipo desconhecido e perder informação.
    raise TypeError(f"tipo não suportado para armazenamento no MongoDB: {type(valor).__name__}")


def _converter_do_bson(valor: Any) -> Any:
    """Converte recursivamente valores BSON para valores Python."""

    # Decimal128 volta a ser Decimal.
    if isinstance(valor, Decimal128):
        return valor.to_decimal()

    # O PyMongo pode devolver datas sem tzinfo dependendo
    # das configurações da conexão.
    #
    # Neste caso sabemos que nossas datas são armazenadas em UTC.
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            return valor.replace(tzinfo=UTC)

        return valor.astimezone(UTC)

    # Convertemos todos os valores internos dos dicionários.
    if isinstance(valor, Mapping):
        return {chave: _converter_do_bson(item) for chave, item in valor.items()}

    # As listas também podem conter Decimal128 ou documentos.
    if isinstance(valor, list):
        return [_converter_do_bson(item) for item in valor]

    # Outros tipos, como UUID, texto e números inteiros,
    # podem ser devolvidos sem alteração.
    return valor


def modelo_para_documento(
    modelo: ModeloDominio,
) -> dict[str, Any]:
    """Transforma um modelo em documento pronto para o MongoDB."""

    # mode="python" preserva UUID, datetime e Decimal
    # para que nosso conversor escolha o formato correto.
    dados = modelo.model_dump(
        mode="python",
        exclude_none=True,
    )

    # Fazemos a conversão recursiva.
    documento = _converter_para_bson(dados)

    # Esta verificação também ajuda analisadores e futuros leitores.
    if not isinstance(documento, dict):
        raise TypeError("o modelo precisa produzir um documento")

    # O MongoDB utiliza "_id" como identificador principal.
    #
    # Nossos modelos utilizam "id", porque não devem depender
    # de uma tecnologia específica de banco.
    id_interno = documento.pop("id", None)

    if id_interno is not None:
        documento["_id"] = id_interno

    return documento


def documento_para_modelo(
    documento: Mapping[str, Any],
    tipo_modelo: type[ModeloT],
) -> ModeloT:
    """Reconstrói um modelo de domínio a partir de um documento."""

    # Criamos uma cópia para não alterar o documento
    # que foi entregue pelo PyMongo.
    dados_convertidos = _converter_do_bson(dict(documento))

    if not isinstance(dados_convertidos, dict):
        raise TypeError("o documento precisa ser um dicionário")

    # Retiramos o nome específico do MongoDB.
    id_mongo = dados_convertidos.pop("_id", None)

    # Se existir um ID do MongoDB, ele volta ao campo
    # independente chamado "id".
    if id_mongo is not None:
        id_existente = dados_convertidos.get("id")

        # Um documento não pode possuir dois IDs diferentes.
        if id_existente is not None and id_existente != id_mongo:
            raise ValueError("documento possui conflito entre id e _id")

        dados_convertidos["id"] = id_mongo

    # O Pydantic fará novamente todas as validações do modelo.
    return tipo_modelo.model_validate(dados_convertidos)
